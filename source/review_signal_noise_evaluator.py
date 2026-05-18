# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Deterministic signal/noise evaluation for code-review workflow outputs.

The evaluator is intentionally offline and read-only. It scores a fixture pack
that pairs review scenarios with expected blocker findings, explicit
"should not flag" non-findings, and normalized review output JSON.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EVALUATION_SCHEMA = "review_signal_noise_evaluation.v1"
FIXTURE_SCHEMA = "review_signal_noise_fixture.v1"
MANIFEST_SCHEMA = "review_signal_noise_manifest.v1"
REVIEW_OUTPUT_SCHEMA = "review_output_findings.v1"
DEFAULT_THRESHOLDS = {
    "min_precision": 1.0,
    "min_recall": 1.0,
    "max_spurious_findings": 0,
}
DEFAULT_FIXTURE_DIR = Path("tests/fixtures/review_evaluation")

_TEXT_KEYS = (
    "id",
    "title",
    "summary",
    "message",
    "description",
    "details",
    "recommendation",
    "suggestion",
    "code",
    "severity",
)
_PATH_KEYS = ("path", "file", "file_path", "relative_path")
_FINDING_LIST_KEYS = ("findings", "issues", "comments", "blockers", "review_findings")


@dataclass(frozen=True)
class ReviewFinding:
    """Normalized review finding used for deterministic fixture matching."""

    finding_id: str
    title: str
    message: str
    path: str
    line: int | None
    severity: str
    evidence_paths: tuple[str, ...]
    text: str

    def public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.finding_id,
            "title": self.title,
            "message": _clip(self.message),
            "path": self.path,
            "severity": self.severity,
            "evidence_paths": list(self.evidence_paths),
        }
        if self.line is not None:
            payload["line"] = self.line
        return payload


def repository_root() -> Path:
    """Return this repository root from the checked-in source module path."""

    return Path(__file__).resolve().parents[1]


