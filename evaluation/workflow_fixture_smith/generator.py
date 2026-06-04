# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Deterministic offline generator for review-only workflow fixture candidates.

This module intentionally stops at a quarantine/review queue. It never writes to
``evaluation/e2e_mcp_workflows/tasks`` and every generated candidate carries an
explicit ``ci_enabled: false`` guard so manual promotion is required before a
candidate can become a benchmark fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SEED_SCHEMA = "workflow_fixture_smith_seed_pack.v1"
CANDIDATE_SCHEMA = "workflow_fixture_smith_candidate.v1"
REPORT_SCHEMA = "workflow_fixture_smith_report.v1"
DEFAULT_SEED_FILE = Path(__file__).resolve().parent / "seeds" / "local_seed_metadata.json"
DEFAULT_REVIEW_QUEUE = (
    ".codebase-tooling-mcp/review-queue/workflow-fixture-candidates"
)
DEFAULT_EXISTING_FIXTURES = Path(__file__).resolve().parents[1] / "e2e_mcp_workflows" / "tasks"

_ALLOWED_SOURCE_KINDS = {"checked_in_seed_metadata", "local_fixture_metadata"}
_REQUIRED_CANDIDATE_KEYS = {
    "schema",
    "id",
    "title",
    "source",
    "quarantine",
    "prompt",
    "tags",
    "tool_chain_shape",
    "verifier_requirements",
    "safety_gates",
    "expected_artifacts",
    "diversity",
}
_UNSAFE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), reason)
    for pattern, reason in (
        (r"\blive\s+github\b|\bgithub\s+issue\s+import\b", "live_github_import"),
        (r"\bhttps?://|\bnetwork\b|\bcurl\b|\bwget\b", "network_dependency"),
        (r"\bdelete\s+the\s+repo\b|\brm\s+-rf\b|\bdrop\s+database\b", "destructive_action"),
        (r"\bany\s+repository\b|\ball\s+repositories\b|\bunbounded\b", "unbounded_scope"),
        (r"\bsecrets?\b|\btokens?\b|\bprivate\s+key\b", "secret_handling"),
    )
)


