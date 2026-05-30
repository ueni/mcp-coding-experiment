# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Offline review-comment resolution outcome reporting.

The report consumes redacted local fixtures that describe automated review
comments, follow-up commits, and structured human decisions. It never calls the
network, shells out, or requires raw review comments/patches at runtime.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_SCHEMA = "review_outcome_report.v1"
FIXTURE_SCHEMA = "review_outcome_fixture.v1"
MANIFEST_SCHEMA = "review_outcome_manifest.v1"
DEFAULT_FIXTURE_DIR = Path("tests/fixtures/review_outcomes")
REPORT_PREFIX = "review-outcome-report"

OUTCOME_RESOLVED = "resolved_by_change"
OUTCOME_ACCEPTED = "accepted_no_change"
OUTCOME_DISMISSED = "dismissed"
OUTCOME_STALE = "stale"
OUTCOME_OPEN = "open"
OUTCOME_UNVERIFIABLE = "unverifiable"
OUTCOME_REGRESSED = "regressed"

# Backward-compatible internal aliases for common source-system labels. Public
# reports use the issue vocabulary: dismissed/noisy and stale.
OUTCOME_REJECTED = OUTCOME_DISMISSED
OUTCOME_SUPERSEDED = OUTCOME_STALE

STABLE_OUTCOMES = (
    OUTCOME_RESOLVED,
    OUTCOME_ACCEPTED,
    OUTCOME_DISMISSED,
    OUTCOME_STALE,
    OUTCOME_OPEN,
    OUTCOME_UNVERIFIABLE,
    OUTCOME_REGRESSED,
)

_RESOLVED_LABELS = {"resolved", "fixed", "addressed", "resolved_by_change", "changed"}
_ACCEPTED_LABELS = {"accepted", "accepted_no_change", "intentional", "documented_no_change"}
_REJECTED_LABELS = {
    "rejected",
    "dismissed",
    "false_positive",
    "noise",
    "noisy",
    "wontfix",
    "not_actionable",
}
_SUPERSEDED_LABELS = {"superseded", "stale", "obsolete", "file_moved", "replaced"}
_OPEN_LABELS = {"open", "unresolved", "left_open", "pending", "todo"}
_REGRESSED_LABELS = {"regressed", "reopened", "failed_again"}

_RESOLVE_KEYS = ("resolves", "resolved", "resolved_findings", "fixed_findings")
_ACCEPT_KEYS = ("accepts", "accepted", "accepted_findings")
_REJECT_KEYS = ("rejects", "rejected", "dismissed", "rejected_findings", "dismissed_findings")
_SUPERSEDE_KEYS = (
    "supersedes",
    "superseded",
    "superseded_findings",
    "stale_findings",
    "replaced_findings",
)
_OPEN_KEYS = ("leaves_open", "left_open", "open_findings", "unresolved_findings")
_REGRESS_KEYS = ("regresses", "regressed", "reopened_findings")


@dataclass(frozen=True)
class FindingOutcome:
    """Redacted per-finding outcome row."""

    finding_ref: str
    rule_id: str
    path: str
    line: int | None
    comment_digest: str
    outcome: str
    outcome_group: str
    reason: str
    evidence: dict[str, Any]
    created_at: str
    resolved_at: str
    time_to_resolution_hours: float | None

    def public_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "finding_ref": self.finding_ref,
            "rule_id": self.rule_id,
            "path": self.path,
            "comment_digest": self.comment_digest,
            "outcome": self.outcome,
            "outcome_group": self.outcome_group,
            "reason": self.reason,
            "evidence": self.evidence,
        }
        if self.line is not None:
            row["line"] = self.line
        if self.created_at:
            row["created_at"] = self.created_at
        if self.resolved_at:
            row["resolved_at"] = self.resolved_at
        if self.time_to_resolution_hours is not None:
            row["time_to_resolution_hours"] = self.time_to_resolution_hours
        return row


