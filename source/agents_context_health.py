# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Read-only AGENTS.md context health checks.

The report intentionally emits metadata, counts, line numbers, and stable line
hashes only. It does not upload data, perform network calls, write artifacts, or
return AGENTS.md text excerpts.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPORT_SCHEMA = "agents_context_health.v1"
SUMMARY_SCHEMA = "agents_context_health.summary.v1"
DEFAULT_AGENTS_PATH = "AGENTS.md"
DEFAULT_TOKEN_BUDGET = 1600
DEFAULT_BYTE_BUDGET = 6000
MAX_FINDINGS = 80

_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("safety", re.compile(r"\b(secret|token|credential|auth|security|safe|sandbox|network|upload|exfiltrat|redact)\b", re.I)),
    ("mutation", re.compile(r"\b(write|edit|delete|move|commit|push|merge|deploy|mutation|rollback|snapshot)\b", re.I)),
    ("routing", re.compile(r"\b(router|workflow|tool|call|invoke|prompt|mode|quality_router|task_router)\b", re.I)),
    ("documentation", re.compile(r"\b(docs?|readme|canonical|index|guide|manual|reference|see )\b", re.I)),
    ("communication", re.compile(r"\b(issue|pr|comment|handoff|review|label|github|ask|clarif)\b", re.I)),
    ("product_scope", re.compile(r"\b(product|scope|service|entrypoint|implementation|alias|repo)\b", re.I)),
    ("generated_artifacts", re.compile(r"\b(generated|artifact|report|cache|runtime state)\b", re.I)),
)

_STALE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("stale_marker", "Line contains a stale/TODO/deprecated marker.", re.compile(r"\b(todo|fixme|deprecated|outdated|stale|legacy|temporary|soon|later)\b", re.I)),
    ("date_sensitive", "Line appears date/version sensitive and may need ownership outside AGENTS.md.", re.compile(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2}|v\d+\.\d+|version\s+\d+)\b", re.I)),
)

_RISKY_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("instruction_override", "Global instruction may override higher-priority or local guidance.", re.compile(r"\b(ignore|disregard|override|bypass)\b.{0,80}\b(instruction|policy|guardrail|safety|system|developer)\b", re.I)),
    ("blanket_never_always", "Blanket always/never guidance may be too broad for a compact entrypoint.", re.compile(r"\b(always|never|must not|do not)\b.{0,100}\b(any|all|every|without exception|under any circumstances)\b", re.I)),
    ("network_or_upload", "Instruction references network/upload/exfiltration-sensitive behavior.", re.compile(r"\b(upload|send|transmit|post|network|internet|external|exfiltrat)\b", re.I)),
    ("secret_handling", "Instruction references secrets/credentials; keep it concise and canonical.", re.compile(r"\b(secret|token|credential|private key|authorization header|api[_ -]?key)\b", re.I)),
    ("destructive_action", "Instruction references destructive or external mutation behavior.", re.compile(r"\b(delete|remove|wipe|force[- ]?push|merge|deploy|release)\b", re.I)),
)

_MOVE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("router_candidate", "Detailed tool/workflow routing may belong in router cards or workflow docs.", re.compile(r"\b(task_router|quality_router|workflow_select|call|invoke|mode=|tool)\b", re.I)),
    ("docs_candidate", "Detailed reference material may belong in canonical docs with AGENTS.md linking to it.", re.compile(r"\b(see|read|documented in|docs?/|README|canonical)\b", re.I)),
    ("long_instruction", "Long instruction line may be better summarized in AGENTS.md and moved to docs.", re.compile(r".{220,}")),
)


def _line_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _normalize_line(line: str) -> str:
    line = re.sub(r"`[^`]+`", "`<code>`", line.strip().lower())
    line = re.sub(r"\[[^\]]+\]\([^)]*\)", "<link>", line)
    line = re.sub(r"[^a-z0-9<>`]+", " ", line)
    return re.sub(r"\s+", " ", line).strip()


def _resolve_repo_file(repo_root: Path, rel_path: str) -> Path:
    root = repo_root.resolve()
    requested = (root / rel_path).resolve()
    try:
        requested.relative_to(root)
    except ValueError as exc:
        raise ValueError("path must stay inside the repository boundary") from exc
    return requested


def _instruction_category(line: str) -> str:
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(line):
            return category
    return "general"


def _finding(
    *,
    finding_id: str,
    severity: str,
    category: str,
    line_number: int,
    line: str,
    message: str,
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": severity,
        "category": category,
        "line": line_number,
        "line_hash": _line_hash(line),
        "message": message,
    }


