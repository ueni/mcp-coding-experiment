# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Read-only workflow event-log checkpoint projection helpers.

This module intentionally treats event logs as already-local audit inputs and
never re-runs tools.  It normalizes a compact JSONL event stream into a stable,
redacted report plus deterministic projection for timeline, checkpoints,
artifact lineage, and optional fork-vs-parent comparison.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

REPORT_SCHEMA = "workflow_checkpoint_report.v1"
EVENT_SCHEMA = "workflow_event.v1"
CHECKPOINT_SCHEMA = "workflow_checkpoint.v1"
PROJECTION_SCHEMA = "workflow_event_projection.v1"
FORK_DIFF_SCHEMA = "workflow_fork_diff.v1"

DEFAULT_EVENT_LOG = ".codebase-tooling-mcp/workflow-events.jsonl"

_ALLOWED_EVENT_TYPES = {
    "workflow.started",
    "workflow.ended",
    "workflow.checkpoint",
    "workflow.fork",
    "tool.summary",
    "guard.decision",
    "catalog.fingerprint",
    "mutation.proposal",
    "test.gate",
    "release.gate",
    "release.readiness",
    "artifact.produced",
}

_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_-])(prompt|raw[_-]?tool|tool[_-]?output|raw[_-]?output|completion|"
    r"token|secret|password|credential|authorization|api[_-]?key|private[_-]?key)(?:$|[_-])",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(\b(?:bearer|token|secret|password|credential|authorization|api[_ -]?key)\b\s*[:= ]\s*\S+"
    r"|\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{12,}\b"
    r"|\bsk-[A-Za-z0-9_-]{16,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b)",
    re.IGNORECASE,
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9._~+%/-])(?:/[A-Za-z0-9._~+@%=-][^\s,;:'\"{}\]<>]*)"
    r"|(?:[A-Za-z]:\\[^\s,;:'\"{}\]<>]+)"
)
_LOCAL_FILE_URI_RE = re.compile(r"\bfile://[^\s,;:'\"{}\]<>]+", re.IGNORECASE)


class _RedactionState:
    def __init__(self) -> None:
        self.findings: list[dict[str, Any]] = []
        self.categories: set[str] = set()

    def add(self, *, path: str, category: str, original: Any) -> None:
        self.categories.add(category)
        fingerprint = hashlib.sha256(str(original).encode("utf-8", "replace")).hexdigest()[:16]
        self.findings.append(
            {
                "path": path,
                "category": category,
                "replacement": f"<redacted:{category}>",
                "fingerprint": f"sha256:{fingerprint}",
            }
        )


def _stable_hash(value: Any, *, prefix: str) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


def _sanitize(value: Any, state: _RedactionState, path: str = "$") -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_item in sorted(value.items(), key=lambda item: str(item[0])):
            key = str(raw_key)
            key_path = f"{path}.{key}"
            if _SENSITIVE_KEY_RE.search(key):
                state.add(path=key_path, category="sensitive_key", original=raw_item)
                sanitized[key] = "<redacted:sensitive_key>"
            else:
                sanitized[key] = _sanitize(raw_item, state, key_path)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item, state, f"{path}[{idx}]") for idx, item in enumerate(value)]
    if isinstance(value, str):
        sanitized_value = value
        if _SENSITIVE_VALUE_RE.search(sanitized_value):
            state.add(path=path, category="secret_value", original=value)
            sanitized_value = _SENSITIVE_VALUE_RE.sub("<redacted:secret_value>", sanitized_value)
        if _LOCAL_FILE_URI_RE.search(sanitized_value):
            state.add(path=path, category="absolute_path", original=value)
            sanitized_value = _LOCAL_FILE_URI_RE.sub("<redacted:absolute_path>", sanitized_value)
        if "://" not in sanitized_value and _ABSOLUTE_PATH_RE.search(sanitized_value):
            state.add(path=path, category="absolute_path", original=value)
            sanitized_value = _ABSOLUTE_PATH_RE.sub("<redacted:absolute_path>", sanitized_value)
        return sanitized_value
    return value