def repository_root() -> Path:
    """Return this repository root from the checked-in source module path."""

    return Path(__file__).resolve().parents[1]


def generate_review_outcome_report(
    fixture_dir: str | Path = DEFAULT_FIXTURE_DIR,
    *,
    repo_root: str | Path | None = None,
    export: bool = False,
    export_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build a deterministic, offline review-comment outcome report.

    The fixture pack is local JSON only. Raw comment bodies and raw patch text are
    intentionally ignored; callers should provide stable IDs, rule IDs, redacted
    repository-relative paths, and optional SHA-256 digests instead.
    """

    root = Path(repo_root).resolve() if repo_root is not None else repository_root()
    fixture_root = _resolve_path(fixture_dir, root).resolve()
    manifest = _load_manifest(fixture_root)

    fixture_reports: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    evidence_paths: list[str] = []
    source_counts: Counter[str] = Counter()

    for fixture_path in _fixture_paths(fixture_root, manifest):
        fixture = _load_fixture(fixture_path)
        fixture_report = _evaluate_fixture(fixture, fixture_path, fixture_root, root)
        fixture_reports.append(fixture_report)
        findings.extend(fixture_report["findings"])
        evidence_paths.extend(fixture_report["evidence_paths"])
        source_counts.update(fixture_report["source_counts"])

    outcome_counts = {outcome: 0 for outcome in STABLE_OUTCOMES}
    group_counts: Counter[str] = Counter()
    for finding in findings:
        outcome = str(finding.get("outcome") or OUTCOME_OPEN)
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        group_counts[str(finding.get("outcome_group") or "open")] += 1

    total = len(findings)
    evidence_covered = sum(
        1
        for finding in findings
        if bool(finding.get("evidence", {}).get("has_location_or_digest"))
    )
    line_covered = sum(1 for finding in findings if bool(finding.get("evidence", {}).get("has_line")))
    resolution_hours = [
        float(finding["time_to_resolution_hours"])
        for finding in findings
        if isinstance(finding.get("time_to_resolution_hours"), (int, float))
    ]

    gate_correlations = _gate_correlations(fixture_reports)
    dismissed_rate = _ratio(group_counts.get("dismissed", 0), total)

    generated_at = _now_iso()
    report_seed = json.dumps(
        {
            "fixture_root": _display_path(fixture_root, root),
            "fixtures": [fixture["id"] for fixture in fixture_reports],
            "findings": findings,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    report_id = f"{REPORT_PREFIX}-{_now_stamp()}-{hashlib.sha256(report_seed.encode('utf-8')).hexdigest()[:12]}"
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "report_id": report_id,
        "generated_at": generated_at,
        "ok": True,
        "read_only": True,
        "network_used": False,
        "fixture_root": _display_path(fixture_root, root),
        "summary": {
            "fixtures": len(fixture_reports),
            "findings": total,
            "outcome_counts": outcome_counts,
            "outcome_group_counts": dict(sorted(group_counts.items())),
            "resolved_rate": _ratio(group_counts.get("resolved", 0), total),
            "dismissed_noise_rate": dismissed_rate,
            "dismissed_rate": dismissed_rate,
            "open_rate": _ratio(group_counts.get("open", 0), total),
            "evidence_coverage": _ratio(evidence_covered, total),
            "line_evidence_coverage": _ratio(line_covered, total),
            "median_time_to_resolution_hours": _median(resolution_hours),
        },
        "findings": findings,
        "fixtures": fixture_reports,
        "gate_correlations": gate_correlations,
        "self_optimization_inputs": {
            "schema": "review_outcome_self_optimization_inputs.v1",
            "resolution_metrics": {
                "findings": total,
                "resolved_rate": _ratio(group_counts.get("resolved", 0), total),
                "dismissed_noise_rate": dismissed_rate,
                "evidence_coverage": _ratio(evidence_covered, total),
                "median_time_to_resolution_hours": _median(resolution_hours),
            },
            "gate_correlations": gate_correlations,
        },
        "aggregations": {
            "by_source": [
                {"name": source, "count": count}
                for source, count in sorted(source_counts.items())
            ],
        },
        "classification_policy": {
            "stable_outcomes": list(STABLE_OUTCOMES),
            "outcome_groups": {
                OUTCOME_RESOLVED: "resolved",
                OUTCOME_ACCEPTED: "resolved",
                OUTCOME_DISMISSED: "dismissed",
                OUTCOME_STALE: "stale",
                OUTCOME_OPEN: "open",
                OUTCOME_UNVERIFIABLE: "unverifiable",
                OUTCOME_REGRESSED: "open",
            },
            "dismissed_noise_labels": sorted(_REJECTED_LABELS),
            "stale_labels": sorted(_SUPERSEDED_LABELS),
        },
        "privacy": {
            "raw_review_comments_persisted": False,
            "raw_patch_text_persisted": False,
            "network_used": False,
            "reference_policy": "repository-relative paths plus stable finding refs and caller-provided digests only",
        },
        "evidence_paths": _unique(evidence_paths),
    }
    if export:
        report["exports"] = _write_exports(report, root, export_dir)
    return report


def _evaluate_fixture(
    fixture: Mapping[str, Any],
    fixture_path: Path,
    fixture_root: Path,
    repo_root: Path,
) -> dict[str, Any]:
    fixture_id = str(fixture.get("id") or fixture_path.parent.name)
    fixture_dir = fixture_path.parent
    raw_findings = _as_list(fixture.get("findings") or fixture.get("review_comments"))
    followups = list(_as_list(fixture.get("follow_up_commits") or fixture.get("followups")))
    human_reviews = list(_as_list(fixture.get("human_reviews") or fixture.get("human_decisions")))
    findings = [
        _classify_finding(
            finding,
            index=index,
            fixture_id=fixture_id,
            fixture_dir=fixture_dir,
            followups=followups,
            human_reviews=human_reviews,
        ).public_dict()
        for index, finding in enumerate(raw_findings, start=1)
        if isinstance(finding, Mapping)
    ]
    outcome_counts = Counter(str(row.get("outcome") or OUTCOME_OPEN) for row in findings)
    source_counts = Counter(str(row.get("evidence", {}).get("source") or "fixture") for row in findings)
    evidence_paths = [
        _display_path(fixture_path, repo_root),
        *[
            _display_path(_resolve_path(path, fixture_dir), repo_root)
            for path in _as_list(fixture.get("evidence_paths"))
        ],
    ]
    return {
        "id": fixture_id,
        "description": str(fixture.get("description") or ""),
        "findings": findings,
        "summary": {
            "findings": len(findings),
            "outcome_counts": dict(sorted(outcome_counts.items())),
        },
        "gate_state": _gate_state(fixture),
        "source_counts": dict(source_counts),
        "evidence_paths": _unique(evidence_paths),
    }


def _classify_finding(
    finding: Mapping[str, Any],
    *,
    index: int,
    fixture_id: str,
    fixture_dir: Path,
    followups: Sequence[Any],
    human_reviews: Sequence[Any],
) -> FindingOutcome:
    finding_id = str(finding.get("id") or finding.get("comment_id") or f"finding-{index}")
    rule_id = str(finding.get("rule_id") or finding.get("check_id") or "")
    path = str(finding.get("path") or finding.get("file_path") or "")
    line = _line_number(finding.get("line") or finding.get("start_line"))
    digest = _safe_digest(str(finding.get("comment_digest") or finding.get("patch_digest") or ""))
    created_at = _timestamp_string(finding.get("created_at") or finding.get("timestamp"))
    refs = _finding_refs(finding_id, rule_id, digest)

    event = _first_matching_event(refs, followups, human_reviews)
    explicit = _normalize_outcome(
        finding.get("outcome")
        or finding.get("expected_outcome")
        or finding.get("resolution_state")
        or finding.get("status")
        or finding.get("decision")
    )
    event_outcome = _normalize_outcome(event.get("outcome") if event else "")
    event_source = str(event.get("source") if event else "fixture_default")
    terminal_outcome = explicit or event_outcome

    has_identity = bool(finding_id or rule_id or digest)
    has_location_or_digest = bool(path or digest)
    has_line = line is not None
    if not terminal_outcome and (not has_identity or not has_location_or_digest):
        terminal_outcome = OUTCOME_UNVERIFIABLE
        reason = "missing stable finding identity or file/digest evidence"
    elif terminal_outcome:
        reason = str(event.get("reason") if event else finding.get("reason") or "structured outcome evidence")
    else:
        terminal_outcome = OUTCOME_OPEN
        reason = "no terminal follow-up evidence"

    resolved_at = _timestamp_string(
        (event.get("timestamp") if event else "")
        or finding.get("resolved_at")
        or finding.get("closed_at")
    )
    hours = _hours_between(created_at, resolved_at) if terminal_outcome in {OUTCOME_RESOLVED, OUTCOME_ACCEPTED} else None
    commit_refs = [str(value) for value in _as_list(event.get("commit") if event else [])]
    if event and not commit_refs and event.get("sha"):
        commit_refs = [str(event.get("sha"))]

    return FindingOutcome(
        finding_ref=_safe_ref(fixture_id, finding_id),
        rule_id=rule_id,
        path=_normalize_repo_path(path, fixture_dir),
        line=line,
        comment_digest=digest,
        outcome=terminal_outcome,
        outcome_group=_outcome_group(terminal_outcome),
        reason=reason,
        evidence={
            "source": event_source,
            "has_identity": has_identity,
            "has_location_or_digest": has_location_or_digest,
            "has_line": has_line,
            "commit_refs": commit_refs[:5],
        },
        created_at=created_at,
        resolved_at=resolved_at,
        time_to_resolution_hours=hours,
    )


def _first_matching_event(
    refs: set[str], followups: Sequence[Any], human_reviews: Sequence[Any]
) -> dict[str, Any] | None:
    for event in list(followups) + list(human_reviews):
        if not isinstance(event, Mapping):
            continue
        for outcome, keys in (
            (OUTCOME_REGRESSED, _REGRESS_KEYS),
            (OUTCOME_RESOLVED, _RESOLVE_KEYS),
            (OUTCOME_ACCEPTED, _ACCEPT_KEYS),
            (OUTCOME_REJECTED, _REJECT_KEYS),
            (OUTCOME_SUPERSEDED, _SUPERSEDE_KEYS),
            (OUTCOME_OPEN, _OPEN_KEYS),
        ):
            matched = _event_matches(event, keys, refs)
            if matched:
                return {
                    "outcome": outcome,
                    "source": str(event.get("source") or event.get("type") or "follow_up"),
                    "timestamp": event.get("timestamp") or event.get("created_at") or event.get("committed_at"),
                    "commit": event.get("commit") or event.get("sha"),
                    "reason": event.get("reason") or f"matched {matched}",
                    "sha": event.get("sha"),
                }
        label_outcome = _normalize_outcome(event.get("outcome") or event.get("decision") or event.get("status"))
        if label_outcome and _event_matches(event, ("finding", "finding_id", "id", "comment_id"), refs):
            return {
                "outcome": label_outcome,
                "source": str(event.get("source") or event.get("type") or "human_review"),
                "timestamp": event.get("timestamp") or event.get("created_at"),
                "commit": event.get("commit") or event.get("sha"),
                "reason": event.get("reason") or "matched structured review decision",
                "sha": event.get("sha"),
            }
    return None


def _event_matches(event: Mapping[str, Any], keys: Sequence[str], refs: set[str]) -> str:
    for key in keys:
        candidates = _as_list(event.get(key))
        for candidate in candidates:
            if isinstance(candidate, Mapping):
                candidate_refs = _finding_refs(
                    str(candidate.get("id") or candidate.get("finding_id") or candidate.get("comment_id") or ""),
                    str(candidate.get("rule_id") or candidate.get("check_id") or ""),
                    str(candidate.get("comment_digest") or candidate.get("patch_digest") or ""),
                )
            else:
                candidate_refs = {str(candidate)}
            if refs.intersection(candidate_refs):
                return key
    return ""


def _normalize_outcome(value: Any) -> str:
    label = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not label:
        return ""
    if label in STABLE_OUTCOMES:
        return label
    if label in _RESOLVED_LABELS:
        return OUTCOME_RESOLVED
    if label in _ACCEPTED_LABELS:
        return OUTCOME_ACCEPTED
    if label in _REJECTED_LABELS:
        return OUTCOME_REJECTED
    if label in _SUPERSEDED_LABELS:
        return OUTCOME_SUPERSEDED
    if label in _OPEN_LABELS:
        return OUTCOME_OPEN
    if label in _REGRESSED_LABELS:
        return OUTCOME_REGRESSED
    if label in {"unknown", "unverifiable", "insufficient_evidence"}:
        return OUTCOME_UNVERIFIABLE
    return ""


def _outcome_group(outcome: str) -> str:
    if outcome in {OUTCOME_RESOLVED, OUTCOME_ACCEPTED}:
        return "resolved"
    if outcome == OUTCOME_DISMISSED:
        return "dismissed"
    if outcome == OUTCOME_STALE:
        return "stale"
    if outcome == OUTCOME_UNVERIFIABLE:
        return "unverifiable"
    return "open"


def _gate_state(fixture: Mapping[str, Any]) -> dict[str, str]:
    raw = fixture.get("gate_state") or fixture.get("final_gate_state") or fixture.get("gates")
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, str] = {}
    for key in ("tests", "security", "release"):
        value = raw.get(key)
        if value is not None and value != "":
            result[key] = str(value).strip().lower().replace(" ", "_")
    return result


def _gate_correlations(fixture_reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for fixture in fixture_reports:
        gate_state = fixture.get("gate_state")
        if not isinstance(gate_state, Mapping):
            continue
        summary = fixture.get("summary") if isinstance(fixture.get("summary"), Mapping) else {}
        outcome_counts = summary.get("outcome_counts") if isinstance(summary.get("outcome_counts"), Mapping) else {}
        finding_count = int(summary.get("findings") or 0)
        for gate, state in gate_state.items():
            key = (str(gate), str(state))
            bucket = buckets.setdefault(
                key,
                {
                    "gate": key[0],
                    "state": key[1],
                    "fixtures": 0,
                    "findings": 0,
                    "outcome_counts": Counter(),
                },
            )
            bucket["fixtures"] += 1
            bucket["findings"] += finding_count
            bucket["outcome_counts"].update(
                {str(name): int(count) for name, count in outcome_counts.items()}
            )

    rows = []
    for bucket in sorted(buckets.values(), key=lambda row: (row["gate"], row["state"])):
        rows.append(
            {
                "gate": bucket["gate"],
                "state": bucket["state"],
                "fixtures": bucket["fixtures"],
                "findings": bucket["findings"],
                "outcome_counts": dict(sorted(bucket["outcome_counts"].items())),
            }
        )
    return {
        "data_available": bool(rows),
        "by_gate_state": rows,
        "privacy": "aggregate fixture gate states only; no raw CI logs or review comments",
    }


def _load_manifest(fixture_root: Path) -> dict[str, Any]:
    manifest_path = fixture_root / "manifest.json"
    if not manifest_path.exists():
        return {"schema": MANIFEST_SCHEMA, "fixtures": []}
    manifest = _load_json(manifest_path)
    if manifest.get("schema") not in {None, MANIFEST_SCHEMA}:
        raise ValueError(f"{manifest_path}: unsupported manifest schema {manifest.get('schema')!r}")
    return manifest


def _fixture_paths(fixture_root: Path, manifest: Mapping[str, Any]) -> list[Path]:
    entries = list(_as_list(manifest.get("fixtures")))
    paths = [_resolve_path(entry, fixture_root) for entry in entries] if entries else sorted(fixture_root.glob("*/fixture.json"))
    if not paths:
        raise ValueError(f"no review outcome fixtures found under {fixture_root}")
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError("missing review outcome fixture(s): " + ", ".join(map(str, missing)))
    return paths


def _load_fixture(path: Path) -> Mapping[str, Any]:
    fixture = _load_json(path)
    if not isinstance(fixture, Mapping):
        raise ValueError(f"{path}: fixture must be a JSON object")
    if fixture.get("schema") not in {None, FIXTURE_SCHEMA}:
        raise ValueError(f"{path}: unsupported fixture schema {fixture.get('schema')!r}")
    return fixture


def _write_exports(report: Mapping[str, Any], repo_root: Path, export_dir: str | Path | None) -> dict[str, str]:
    target_dir = _resolve_path(export_dir or Path(".codebase-tooling-mcp/reports"), repo_root)
    target_dir.mkdir(parents=True, exist_ok=True)
    report_id = str(report["report_id"])
    json_path = target_dir / f"{report_id}.json"
    md_path = target_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    return {
        "json": _display_path(json_path, repo_root),
        "markdown": _display_path(md_path, repo_root),
    }


def _markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), Mapping) else {}
    counts = summary.get("outcome_counts", {}) if isinstance(summary.get("outcome_counts"), Mapping) else {}
    lines = [
        f"# Review outcome report `{report.get('report_id', '')}`",
        "",
        f"- Schema: `{report.get('schema', '')}`",
        f"- Generated at: `{report.get('generated_at', '')}`",
        f"- Findings: {summary.get('findings', 0)}",
        f"- Resolved rate: {summary.get('resolved_rate', 0)}",
        f"- Dismissed/noise rate: {summary.get('dismissed_noise_rate', 0)}",
        f"- Evidence coverage: {summary.get('evidence_coverage', 0)}",
        "",
        "## Outcome counts",
    ]
    for outcome, count in sorted(counts.items()):
        lines.append(f"- `{outcome}`: {count}")
    lines.extend(
        [
            "",
            "This report is generated from redacted local fixture metadata. It does not embed raw review comments, raw patches, secrets, or host absolute paths.",
            "",
        ]
    )
    return "\n".join(lines)


def _finding_refs(finding_id: str, rule_id: str, digest: str) -> set[str]:
    return {value for value in {finding_id, rule_id, digest} if value}


def _safe_ref(fixture_id: str, finding_id: str) -> str:
    if finding_id:
        return finding_id
    digest = hashlib.sha256(f"{fixture_id}:missing-id".encode("utf-8")).hexdigest()[:12]
    return f"finding:{digest}"


def _safe_digest(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("sha256:"):
        return value
    if len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value):
        return f"sha256:{value.lower()}"
    return value


def _normalize_repo_path(path: str, fixture_dir: Path) -> str:
    if not path:
        return ""
    path_obj = Path(path)
    if path_obj.is_absolute():
        return path_obj.name
    try:
        return path_obj.as_posix()
    except TypeError:
        return str(_resolve_path(path, fixture_dir))


def _timestamp_string(value: Any) -> str:
    if not value:
        return ""
    parsed = _parse_timestamp(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else str(value)


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hours_between(start: str, end: str) -> float | None:
    start_dt = _parse_timestamp(start)
    end_dt = _parse_timestamp(end)
    if not start_dt or not end_dt:
        return None
    return round(max(0.0, (end_dt - start_dt).total_seconds() / 3600.0), 4)


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 4)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _resolve_path(path_value: str | Path | Any, base: Path) -> Path:
    path = Path(str(path_value))
    return path if path.is_absolute() else base / path


def _display_path(path: str | Path, repo_root: Path) -> str:
    path_obj = Path(path)
    try:
        return path_obj.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path_obj.as_posix()


def _line_number(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _unique(values: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