def analyze_agents_context(
    repo_root: Path,
    *,
    path: str = DEFAULT_AGENTS_PATH,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    byte_budget: int = DEFAULT_BYTE_BUDGET,
) -> dict[str, Any]:
    """Return a bounded read-only health report for a repository AGENTS.md file."""

    target = _resolve_repo_file(repo_root, path)
    rel_path = target.relative_to(repo_root.resolve()).as_posix()
    if not target.exists():
        return {
            "schema": REPORT_SCHEMA,
            "read_only": True,
            "advisory_only": True,
            "target": {"path": rel_path, "exists": False, "repo_boundary_enforced": True},
            "budget": {
                "bytes": 0,
                "chars": 0,
                "estimated_tokens": 0,
                "byte_budget": int(byte_budget),
                "token_budget": int(token_budget),
                "status": "missing",
            },
            "summary": {"ok": False, "status": "missing", "finding_count": 1},
            "instruction_categories": [],
            "duplicate_guidance": [],
            "stale_guidance": [],
            "risky_global_instructions": [],
            "move_candidates": [],
            "findings": [
                {
                    "id": "missing_agents_file",
                    "severity": "medium",
                    "category": "documentation",
                    "line": 0,
                    "message": "AGENTS.md was not found at the requested repository-relative path.",
                }
            ],
            "safety": _safety_metadata(),
        }

    raw = target.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    estimated_tokens = max(1, (len(text) + 3) // 4) if text else 0

    category_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"line_count": 0, "bullet_count": 0, "estimated_tokens": 0})
    normalized_lines: dict[str, list[int]] = defaultdict(list)
    findings: list[dict[str, Any]] = []
    stale_guidance: list[dict[str, Any]] = []
    risky_global_instructions: list[dict[str, Any]] = []
    move_candidates: list[dict[str, Any]] = []

    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--") or stripped.startswith("-->"):
            continue
        category = _instruction_category(stripped)
        category_counts[category]["line_count"] += 1
        category_counts[category]["estimated_tokens"] += max(1, (len(stripped) + 3) // 4)
        if stripped.startswith(("- ", "* ", "1. ", "2. ", "3. ", "4. ", "5. ")):
            category_counts[category]["bullet_count"] += 1
        normalized = _normalize_line(stripped)
        if len(normalized) >= 24:
            normalized_lines[normalized].append(index)

        for stale_id, message, pattern in _STALE_PATTERNS:
            if pattern.search(stripped):
                item = _finding(
                    finding_id=stale_id,
                    severity="low",
                    category=category,
                    line_number=index,
                    line=stripped,
                    message=message,
                )
                stale_guidance.append(item)
                findings.append(item)
        for risk_id, message, pattern in _RISKY_PATTERNS:
            if pattern.search(stripped):
                severity = "medium" if risk_id in {"instruction_override", "network_or_upload", "destructive_action"} else "low"
                item = _finding(
                    finding_id=risk_id,
                    severity=severity,
                    category=category,
                    line_number=index,
                    line=stripped,
                    message=message,
                )
                risky_global_instructions.append(item)
                findings.append(item)
        for move_id, message, pattern in _MOVE_PATTERNS:
            if pattern.search(stripped):
                item = _finding(
                    finding_id=move_id,
                    severity="info",
                    category=category,
                    line_number=index,
                    line=stripped,
                    message=message,
                )
                move_candidates.append(item)
                findings.append(item)

    duplicate_guidance: list[dict[str, Any]] = []
    for normalized, occurrences in sorted(normalized_lines.items(), key=lambda item: (item[1][0], item[0])):
        if len(occurrences) < 2:
            continue
        item = {
            "id": "duplicate_guidance",
            "severity": "low",
            "category": "deduplication",
            "lines": occurrences[:8],
            "occurrence_count": len(occurrences),
            "line_hash": _line_hash(normalized),
            "message": "Repeated guidance can usually be collapsed or moved to a canonical document.",
        }
        duplicate_guidance.append(item)
        findings.append(item)

    if len(raw) > byte_budget:
        findings.append(
            {
                "id": "byte_budget_exceeded",
                "severity": "medium",
                "category": "budget",
                "line": 0,
                "message": "AGENTS.md exceeds the configured byte budget for minimal context.",
            }
        )
    if estimated_tokens > token_budget:
        findings.append(
            {
                "id": "token_budget_exceeded",
                "severity": "medium",
                "category": "budget",
                "line": 0,
                "message": "AGENTS.md exceeds the configured estimated token budget for minimal context.",
            }
        )

    category_rows = [
        {"category": category, **counts}
        for category, counts in sorted(category_counts.items(), key=lambda item: item[0])
    ]
    severity_counts = Counter(str(item.get("severity", "info")) for item in findings)
    status = "clean"
    if len(raw) > byte_budget or estimated_tokens > token_budget:
        status = "over-budget"
    elif any(item.get("severity") in {"medium", "high"} for item in findings):
        status = "warnings"
    elif findings:
        status = "advisory"

    return {
        "schema": REPORT_SCHEMA,
        "read_only": True,
        "advisory_only": True,
        "target": {"path": rel_path, "exists": True, "repo_boundary_enforced": True},
        "budget": {
            "bytes": len(raw),
            "chars": len(text),
            "estimated_tokens": estimated_tokens,
            "byte_budget": int(byte_budget),
            "token_budget": int(token_budget),
            "remaining_bytes": int(byte_budget) - len(raw),
            "remaining_tokens": int(token_budget) - estimated_tokens,
            "status": "ok" if len(raw) <= byte_budget and estimated_tokens <= token_budget else "over-budget",
        },
        "summary": {
            "ok": status not in {"missing", "over-budget"},
            "status": status,
            "line_count": len(lines),
            "finding_count": len(findings),
            "severity_counts": dict(sorted(severity_counts.items())),
            "category_count": len(category_rows),
        },
        "instruction_categories": category_rows,
        "duplicate_guidance": duplicate_guidance[:MAX_FINDINGS],
        "stale_guidance": stale_guidance[:MAX_FINDINGS],
        "risky_global_instructions": risky_global_instructions[:MAX_FINDINGS],
        "move_candidates": move_candidates[:MAX_FINDINGS],
        "findings": findings[:MAX_FINDINGS],
        "safety": _safety_metadata(),
    }


def summarize_agents_context_health(report: dict[str, Any], *, max_categories: int = 8) -> dict[str, Any]:
    """Return a compact schema-tagged AGENTS context-health summary.

    The summary is safe to embed in broader governance reports: it preserves only
    aggregate counts, budget metadata, repository-relative target metadata, and
    safety flags. It intentionally excludes findings and line hashes so broader
    reports do not echo AGENTS.md content or stable content fingerprints.
    """

    budget = report.get("budget", {}) if isinstance(report.get("budget"), dict) else {}
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    target = report.get("target", {}) if isinstance(report.get("target"), dict) else {}
    safety = report.get("safety", {}) if isinstance(report.get("safety"), dict) else {}
    categories = report.get("instruction_categories", [])
    if not isinstance(categories, list):
        categories = []

    return {
        "schema": SUMMARY_SCHEMA,
        "source_schema": report.get("schema", REPORT_SCHEMA),
        "advisory_only": bool(report.get("advisory_only", True)),
        "target": {
            "path": str(target.get("path", DEFAULT_AGENTS_PATH)),
            "exists": bool(target.get("exists", False)),
            "repo_boundary_enforced": bool(target.get("repo_boundary_enforced", True)),
        },
        "budget": {
            "bytes": int(budget.get("bytes", 0) or 0),
            "estimated_tokens": int(budget.get("estimated_tokens", 0) or 0),
            "byte_budget": int(budget.get("byte_budget", DEFAULT_BYTE_BUDGET) or DEFAULT_BYTE_BUDGET),
            "token_budget": int(budget.get("token_budget", DEFAULT_TOKEN_BUDGET) or DEFAULT_TOKEN_BUDGET),
            "remaining_bytes": int(budget.get("remaining_bytes", 0) or 0),
            "remaining_tokens": int(budget.get("remaining_tokens", 0) or 0),
            "status": str(budget.get("status", "unknown")),
        },
        "summary": {
            "ok": bool(summary.get("ok", False)),
            "status": str(summary.get("status", "unknown")),
            "line_count": int(summary.get("line_count", 0) or 0),
            "finding_count": int(summary.get("finding_count", 0) or 0),
            "severity_counts": dict(summary.get("severity_counts", {}) or {}),
            "category_count": int(summary.get("category_count", len(categories)) or 0),
        },
        "instruction_categories": [
            {
                "category": str(row.get("category", "unknown")),
                "line_count": int(row.get("line_count", 0) or 0),
                "bullet_count": int(row.get("bullet_count", 0) or 0),
                "estimated_tokens": int(row.get("estimated_tokens", 0) or 0),
            }
            for row in categories[:max_categories]
            if isinstance(row, dict)
        ],
        "finding_counts": {
            "duplicates": len(report.get("duplicate_guidance", []) if isinstance(report.get("duplicate_guidance"), list) else []),
            "stale": len(report.get("stale_guidance", []) if isinstance(report.get("stale_guidance"), list) else []),
            "risky_global_instructions": len(report.get("risky_global_instructions", []) if isinstance(report.get("risky_global_instructions"), list) else []),
            "move_candidates": len(report.get("move_candidates", []) if isinstance(report.get("move_candidates"), list) else []),
        },
        "safety": {
            "no_network": bool(safety.get("no_network", True)),
            "no_upload": bool(safety.get("no_upload", True)),
            "read_only": bool(safety.get("read_only", True)),
            "repo_boundary_enforced": bool(safety.get("repo_boundary_enforced", True)),
            "content_excerpts_included": bool(safety.get("content_excerpts_included", False)),
            "contains_file_content": bool(safety.get("contains_file_content", False)),
            "redacted": bool(safety.get("redacted", True)),
        },
    }


def _safety_metadata() -> dict[str, Any]:
    return {
        "no_network": True,
        "no_upload": True,
        "read_only": True,
        "repo_boundary_enforced": True,
        "content_excerpts_included": False,
        "contains_file_content": False,
        "redacted": True,
    }