def _compact_ref(raw_ref: Any, *, state: _RedactionState, path: str) -> dict[str, Any]:
    sanitized = _sanitize(raw_ref if isinstance(raw_ref, Mapping) else {"value": raw_ref}, state, path)
    if not isinstance(sanitized, Mapping):
        sanitized = {"value": str(sanitized)}
    allowed = {
        "ref_id",
        "artifact_id",
        "kind",
        "uri",
        "path",
        "digest",
        "hash",
        "trace_id",
        "summary",
        "redaction_status",
        "produced_by",
        "checkpoint_id",
    }
    compact = {key: sanitized[key] for key in sorted(sanitized) if key in allowed}
    if "ref_id" not in compact and "artifact_id" in compact:
        compact["ref_id"] = compact["artifact_id"]
    if "digest" not in compact and "hash" in compact:
        compact["digest"] = compact.pop("hash")
    if "redaction_status" not in compact:
        compact["redaction_status"] = "redacted"
    if "ref_id" not in compact:
        compact["ref_id"] = _stable_hash(compact, prefix="evidence")
    return dict(compact)


def _normalize_event(raw: Mapping[str, Any], sequence: int) -> dict[str, Any]:
    state = _RedactionState()
    sanitized = _sanitize(raw, state)
    event_type = str(sanitized.get("event_type") or sanitized.get("type") or "unknown")
    if event_type not in _ALLOWED_EVENT_TYPES:
        state.add(path="$.event_type", category="unknown_event_type", original=event_type)
        normalized_type = "unknown"
    else:
        normalized_type = event_type

    event_id = str(sanitized.get("event_id") or sanitized.get("id") or f"event-{sequence:04d}")
    checkpoint = sanitized.get("checkpoint") if isinstance(sanitized.get("checkpoint"), Mapping) else {}
    checkpoint_id = str(
        sanitized.get("checkpoint_id")
        or checkpoint.get("checkpoint_id")
        or checkpoint.get("id")
        or ""
    )
    fork = sanitized.get("fork") if isinstance(sanitized.get("fork"), Mapping) else {}
    fork_id = str(sanitized.get("fork_id") or fork.get("fork_id") or "")
    parent_fork_id = str(sanitized.get("parent_fork_id") or fork.get("parent_fork_id") or "")

    evidence_refs = [
        _compact_ref(ref, state=state, path=f"$.evidence_refs[{idx}]")
        for idx, ref in enumerate(sanitized.get("evidence_refs") or [])
    ]
    artifact_refs = [
        _compact_ref(ref, state=state, path=f"$.artifact_refs[{idx}]")
        for idx, ref in enumerate(sanitized.get("artifact_refs") or [])
    ]

    status = str(sanitized.get("status") or sanitized.get("decision") or "observed")
    label = str(sanitized.get("label") or normalized_type.replace(".", " "))
    event = {
        "schema": EVENT_SCHEMA,
        "sequence": int(sanitized.get("sequence") or sequence),
        "event_id": event_id,
        "event_type": normalized_type,
        "timestamp": str(sanitized.get("timestamp") or ""),
        "label": label,
        "status": status,
        "workflow_id": str(sanitized.get("workflow_id") or ""),
        "checkpoint_id": checkpoint_id,
        "parent_checkpoint_id": str(
            sanitized.get("parent_checkpoint_id")
            or checkpoint.get("parent_checkpoint_id")
            or ""
        ),
        "fork_id": fork_id,
        "parent_fork_id": parent_fork_id,
        "evidence_refs": evidence_refs,
        "artifact_refs": artifact_refs,
        "privacy": {
            "raw_prompt_persisted": False,
            "raw_tool_output_persisted": False,
            "secrets_persisted": False,
            "absolute_host_paths_persisted": False,
            "redaction_categories": sorted(state.categories),
            "redaction_count": len(state.findings),
        },
    }
    if state.findings:
        event["redaction_findings"] = state.findings
    if normalized_type == "workflow.fork":
        event["fork_marker"] = {
            "fork_id": fork_id,
            "parent_fork_id": parent_fork_id,
            "parent_checkpoint_id": event["parent_checkpoint_id"],
        }
    return event


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    if not path.exists():
        return [], [{"code": "missing", "message": "event log file is missing"}], "missing"
    if not path.is_file():
        return [], [{"code": "not_file", "message": "event log path is not a file"}], "invalid"

    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(
                {
                    "code": "corrupt_json",
                    "line": line_number,
                    "message": "event log line is not valid JSON",
                    "column": exc.colno,
                }
            )
            continue
        if not isinstance(raw, Mapping):
            errors.append(
                {
                    "code": "invalid_event",
                    "line": line_number,
                    "message": "event log line must be a JSON object",
                }
            )
            continue
        events.append(_normalize_event(raw, len(events) + 1))
    status = "corrupt" if errors else "ok"
    return events, errors, status