def evaluate_review_fixtures(
    fixture_dir: str | Path = DEFAULT_FIXTURE_DIR,
    *,
    actual_output_dir: str | Path | None = None,
    thresholds: Mapping[str, float | int] | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate a review fixture pack and return structured signal/noise output.

    Args:
        fixture_dir: Directory containing ``manifest.json`` and fixture folders.
        actual_output_dir: Optional directory with ``<fixture-id>.json`` files that
            override each fixture's checked-in ``review_output.json``. Missing
            overrides fall back to the fixture-local output file.
        thresholds: Optional overrides for precision/recall/spurious limits.
        repo_root: Root used to render evidence paths as repository-relative.

    The function only reads fixture/output files and never shells out, mutates the
    repository, or uses the network.
    """

    root = Path(repo_root).resolve() if repo_root is not None else repository_root()
    fixture_root = _resolve_path(fixture_dir, root).resolve()
    actual_root = _resolve_path(actual_output_dir, root).resolve() if actual_output_dir else None
    manifest = _load_manifest(fixture_root)
    effective_thresholds = _effective_thresholds(manifest.get("thresholds", {}), thresholds)

    fixture_results = []
    all_spurious: list[dict[str, Any]] = []
    evidence_paths: list[str] = []
    total_expected = 0
    total_actual = 0
    total_true_positive = 0
    total_missed = 0

    for fixture_path in _fixture_paths(fixture_root, manifest):
        fixture = _load_json(fixture_path)
        result = _evaluate_fixture(
            fixture=fixture,
            fixture_path=fixture_path,
            fixture_root=fixture_root,
            actual_output_dir=actual_root,
            thresholds=effective_thresholds,
            repo_root=root,
        )
        fixture_results.append(result)
        all_spurious.extend(result["spurious_findings"])
        evidence_paths.extend(result["evidence_paths"])
        total_expected += result["counts"]["expected_findings"]
        total_actual += result["counts"]["actual_findings"]
        total_true_positive += result["counts"]["true_positives"]
        total_missed += result["counts"]["missed_findings"]

    precision = _ratio(total_true_positive, total_true_positive + len(all_spurious), empty=1.0)
    recall = _ratio(total_true_positive, total_expected, empty=1.0)
    threshold_status = _threshold_status(
        precision=precision,
        recall=recall,
        spurious_count=len(all_spurious),
        thresholds=effective_thresholds,
    )
    ok = threshold_status["ok"] and all(fixture["ok"] for fixture in fixture_results)

    return {
        "schema": EVALUATION_SCHEMA,
        "ok": ok,
        "read_only": True,
        "fixture_root": _display_path(fixture_root, root),
        "thresholds": effective_thresholds,
        "summary": {
            "fixtures": len(fixture_results),
            "expected_findings": total_expected,
            "actual_findings": total_actual,
            "true_positives": total_true_positive,
            "missed_findings": total_missed,
            "spurious_findings": len(all_spurious),
            "precision": precision,
            "recall": recall,
        },
        "threshold_status": threshold_status,
        "spurious_findings": all_spurious,
        "fixtures": fixture_results,
        "evidence_paths": _unique(evidence_paths),
    }


def _evaluate_fixture(
    *,
    fixture: Mapping[str, Any],
    fixture_path: Path,
    fixture_root: Path,
    actual_output_dir: Path | None,
    thresholds: Mapping[str, float | int],
    repo_root: Path,
) -> dict[str, Any]:
    fixture_id = str(fixture.get("id") or fixture_path.parent.name)
    if fixture.get("schema") not in {None, FIXTURE_SCHEMA}:
        raise ValueError(f"{fixture_path}: unsupported fixture schema {fixture.get('schema')!r}")

    fixture_dir = fixture_path.parent
    diff_path = _resolve_path(fixture.get("diff_path", "diff.patch"), fixture_dir)
    review_output_path = _review_output_path(
        fixture=fixture,
        fixture_id=fixture_id,
        fixture_dir=fixture_dir,
        actual_output_dir=actual_output_dir,
    )
    review_doc = _load_json(review_output_path)
    findings = _extract_findings(review_doc, review_output_path, repo_root)
    expected = list(_as_list(fixture.get("expected_findings")))
    non_findings = list(_as_list(fixture.get("should_not_flag")))

    matched_actual_indexes: set[int] = set()
    true_positives = []
    missed_findings = []

    for expected_finding in expected:
        match_index = _first_matching_finding(expected_finding, findings, matched_actual_indexes)
        expected_id = str(expected_finding.get("id") or expected_finding.get("title") or "expected")
        if match_index is None:
            missed_findings.append(_expected_public_dict(expected_finding, repo_root, fixture_dir))
            continue
        matched_actual_indexes.add(match_index)
        true_positives.append(
            {
                "expected_id": expected_id,
                "actual_id": findings[match_index].finding_id,
                "path": findings[match_index].path,
                "evidence_paths": list(findings[match_index].evidence_paths),
            }
        )

    spurious = []
    for index, finding in enumerate(findings):
        if index in matched_actual_indexes:
            continue
        public = finding.public_dict()
        public["fixture_id"] = fixture_id
        public["matched_should_not_flag_ids"] = _matching_non_finding_ids(non_findings, finding)
        spurious.append(public)

    precision = _ratio(len(true_positives), len(true_positives) + len(spurious), empty=1.0)
    recall = _ratio(len(true_positives), len(expected), empty=1.0)
    threshold_status = _threshold_status(
        precision=precision,
        recall=recall,
        spurious_count=len(spurious),
        thresholds=thresholds,
    )

    evidence_paths = [
        _display_path(fixture_path, repo_root),
        _display_path(diff_path, repo_root),
        _display_path(review_output_path, repo_root),
    ]
    for expected_finding in expected:
        for evidence_path in _as_list(expected_finding.get("evidence_paths")):
            evidence_paths.append(
                _display_path(_resolve_path(evidence_path, fixture_dir), repo_root)
            )
    for finding in findings:
        evidence_paths.extend(finding.evidence_paths)

    return {
        "id": fixture_id,
        "description": str(fixture.get("description") or ""),
        "ok": threshold_status["ok"],
        "diff_path": _display_path(diff_path, repo_root),
        "review_output_path": _display_path(review_output_path, repo_root),
        "counts": {
            "expected_findings": len(expected),
            "actual_findings": len(findings),
            "true_positives": len(true_positives),
            "missed_findings": len(missed_findings),
            "spurious_findings": len(spurious),
        },
        "precision": precision,
        "recall": recall,
        "true_positives": true_positives,
        "missed_findings": missed_findings,
        "spurious_findings": spurious,
        "should_not_flag_ids": [
            str(item.get("id") or item.get("title") or "non_finding")
            for item in non_findings
        ],
        "evidence_paths": _unique(evidence_paths),
        "threshold_status": threshold_status,
    }


def _effective_thresholds(
    manifest_thresholds: Mapping[str, Any],
    override_thresholds: Mapping[str, float | int] | None,
) -> dict[str, float | int]:
    thresholds: dict[str, float | int] = dict(DEFAULT_THRESHOLDS)
    for source in (manifest_thresholds, override_thresholds or {}):
        for key in DEFAULT_THRESHOLDS:
            if key not in source or source[key] is None:
                continue
            if key == "max_spurious_findings":
                thresholds[key] = int(source[key])
            else:
                thresholds[key] = float(source[key])
    return thresholds


def _threshold_status(
    *,
    precision: float,
    recall: float,
    spurious_count: int,
    thresholds: Mapping[str, float | int],
) -> dict[str, Any]:
    checks = {
        "precision": precision >= float(thresholds["min_precision"]),
        "recall": recall >= float(thresholds["min_recall"]),
        "spurious_findings": spurious_count <= int(thresholds["max_spurious_findings"]),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "observed": {
            "precision": precision,
            "recall": recall,
            "spurious_findings": spurious_count,
        },
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
    if entries:
        paths = [_resolve_path(entry, fixture_root) for entry in entries]
    else:
        paths = sorted(fixture_root.glob("*/fixture.json"))
    if not paths:
        raise ValueError(f"no review evaluation fixtures found under {fixture_root}")
    missing = [path for path in paths if not path.exists()]
    if missing:
        missing_paths = ", ".join(map(str, missing))
        raise FileNotFoundError(
            f"missing review evaluation fixture(s): {missing_paths}"
        )
    return paths


def _review_output_path(
    *,
    fixture: Mapping[str, Any],
    fixture_id: str,
    fixture_dir: Path,
    actual_output_dir: Path | None,
) -> Path:
    if actual_output_dir is not None:
        candidate = actual_output_dir / f"{fixture_id}.json"
        if candidate.exists():
            return candidate
    return _resolve_path(fixture.get("review_output_path", "review_output.json"), fixture_dir)


def _extract_findings(
    review_doc: Any,
    review_output_path: Path,
    repo_root: Path,
) -> list[ReviewFinding]:
    raw_findings = _raw_finding_items(review_doc)
    return [
        _normalize_finding(
            item,
            index=index,
            review_output_path=review_output_path,
            repo_root=repo_root,
        )
        for index, item in enumerate(raw_findings, start=1)
    ]


def _raw_finding_items(review_doc: Any) -> list[Any]:
    if isinstance(review_doc, list):
        return review_doc
    if not isinstance(review_doc, Mapping):
        return []
    for key in _FINDING_LIST_KEYS:
        value = review_doc.get(key)
        if isinstance(value, list):
            return value
    review = review_doc.get("review")
    if isinstance(review, Mapping):
        for key in _FINDING_LIST_KEYS:
            value = review.get(key)
            if isinstance(value, list):
                return value
    return []


def _normalize_finding(
    item: Any,
    *,
    index: int,
    review_output_path: Path,
    repo_root: Path,
) -> ReviewFinding:
    if isinstance(item, Mapping):
        finding_id = str(item.get("id") or item.get("finding_id") or f"finding-{index}")
        title = str(item.get("title") or item.get("summary") or finding_id)
        message = str(item.get("message") or item.get("description") or item.get("details") or "")
        path = _first_string_value(item, _PATH_KEYS)
        line = _line_number(item.get("line") or item.get("start_line"))
        severity = str(item.get("severity") or item.get("level") or "unknown")
        evidence_paths = tuple(
            _display_path(_resolve_path(path_value, review_output_path.parent), repo_root)
            for path_value in _as_list(item.get("evidence_paths"))
        )
        text_parts = [str(item.get(key, "")) for key in _TEXT_KEYS]
        text_parts.extend(str(value) for value in _as_list(item.get("tags")))
        text = " ".join(part for part in text_parts if part).lower()
    else:
        finding_id = f"finding-{index}"
        title = str(item)
        message = ""
        path = ""
        line = None
        severity = "unknown"
        evidence_paths = ()
        text = title.lower()

    if not evidence_paths:
        evidence_paths = (_display_path(review_output_path, repo_root),)

    return ReviewFinding(
        finding_id=finding_id,
        title=title,
        message=message,
        path=path,
        line=line,
        severity=severity,
        evidence_paths=evidence_paths,
        text=text,
    )


def _first_matching_finding(
    expected: Mapping[str, Any],
    findings: Sequence[ReviewFinding],
    used_indexes: set[int],
) -> int | None:
    for index, finding in enumerate(findings):
        if index in used_indexes:
            continue
        if _matches_expectation(expected, finding):
            return index
    return None


def _matching_non_finding_ids(
    non_findings: Sequence[Mapping[str, Any]], finding: ReviewFinding
) -> list[str]:
    ids = []
    for non_finding in non_findings:
        if _matches_expectation(non_finding, finding):
            ids.append(str(non_finding.get("id") or non_finding.get("title") or "non_finding"))
    return ids


def _matches_expectation(expectation: Mapping[str, Any], finding: ReviewFinding) -> bool:
    expected_path = str(expectation.get("path") or "")
    if expected_path and expected_path != finding.path:
        return False

    expected_line = _line_number(expectation.get("line"))
    if expected_line is not None and finding.line is not None:
        tolerance = int(expectation.get("line_tolerance", 3))
        if abs(expected_line - finding.line) > tolerance:
            return False

    match_terms = [str(term).lower() for term in _as_list(expectation.get("match_terms"))]
    if match_terms:
        return all(term in finding.text for term in match_terms)

    match_any = [str(term).lower() for term in _as_list(expectation.get("match_any"))]
    if match_any:
        return any(term in finding.text for term in match_any)

    expected_id = str(expectation.get("id") or "").lower()
    return bool(expected_id and expected_id in finding.text)


def _expected_public_dict(
    expected: Mapping[str, Any], repo_root: Path, fixture_dir: Path
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(expected.get("id") or expected.get("title") or "expected"),
        "title": str(expected.get("title") or expected.get("id") or "expected"),
        "path": str(expected.get("path") or ""),
        "match_terms": [str(term) for term in _as_list(expected.get("match_terms"))],
        "evidence_paths": [
            _display_path(_resolve_path(path_value, fixture_dir), repo_root)
            for path_value in _as_list(expected.get("evidence_paths"))
        ],
    }
    line = _line_number(expected.get("line"))
    if line is not None:
        payload["line"] = line
    return payload


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


def _first_string_value(item: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str):
            return value
    return ""


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


def _ratio(numerator: int, denominator: int, *, empty: float) -> float:
    if denominator == 0:
        return empty
    return round(numerator / denominator, 4)


def _unique(values: Any) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value)
        if text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _clip(text: str, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"