class WorkflowFixtureSmithError(ValueError):
    """Raised when seed metadata or generated candidates violate the contract."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def generate_review_queue(
    *,
    seed_file: str | Path = DEFAULT_SEED_FILE,
    review_queue_dir: str | Path = DEFAULT_REVIEW_QUEUE,
    repo_root: str | Path | None = None,
    existing_fixture_dir: str | Path = DEFAULT_EXISTING_FIXTURES,
    write: bool = True,
) -> dict[str, Any]:
    """Generate deterministic review-queue candidates from local seed metadata.

    ``write=True`` persists each accepted candidate under the quarantine queue and
    writes a compact report next to them. ``write=False`` returns the same report
    without touching the filesystem, which is useful for tests and review.
    """

    root = Path(repo_root).resolve() if repo_root is not None else repository_root()
    seed_path = _resolve_repo_path(seed_file, root)
    seeds = load_seed_pack(seed_path)
    existing_signatures = _load_existing_fixture_signatures(
        _resolve_repo_path(existing_fixture_dir, root)
    )

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for seed in seeds["seeds"]:
        seed_rejections = _seed_rejection_reasons(seed)
        if seed_rejections:
            rejected.append(_rejection(seed, None, seed_rejections))
            continue
        for variant in seed.get("variants", []):
            reasons = _variant_rejection_reasons(seed, variant)
            if reasons:
                rejected.append(_rejection(seed, variant, reasons))
                continue
            candidate = _candidate_from_seed(seed, variant, existing_signatures)
            validate_candidate(candidate)
            accepted.append(candidate)

    accepted.sort(key=lambda item: item["id"])
    rejected.sort(key=lambda item: item["id"])
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_by": "evaluation.workflow_fixture_smith.generator",
        "source_seed_file": _repo_relative(seed_path, root),
        "quarantine_only": True,
        "ci_enabled": False,
        "review_queue_dir": _repo_relative(_resolve_repo_path(review_queue_dir, root), root),
        "existing_fixture_dir": _repo_relative(
            _resolve_repo_path(existing_fixture_dir, root), root
        ),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "candidate_ids": [candidate["id"] for candidate in accepted],
        "rejections": rejected,
        "candidates": accepted,
    }

    if write:
        output_dir = _resolve_repo_path(review_queue_dir, root)
        _write_review_queue(report, output_dir, root)
    return report


def load_seed_pack(path: str | Path = DEFAULT_SEED_FILE) -> dict[str, Any]:
    """Load and validate the local seed metadata pack."""

    seed_pack = _load_json(Path(path))
    if seed_pack.get("schema") != SEED_SCHEMA:
        raise WorkflowFixtureSmithError(
            f"unsupported seed schema {seed_pack.get('schema')!r}"
        )
    seeds = seed_pack.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        raise WorkflowFixtureSmithError("seed pack must contain a non-empty seeds list")
    seen: set[str] = set()
    for seed in seeds:
        _validate_seed(seed)
        seed_id = str(seed["id"])
        if seed_id in seen:
            raise WorkflowFixtureSmithError(f"duplicate seed id {seed_id!r}")
        seen.add(seed_id)
    return seed_pack


def validate_candidate(candidate: Mapping[str, Any]) -> None:
    """Validate the quarantine candidate schema and safety gates."""

    missing = sorted(_REQUIRED_CANDIDATE_KEYS.difference(candidate))
    if missing:
        raise WorkflowFixtureSmithError(f"candidate missing required fields: {missing}")
    if candidate.get("schema") != CANDIDATE_SCHEMA:
        raise WorkflowFixtureSmithError("candidate schema mismatch")
    if not _safe_id(str(candidate.get("id", ""))):
        raise WorkflowFixtureSmithError("candidate id must be lowercase slug text")
    source = candidate["source"]
    if not isinstance(source, Mapping):
        raise WorkflowFixtureSmithError("candidate.source must be an object")
    if source.get("kind") not in _ALLOWED_SOURCE_KINDS:
        raise WorkflowFixtureSmithError("candidate source must be checked-in/local metadata")
    if source.get("live_github_import") is not False:
        raise WorkflowFixtureSmithError("candidate must disable live GitHub import")
    quarantine = candidate["quarantine"]
    if not isinstance(quarantine, Mapping):
        raise WorkflowFixtureSmithError("candidate.quarantine must be an object")
    if quarantine.get("review_queue") is not True or quarantine.get("ci_enabled") is not False:
        raise WorkflowFixtureSmithError("candidate must remain review-queue only and CI-disabled")
    verifier = candidate["verifier_requirements"]
    if not isinstance(verifier, Mapping):
        raise WorkflowFixtureSmithError("candidate.verifier_requirements must be an object")
    if verifier.get("deterministic") is not True:
        raise WorkflowFixtureSmithError("candidate verifier must be deterministic")
    for key in ("required_reports", "expected_file_touches", "deterministic_checks"):
        if not isinstance(verifier.get(key), list) or not verifier[key]:
            raise WorkflowFixtureSmithError(f"candidate verifier.{key} must be non-empty")
    safety = candidate["safety_gates"]
    if not isinstance(safety, Mapping):
        raise WorkflowFixtureSmithError("candidate.safety_gates must be an object")
    if safety.get("network") is not False or safety.get("live_github_import") is not False:
        raise WorkflowFixtureSmithError("candidate must disable network and live imports")
    if safety.get("unbounded_scope_allowed") is not False:
        raise WorkflowFixtureSmithError("candidate must reject unbounded scope")
    if not isinstance(safety.get("max_files_changed"), int) or safety["max_files_changed"] < 0:
        raise WorkflowFixtureSmithError("candidate max_files_changed must be a non-negative int")
    if not isinstance(candidate.get("expected_artifacts"), list) or not candidate["expected_artifacts"]:
        raise WorkflowFixtureSmithError("candidate expected_artifacts must be non-empty")
    diversity = candidate["diversity"]
    if not isinstance(diversity, Mapping):
        raise WorkflowFixtureSmithError("candidate.diversity must be an object")
    if not isinstance(diversity.get("reasons"), list) or not diversity["reasons"]:
        raise WorkflowFixtureSmithError("candidate diversity reasons must be non-empty")
    score = diversity.get("score")
    if not isinstance(score, int) or score < 0:
        raise WorkflowFixtureSmithError("candidate diversity score must be a non-negative int")
    unsafe_reasons = _text_rejection_reasons(_candidate_text(candidate))
    if unsafe_reasons:
        raise WorkflowFixtureSmithError(
            "candidate contains unsafe/unbounded text: " + ", ".join(unsafe_reasons)
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate offline workflow fixture candidates into a quarantine review queue."
    )
    parser.add_argument("--seed-file", default=str(DEFAULT_SEED_FILE))
    parser.add_argument("--review-queue-dir", default=DEFAULT_REVIEW_QUEUE)
    parser.add_argument("--existing-fixture-dir", default=str(DEFAULT_EXISTING_FIXTURES))
    parser.add_argument("--dry-run", action="store_true", help="print report without writing candidates")
    parser.add_argument("--compact", action="store_true", help="print a compact JSON report")
    args = parser.parse_args(argv)

    report = generate_review_queue(
        seed_file=args.seed_file,
        review_queue_dir=args.review_queue_dir,
        existing_fixture_dir=args.existing_fixture_dir,
        write=not args.dry_run,
    )
    if args.compact:
        printable = {
            key: report[key]
            for key in (
                "schema",
                "quarantine_only",
                "ci_enabled",
                "accepted_count",
                "rejected_count",
                "candidate_ids",
                "review_queue_dir",
            )
        }
        print(json.dumps(printable, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _candidate_from_seed(
    seed: Mapping[str, Any],
    variant: Mapping[str, Any],
    existing_signatures: Mapping[str, set[str]],
) -> dict[str, Any]:
    candidate_id = f"smith-{seed['id']}-{variant['id']}"
    tags = sorted(set(seed["tags"]) | set(variant.get("tags", [])))
    tool_chain_shape = sorted(set(seed["tool_chain_shape"]) | set(variant.get("tool_chain_shape", [])))
    expected_artifacts = sorted(
        set(seed["expected_artifacts"]) | set(variant.get("expected_artifacts", []))
    )
    verifier_seed = seed["verifier_requirements"]
    verifier = {
        "deterministic": True,
        "required_reports": sorted(
            set(verifier_seed["required_reports"])
            | set(variant.get("required_reports", []))
        ),
        "expected_file_touches": sorted(
            set(verifier_seed["expected_file_touches"])
            | set(variant.get("expected_file_touches", []))
        ),
        "deterministic_checks": sorted(
            set(verifier_seed["deterministic_checks"])
            | set(variant.get("deterministic_checks", []))
        ),
    }
    safety_seed = seed["safety_gates"]
    safety = {
        "network": False,
        "live_github_import": False,
        "unbounded_scope_allowed": False,
        "allowed_mutations": sorted(safety_seed["allowed_mutations"]),
        "max_files_changed": int(safety_seed["max_files_changed"]),
        "required_review": True,
        "manual_promotion_required": True,
    }
    diversity = _score_diversity(
        tags=tags,
        tool_chain_shape=tool_chain_shape,
        expected_artifacts=expected_artifacts,
        existing_signatures=existing_signatures,
        variant_reason=str(variant["diversity_reason"]),
    )
    candidate = {
        "schema": CANDIDATE_SCHEMA,
        "id": candidate_id,
        "title": f"{seed['title']} - {variant['title']}",
        "source": {
            "kind": seed["source_kind"],
            "seed_id": seed["id"],
            "variant_id": variant["id"],
            "live_github_import": False,
            "local_metadata_only": True,
        },
        "quarantine": {
            "review_queue": True,
            "ci_enabled": False,
            "promotion_required": "manual_review_and_copy_to_benchmark_fixture",
        },
        "prompt": " ".join([str(seed["prompt"]).strip(), str(variant["prompt_delta"]).strip()]),
        "tags": tags,
        "tool_chain_shape": tool_chain_shape,
        "verifier_requirements": verifier,
        "safety_gates": safety,
        "expected_artifacts": expected_artifacts,
        "diversity": diversity,
    }
    candidate["deterministic_id"] = _digest_candidate(candidate)
    return candidate


def _score_diversity(
    *,
    tags: Sequence[str],
    tool_chain_shape: Sequence[str],
    expected_artifacts: Sequence[str],
    existing_signatures: Mapping[str, set[str]],
    variant_reason: str,
) -> dict[str, Any]:
    reasons = [variant_reason]
    score = 0
    new_tags = sorted(set(tags).difference(existing_signatures["tags"]))
    if new_tags:
        score += len(new_tags)
        reasons.append("adds underrepresented tags: " + ", ".join(new_tags))
    new_tools = sorted(set(tool_chain_shape).difference(existing_signatures["tools"]))
    if new_tools:
        score += len(new_tools)
        reasons.append("adds uncommon tool-chain steps: " + ", ".join(new_tools))
    new_artifacts = sorted(set(_artifact_kinds(expected_artifacts)).difference(existing_signatures["artifact_kinds"]))
    if new_artifacts:
        score += len(new_artifacts)
        reasons.append("requires artifact kinds absent from current fixtures: " + ", ".join(new_artifacts))
    if len(tool_chain_shape) >= 4:
        score += 1
        reasons.append("combines multiple workflow gates in one bounded task")
    return {"score": score, "reasons": reasons}


def _load_existing_fixture_signatures(path: Path) -> dict[str, set[str]]:
    signatures = {"tags": set(), "tools": set(), "artifact_kinds": set()}
    if not path.is_dir():
        return signatures
    for fixture_path in sorted(path.glob("*.json")):
        try:
            fixture = _load_json(fixture_path)
        except WorkflowFixtureSmithError:
            continue
        signatures["tags"].update(str(tag) for tag in fixture.get("tags") or [])
        allowed = fixture.get("allowed")
        if isinstance(allowed, Mapping):
            signatures["tools"].update(str(tool) for tool in allowed.get("tools") or [])
        verification = fixture.get("verification")
        if isinstance(verification, Mapping):
            signatures["artifact_kinds"].update(
                _artifact_kinds(verification.get("expected_artifacts") or [])
            )
    return signatures


def _artifact_kinds(paths: Sequence[Any]) -> set[str]:
    kinds: set[str] = set()
    for path in paths:
        suffix = Path(str(path)).suffix.lower().lstrip(".")
        kinds.add(suffix or "path")
    return kinds


def _write_review_queue(report: Mapping[str, Any], output_dir: Path, root: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for candidate in report["candidates"]:
        _write_json(output_dir / f"{candidate['id']}.json", candidate)
    report_for_disk = dict(report)
    report_for_disk["candidates"] = [
        {"id": candidate["id"], "path": f"{candidate['id']}.json"}
        for candidate in report["candidates"]
    ]
    _write_json(output_dir / "WORKFLOW_FIXTURE_SMITH_REPORT.json", report_for_disk)


def _validate_seed(seed: Any) -> None:
    if not isinstance(seed, Mapping):
        raise WorkflowFixtureSmithError("each seed must be an object")
    for key in (
        "id",
        "title",
        "source_kind",
        "prompt",
        "tags",
        "tool_chain_shape",
        "expected_artifacts",
        "verifier_requirements",
        "safety_gates",
        "variants",
    ):
        if key not in seed:
            raise WorkflowFixtureSmithError(f"seed missing required field {key!r}")
    if not _safe_id(str(seed["id"])):
        raise WorkflowFixtureSmithError(f"unsafe seed id {seed['id']!r}")
    if seed["source_kind"] not in _ALLOWED_SOURCE_KINDS:
        raise WorkflowFixtureSmithError("seed source_kind must be checked-in/local metadata")
    for list_key in ("tags", "tool_chain_shape", "expected_artifacts"):
        if not isinstance(seed[list_key], list) or not seed[list_key]:
            raise WorkflowFixtureSmithError(f"seed {list_key} must be a non-empty list")
    verifier = seed["verifier_requirements"]
    if not isinstance(verifier, Mapping):
        raise WorkflowFixtureSmithError("seed verifier_requirements must be an object")
    if verifier.get("deterministic") is not True:
        raise WorkflowFixtureSmithError("seed verifier must be deterministic")
    for key in ("required_reports", "expected_file_touches", "deterministic_checks"):
        if not isinstance(verifier.get(key), list) or not verifier[key]:
            raise WorkflowFixtureSmithError(f"seed verifier.{key} must be non-empty")
    safety = seed["safety_gates"]
    if not isinstance(safety, Mapping):
        raise WorkflowFixtureSmithError("seed safety_gates must be an object")
    if safety.get("network") is not False or safety.get("live_github_import") is not False:
        raise WorkflowFixtureSmithError("seed must disable network and live GitHub imports")
    if safety.get("unbounded_scope_allowed") is not False:
        raise WorkflowFixtureSmithError("seed must reject unbounded scope")
    if not isinstance(safety.get("allowed_mutations"), list):
        raise WorkflowFixtureSmithError("seed safety.allowed_mutations must be a list")
    if not isinstance(safety.get("max_files_changed"), int):
        raise WorkflowFixtureSmithError("seed safety.max_files_changed must be an int")
    variants = seed["variants"]
    if not isinstance(variants, list) or not variants:
        raise WorkflowFixtureSmithError("seed variants must be a non-empty list")
    seen: set[str] = set()
    for variant in variants:
        _validate_variant(variant)
        variant_id = str(variant["id"])
        if variant_id in seen:
            raise WorkflowFixtureSmithError(f"duplicate variant id {variant_id!r}")
        seen.add(variant_id)


def _validate_variant(variant: Any) -> None:
    if not isinstance(variant, Mapping):
        raise WorkflowFixtureSmithError("each variant must be an object")
    for key in ("id", "title", "prompt_delta", "diversity_reason"):
        if key not in variant:
            raise WorkflowFixtureSmithError(f"variant missing required field {key!r}")
    if not _safe_id(str(variant["id"])):
        raise WorkflowFixtureSmithError(f"unsafe variant id {variant['id']!r}")
    for list_key in (
        "tags",
        "tool_chain_shape",
        "expected_artifacts",
        "required_reports",
        "expected_file_touches",
        "deterministic_checks",
    ):
        if list_key in variant and not isinstance(variant[list_key], list):
            raise WorkflowFixtureSmithError(f"variant {list_key} must be a list")


def _seed_rejection_reasons(seed: Mapping[str, Any]) -> list[str]:
    reasons = _text_rejection_reasons(
        " ".join(
            str(seed.get(key, ""))
            for key in ("id", "title", "source_kind", "prompt")
        )
    )
    safety = seed.get("safety_gates", {})
    if safety.get("network") is not False:
        reasons.append("network_not_disabled")
    if safety.get("live_github_import") is not False:
        reasons.append("live_github_import_not_disabled")
    if safety.get("unbounded_scope_allowed") is not False:
        reasons.append("unbounded_scope_allowed")
    if seed.get("source_kind") not in _ALLOWED_SOURCE_KINDS:
        reasons.append("non_local_source_kind")
    return sorted(set(reasons))


def _variant_rejection_reasons(seed: Mapping[str, Any], variant: Mapping[str, Any]) -> list[str]:
    text = " ".join(
        str(variant.get(key, ""))
        for key in ("id", "title", "prompt_delta", "diversity_reason")
    )
    return sorted(set(_text_rejection_reasons(str(seed.get("prompt", "")) + " " + text)))


def _text_rejection_reasons(text: str) -> list[str]:
    return sorted({reason for pattern, reason in _UNSAFE_PATTERNS if pattern.search(text)})


def _rejection(
    seed: Mapping[str, Any], variant: Mapping[str, Any] | None, reasons: Sequence[str]
) -> dict[str, Any]:
    variant_id = str(variant["id"]) if variant is not None else "seed"
    return {
        "id": f"{seed.get('id', 'unknown')}-{variant_id}",
        "seed_id": seed.get("id"),
        "variant_id": None if variant is None else variant.get("id"),
        "reasons": sorted(set(reasons)),
    }


def _candidate_text(candidate: Mapping[str, Any]) -> str:
    searchable: list[str] = []
    for key in ("id", "title", "prompt"):
        searchable.append(str(candidate.get(key, "")))
    searchable.extend(str(tag) for tag in candidate.get("tags", []))
    searchable.extend(str(path) for path in candidate.get("expected_artifacts", []))
    searchable.extend(candidate.get("diversity", {}).get("reasons", []))
    return " ".join(searchable)


def _safe_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{2,80}", value))


def _digest_candidate(candidate: Mapping[str, Any]) -> str:
    payload = dict(candidate)
    payload.pop("deterministic_id", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise WorkflowFixtureSmithError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowFixtureSmithError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowFixtureSmithError(f"JSON file must contain an object: {path}")
    return data


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _resolve_repo_path(path: str | Path, root: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def _repo_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