def _checkpoint_rows(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for event in events:
        checkpoint_id = str(event.get("checkpoint_id") or "")
        if not checkpoint_id:
            continue
        row = rows.setdefault(
            checkpoint_id,
            {
                "schema": CHECKPOINT_SCHEMA,
                "checkpoint_id": checkpoint_id,
                "first_sequence": event.get("sequence"),
                "last_sequence": event.get("sequence"),
                "event_ids": [],
                "fork_ids": [],
                "artifact_ids": [],
                "evidence_ref_ids": [],
            },
        )
        row["last_sequence"] = event.get("sequence")
        row["event_ids"].append(event.get("event_id"))
        if event.get("fork_id") and event.get("fork_id") not in row["fork_ids"]:
            row["fork_ids"].append(event.get("fork_id"))
        for ref in event.get("artifact_refs") or []:
            artifact_id = ref.get("artifact_id") or ref.get("ref_id")
            if artifact_id and artifact_id not in row["artifact_ids"]:
                row["artifact_ids"].append(artifact_id)
        for ref in event.get("evidence_refs") or []:
            ref_id = ref.get("ref_id")
            if ref_id and ref_id not in row["evidence_ref_ids"]:
                row["evidence_ref_ids"].append(ref_id)
    return [rows[key] for key in sorted(rows)]


def _projection(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(events, key=lambda event: (event.get("sequence", 0), event.get("event_id", "")))
    timeline = [
        {
            "sequence": event.get("sequence"),
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "label": event.get("label"),
            "status": event.get("status"),
            "checkpoint_id": event.get("checkpoint_id"),
            "fork_id": event.get("fork_id"),
            "evidence_ref_count": len(event.get("evidence_refs") or []),
            "artifact_ref_count": len(event.get("artifact_refs") or []),
        }
        for event in ordered
    ]

    artifacts: dict[str, dict[str, Any]] = {}
    for event in ordered:
        for ref in event.get("artifact_refs") or []:
            artifact_id = str(ref.get("artifact_id") or ref.get("ref_id") or "")
            if not artifact_id:
                continue
            row = artifacts.setdefault(
                artifact_id,
                {
                    "artifact_id": artifact_id,
                    "kind": ref.get("kind", "artifact"),
                    "latest_digest": "",
                    "produced_by_events": [],
                    "checkpoints": [],
                    "fork_ids": [],
                    "evidence_ref_ids": [],
                },
            )
            row["latest_digest"] = str(ref.get("digest") or row["latest_digest"])
            row["produced_by_events"].append(event.get("event_id"))
            if event.get("checkpoint_id") and event.get("checkpoint_id") not in row["checkpoints"]:
                row["checkpoints"].append(event.get("checkpoint_id"))
            if event.get("fork_id") and event.get("fork_id") not in row["fork_ids"]:
                row["fork_ids"].append(event.get("fork_id"))
            for evidence in event.get("evidence_refs") or []:
                if evidence.get("ref_id") and evidence.get("ref_id") not in row["evidence_ref_ids"]:
                    row["evidence_ref_ids"].append(evidence.get("ref_id"))

    event_type_counts = Counter(str(event.get("event_type")) for event in ordered)
    return {
        "schema": PROJECTION_SCHEMA,
        "deterministic": True,
        "reran_tools": False,
        "event_count": len(ordered),
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "timeline": timeline,
        "artifact_lineage": [artifacts[key] for key in sorted(artifacts)],
    }


def _events_for_fork(events: Sequence[Mapping[str, Any]], fork_id: str) -> list[Mapping[str, Any]]:
    return [event for event in events if str(event.get("fork_id") or "") == fork_id]


def _artifact_digest_map(events: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    digest_by_artifact: dict[str, str] = {}
    for event in events:
        for ref in event.get("artifact_refs") or []:
            artifact_id = str(ref.get("artifact_id") or ref.get("ref_id") or "")
            if artifact_id:
                digest_by_artifact[artifact_id] = str(ref.get("digest") or "")
    return digest_by_artifact


def _infer_fork_pair(events: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    for event in events:
        marker = event.get("fork_marker") if isinstance(event.get("fork_marker"), Mapping) else {}
        fork_id = str(marker.get("fork_id") or event.get("fork_id") or "")
        parent_fork_id = str(marker.get("parent_fork_id") or event.get("parent_fork_id") or "")
        if fork_id and parent_fork_id:
            return fork_id, parent_fork_id
    return "", ""


def _fork_diff(events: Sequence[Mapping[str, Any]], fork_id: str, parent_fork_id: str) -> dict[str, Any]:
    if not fork_id or not parent_fork_id:
        inferred_fork, inferred_parent = _infer_fork_pair(events)
        fork_id = fork_id or inferred_fork
        parent_fork_id = parent_fork_id or inferred_parent
    fork_events = _events_for_fork(events, fork_id) if fork_id else []
    parent_events = _events_for_fork(events, parent_fork_id) if parent_fork_id else []
    fork_artifacts = _artifact_digest_map(fork_events)
    parent_artifacts = _artifact_digest_map(parent_events)
    changed_artifacts = []
    for artifact_id in sorted(set(fork_artifacts) | set(parent_artifacts)):
        parent_digest = parent_artifacts.get(artifact_id, "")
        fork_digest = fork_artifacts.get(artifact_id, "")
        if parent_digest != fork_digest:
            changed_artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "parent_digest": parent_digest,
                    "fork_digest": fork_digest,
                    "change": "added" if not parent_digest else "removed" if not fork_digest else "changed",
                }
            )
    fork_type_counts = Counter(str(event.get("event_type")) for event in fork_events)
    parent_type_counts = Counter(str(event.get("event_type")) for event in parent_events)
    return {
        "schema": FORK_DIFF_SCHEMA,
        "available": bool(fork_id and parent_fork_id),
        "fork_id": fork_id,
        "parent_fork_id": parent_fork_id,
        "event_delta": len(fork_events) - len(parent_events),
        "added_event_ids": sorted(str(event.get("event_id")) for event in fork_events),
        "parent_event_ids": sorted(str(event.get("event_id")) for event in parent_events),
        "event_type_count_delta": {
            event_type: fork_type_counts.get(event_type, 0) - parent_type_counts.get(event_type, 0)
            for event_type in sorted(set(fork_type_counts) | set(parent_type_counts))
        },
        "changed_artifacts": changed_artifacts,
    }


def _privacy_summary(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    categories: Counter[str] = Counter()
    finding_count = 0
    for event in events:
        privacy = event.get("privacy") if isinstance(event.get("privacy"), Mapping) else {}
        finding_count += int(privacy.get("redaction_count") or 0)
        categories.update(str(category) for category in privacy.get("redaction_categories") or [])
    return {
        "privacy_schema": "workflow_event_privacy.v1",
        "raw_prompts_persisted": False,
        "raw_tool_outputs_persisted": False,
        "secrets_persisted": False,
        "absolute_host_paths_persisted": False,
        "evidence_references_redacted": True,
        "redaction_count": finding_count,
        "redaction_categories": dict(sorted(categories.items())),
    }


def build_workflow_checkpoint_report(
    event_log_path: Path,
    *,
    display_path: str = "",
    fork_id: str = "",
    parent_fork_id: str = "",
    include_events: bool = True,
) -> dict[str, Any]:
    """Build a deterministic read-only workflow checkpoint report from JSONL."""
    events, errors, status = _load_jsonl(event_log_path)
    projection = _projection(events)
    checkpoints = _checkpoint_rows(events)
    report_id = _stable_hash(
        {
            "events": events,
            "errors": errors,
            "fork_id": fork_id,
            "parent_fork_id": parent_fork_id,
        },
        prefix="workflow-checkpoint",
    )
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "report_id": report_id,
        "read_only": True,
        "ok": status == "ok",
        "status": status,
        "event_log": {
            "path": display_path or event_log_path.as_posix(),
            "format": "jsonl",
            "source": "local_fixture_or_audit_log",
        },
        "summary": {
            "event_count": len(events),
            "checkpoint_count": len(checkpoints),
            "artifact_count": len(projection["artifact_lineage"]),
            "error_count": len(errors),
            "fork_count": len({event.get("fork_id") for event in events if event.get("fork_id")}),
        },
        "privacy": _privacy_summary(events),
        "checkpoints": checkpoints,
        "projection": projection,
        "fork_diff": _fork_diff(events, fork_id, parent_fork_id),
        "errors": errors,
    }
    if include_events:
        report["events"] = list(events)
    return report
