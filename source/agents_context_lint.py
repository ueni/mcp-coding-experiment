# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Read-only AGENTS.md context-health lint and routing-effectiveness checks."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORT_SCHEMA = "agents_context_health_report.v1"
EFFECTIVENESS_FIXTURE_SCHEMA = "agents_context_effectiveness_fixture_set.v1"
EFFECTIVENESS_REPORT_SCHEMA = "agents_context_effectiveness_report.v1"

DEFAULT_CONTEXT_FILES = ("AGENTS.md",)
DEFAULT_MAX_BYTES = 12_000
DEFAULT_MAX_ESTIMATED_TOKENS = 3_000
DEFAULT_MAX_CONTEXT_TOKENS = 140
DEFAULT_TOP_K = 3

KNOWN_PUBLIC_TOOL_REFS = {
    "artifact_provenance",
    "change_impact_gate",
    "clarification_gate",
    "dependency_security_report",
    "find_paths",
    "git_status",
    "governance_report",
    "governance_router",
    "quality_router",
    "read_snippet",
    "release_readiness",
    "repo_info",
    "risk_scoring",
    "self_optimization_report",
    "summarize_diff",
    "task_router",
    "tool_catalog_integrity",
    "workspace_transaction",
    "workflow_lineage",
    "workflow_policy_plan",
    "workflow_task",
}

SAFETY_TERMS = {
    "allow_mutations",
    "auth",
    "authorization",
    "bearer",
    "credential",
    "host access",
    "http",
    "mutation",
    "private key",
    "privileged",
    "rollback",
    "sandbox",
    "secret",
    "snapshot",
    "token",
}
WORKFLOW_TERMS = {
    "mode=",
    "prompt workflow",
    "quality_router",
    "release_readiness",
    "router",
    "task_router",
    "tool",
    "workflow",
    "workspace_transaction",
}
CODING_TERMS = {
    "branch",
    "change",
    "commit",
    "diff",
    "git",
    "pr",
    "pytest",
    "test",
}
OPTIONAL_TERMS = {"background", "docs", "documentation", "generated", "overview", "product", "scope"}

GLOBAL_DIRECTIVE_RE = re.compile(
    r"\b(MUST|ALWAYS|NEVER|DO NOT|DON'T|REQUIRED|SHOULD)\b", re.IGNORECASE
)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
TOOL_CALL_RE = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]+)(?:\(|`)")
SECRET_LITERAL_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"\bgh[opsu]_[A-Za-z0-9_]{30,}\b|"
    r"\bsk-[A-Za-z0-9_-]{24,}\b|"
    r"Authorization:\s*Bearer\s+(?!\$|<|\{|\.\.\.)[A-Za-z0-9._~+/=-]{12,}|"
    r"MCP_HTTP_BEARER_TOKEN\s*=\s*['\"]?(?!\$|<|\{|\.\.\.)[A-Za-z0-9._~+/=-]{12,}",
    re.IGNORECASE,
)

RouteFn = Callable[..., dict[str, Any]]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _estimate_tokens(text: str) -> int:
    # Conservative, dependency-free estimate used only for budget tracking.
    return max(1, (len(text) + 3) // 4) if text else 0


def _repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("context file path must stay inside repository root") from exc


def _resolve_repo_path(repo_root: Path, rel_path: str) -> Path:
    if not rel_path or Path(rel_path).is_absolute():
        raise ValueError("context file paths must be repository-relative")
    root = repo_root.resolve()
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("context file path must stay inside repository root") from exc
    return candidate


def _line_classification(line: str) -> str:
    lowered = line.lower()
    if any(term in lowered for term in SAFETY_TERMS):
        return "safety-critical"
    if any(term in lowered for term in WORKFLOW_TERMS):
        return "workflow-routing"
    if any(term in lowered for term in CODING_TERMS):
        return "coding-style"
    if any(term in lowered for term in OPTIONAL_TERMS):
        return "optional-background"
    return "optional-background"


def _instruction_lines(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--") or stripped.startswith("#"):
            continue
        if stripped.startswith(("- ", "* ")) or re.match(r"^\d+\.\s", stripped):
            rows.append({"line": index, "class": _line_classification(stripped)})
    return rows


def _normalize_instruction(line: str) -> str:
    line = re.sub(r"\[[^\]]+\]\([^)]+\)", "", line.lower())
    line = re.sub(r"`[^`]+`", "", line)
    line = re.sub(r"[^a-z0-9]+", " ", line)
    return " ".join(token for token in line.split() if len(token) > 2)


def _duplicate_guidance(path: str, lines: list[str]) -> list[dict[str, Any]]:
    normalized: dict[str, list[int]] = defaultdict(list)
    concepts: dict[str, list[int]] = defaultdict(list)
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped.startswith(("- ", "* ")) and not re.match(r"^\d+\.\s", stripped):
            continue
        key = _normalize_instruction(stripped)
        if key:
            normalized[key].append(index)
        lowered = stripped.lower()
        for concept, terms in {
            "task-router-routing": ("task_router", "workflow_select"),
            "quality-gates": ("quality_router", "test", "gate"),
            "mutation-rollback": ("mutation", "snapshot", "rollback"),
            "secrets-auth": ("secret", "token", "authorization", "bearer"),
            "canonical-docs": ("readme.md", "docs/", "canonical"),
        }.items():
            if any(term in lowered for term in terms):
                concepts[concept].append(index)
    duplicates: list[dict[str, Any]] = []
    for key, line_numbers in sorted(normalized.items()):
        if len(line_numbers) > 1:
            duplicates.append(
                {
                    "path": path,
                    "kind": "exact_or_near_duplicate_instruction",
                    "line_numbers": line_numbers[:12],
                    "count": len(line_numbers),
                    "recommendation": "Consolidate repeated always-on guidance or move detail behind canonical docs.",
                }
            )
    for concept, line_numbers in sorted(concepts.items()):
        if len(line_numbers) >= 4:
            duplicates.append(
                {
                    "path": path,
                    "kind": "repeated_concept",
                    "concept": concept,
                    "line_numbers": sorted(set(line_numbers))[:16],
                    "count": len(set(line_numbers)),
                    "recommendation": "Keep only the decision aid in AGENTS.md; route deeper detail to docs or MCP workflow cards.",
                }
            )
    return duplicates


def _stale_guidance(path: str, text: str, file_path: Path, repo_root: Path) -> dict[str, Any]:
    missing_links: list[dict[str, Any]] = []
    checked_links = 0
    for match in MARKDOWN_LINK_RE.finditer(text):
        target = match.group(1).split("#", 1)[0]
        if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue
        checked_links += 1
        candidate = (file_path.parent / target).resolve()
        try:
            rel = candidate.relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            missing_links.append({"path": path, "target": "<outside-repository>", "reason": "outside_repo_boundary"})
            continue
        if not candidate.exists():
            missing_links.append({"path": path, "target": rel, "reason": "missing_relative_link"})

    tool_refs = sorted(set(TOOL_CALL_RE.findall(text)))
    unknown_tool_refs = [
        {"path": path, "tool": name, "reason": "not_in_known_public_tool_allowlist"}
        for name in tool_refs
        if name.endswith(("_router", "_report", "_gate")) or name in {"task_router", "quality_router"}
        if name not in KNOWN_PUBLIC_TOOL_REFS
    ]
    return {
        "checked_relative_link_count": checked_links,
        "missing_links": missing_links,
        "unknown_tool_references": unknown_tool_refs,
    }


def _directive_findings(path: str, lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        if not GLOBAL_DIRECTIVE_RE.search(line):
            continue
        classification = _line_classification(line)
        severity = "info" if classification == "safety-critical" else "warning"
        findings.append(
            {
                "id": f"global-directive:{path}:{index}",
                "path": path,
                "line": index,
                "kind": "broad_global_instruction",
                "severity": severity,
                "instruction_class": classification,
                "signal": GLOBAL_DIRECTIVE_RE.search(line).group(1).upper(),
                "recommendation": (
                    "Keep if safety-critical; otherwise prefer task-specific workflow routing, prompts, or docs."
                ),
            }
        )
    return findings


def _candidate_moves(path: str, instructions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in instructions:
        instruction_class = str(row.get("class", ""))
        line = int(row.get("line", 0) or 0)
        if instruction_class == "optional-background":
            candidates.append(
                {
                    "path": path,
                    "line": line,
                    "instruction_class": instruction_class,
                    "target": "docs/index.md or README.md",
                    "reason": "background context is useful but usually not needed in every agent prompt",
                }
            )
        elif instruction_class == "workflow-routing":
            candidates.append(
                {
                    "path": path,
                    "line": line,
                    "instruction_class": instruction_class,
                    "target": "task_router(mode='workflow_select') or workflow cards",
                    "reason": "detailed routing choices can live behind the read-only selector",
                }
            )
    return candidates[:20]


def analyze_agents_context(
    repo_root: str | Path | None = None,
    *,
    context_files: Iterable[str] = DEFAULT_CONTEXT_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_estimated_tokens: int = DEFAULT_MAX_ESTIMATED_TOKENS,
) -> dict[str, Any]:
    """Analyze repository-owned always-on context files without mutating files."""

    root = Path(repo_root or Path.cwd()).resolve()
    generated_at = _now_iso()
    files: list[dict[str, Any]] = []
    all_findings: list[dict[str, Any]] = []
    all_duplicates: list[dict[str, Any]] = []
    missing_links: list[dict[str, Any]] = []
    unknown_tool_refs: list[dict[str, Any]] = []
    candidate_moves: list[dict[str, Any]] = []
    class_counts: Counter[str] = Counter()
    secret_literal_count = 0
    total_bytes = 0
    total_tokens = 0

    for rel_path in context_files:
        file_path = _resolve_repo_path(root, rel_path)
        display_path = _repo_relative(file_path, root)
        if not file_path.exists():
            all_findings.append(
                {
                    "id": f"missing-context-file:{display_path}",
                    "path": display_path,
                    "kind": "missing_context_file",
                    "severity": "warning",
                    "recommendation": "Keep AGENTS.md as the repository-owned coding-agent entrypoint.",
                }
            )
            continue
        text = file_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        byte_count = len(text.encode("utf-8"))
        token_estimate = _estimate_tokens(text)
        total_bytes += byte_count
        total_tokens += token_estimate
        instruction_rows = _instruction_lines(text)
        class_counts.update(str(row["class"]) for row in instruction_rows)
        directives = _directive_findings(display_path, lines)
        duplicates = _duplicate_guidance(display_path, lines)
        stale = _stale_guidance(display_path, text, file_path, root)
        secrets = len(SECRET_LITERAL_RE.findall(text))
        secret_literal_count += secrets
        if secrets:
            all_findings.append(
                {
                    "id": f"secret-literal:{display_path}",
                    "path": display_path,
                    "kind": "possible_secret_literal",
                    "severity": "error",
                    "count": secrets,
                    "recommendation": "Remove literal secrets/tokens; keep placeholders or environment-variable names only.",
                }
            )
        if byte_count > max_bytes or token_estimate > max_estimated_tokens:
            all_findings.append(
                {
                    "id": f"budget:{display_path}",
                    "path": display_path,
                    "kind": "context_budget_exceeded",
                    "severity": "warning",
                    "bytes": byte_count,
                    "estimated_tokens": token_estimate,
                    "recommendation": "Shorten AGENTS.md and move detail behind docs, prompts, or task_router workflow selection.",
                }
            )
        all_findings.extend(directives)
        all_duplicates.extend(duplicates)
        missing_links.extend(stale["missing_links"])
        unknown_tool_refs.extend(stale["unknown_tool_references"])
        candidate_moves.extend(_candidate_moves(display_path, instruction_rows))
        files.append(
            {
                "path": display_path,
                "exists": True,
                "bytes": byte_count,
                "estimated_tokens": token_estimate,
                "line_count": len(lines),
                "instruction_count": len(instruction_rows),
                "classification_counts": dict(Counter(str(row["class"]) for row in instruction_rows)),
                "stale_guidance": stale,
            }
        )

    stale_finding_count = len(missing_links) + len(unknown_tool_refs)
    warning_count = sum(1 for finding in all_findings if finding.get("severity") == "warning")
    error_count = sum(1 for finding in all_findings if finding.get("severity") == "error")
    status = "clean"
    if error_count:
        status = "error"
    elif warning_count or all_duplicates or stale_finding_count:
        status = "findings"

    return {
        "schema": REPORT_SCHEMA,
        "generated_at": generated_at,
        "read_only": True,
        "advisory_only": True,
        "status": status,
        "ok": error_count == 0,
        "summary": {
            "context_file_count": len(files),
            "total_bytes": total_bytes,
            "total_estimated_tokens": total_tokens,
            "max_bytes": max_bytes,
            "max_estimated_tokens": max_estimated_tokens,
            "budget_ok": total_bytes <= max_bytes and total_tokens <= max_estimated_tokens,
            "finding_count": len(all_findings),
            "warning_count": warning_count,
            "error_count": error_count,
            "duplicate_finding_count": len(all_duplicates),
            "stale_guidance_finding_count": stale_finding_count,
            "secret_literal_count": secret_literal_count,
        },
        "classification": {
            "safety-critical": {"count": class_counts.get("safety-critical", 0)},
            "workflow-routing": {"count": class_counts.get("workflow-routing", 0)},
            "coding-style": {"count": class_counts.get("coding-style", 0)},
            "optional-background": {"count": class_counts.get("optional-background", 0)},
        },
        "files": files,
        "duplicates": all_duplicates,
        "stale_guidance": {
            "missing_links": missing_links,
            "unknown_tool_references": unknown_tool_refs,
        },
        "findings": all_findings,
        "candidate_moves": candidate_moves[:30],
        "security": {
            "network_access": False,
            "records_file_contents": False,
            "records_absolute_host_paths": False,
            "repo_boundary_enforced": True,
            "redaction": "The report stores counts, line numbers, relative paths, and identifiers only; it does not embed AGENTS.md text.",
        },
    }


def minimal_context_summary(report: dict[str, Any] | None = None) -> str:
    """Return a compact, task-neutral summary for routing regression checks."""

    token_note = ""
    if isinstance(report, dict):
        summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
        if summary.get("total_estimated_tokens") is not None:
            token_note = f" Estimated always-on context budget: {summary.get('total_estimated_tokens')} tokens."
    return (
        "Always-on repository context summary: codebase-tooling-mcp is a repository-scoped MCP server. "
        "Start with read-only inspection, keep secrets and host paths out of outputs, use task_router workflow selection when unsure, "
        "use quality_router for verification gates, and create a snapshot or normal Git rollback point before risky mutation."
        f"{token_note} The user task follows."
    )


def _load_task_router() -> RouteFn:
    from source import server

    return server.task_router


def _top_workflow_card(selection: dict[str, Any]) -> str:
    matches = selection.get("matches", [])
    if matches and isinstance(matches[0], dict):
        return str(matches[0].get("id") or "")
    return ""


def load_effectiveness_fixtures(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != EFFECTIVENESS_FIXTURE_SCHEMA:
        raise ValueError(f"fixture set schema must be {EFFECTIVENESS_FIXTURE_SCHEMA}")
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list) or len(fixtures) < 3:
        raise ValueError("effectiveness fixture set requires at least three fixtures")
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise ValueError("each fixture must be an object")
        for field in ("id", "prompt", "expected_top_workflow_card"):
            if not str(fixture.get(field) or "").strip():
                raise ValueError(f"fixture missing required field: {field}")
    return payload


def evaluate_context_effectiveness(
    fixture_path: str | Path,
    *,
    repo_root: str | Path | None = None,
    route_fn: RouteFn | None = None,
    context_summary: str | None = None,
) -> dict[str, Any]:
    """Compare workflow selection with and without a minimal always-on context summary."""

    fixture_set = load_effectiveness_fixtures(fixture_path)
    lint_report = analyze_agents_context(repo_root or Path.cwd())
    summary_text = context_summary or str(fixture_set.get("minimal_context_summary") or minimal_context_summary(lint_report))
    top_k = int(fixture_set.get("default_top_k") or DEFAULT_TOP_K)
    route = route_fn or _load_task_router()
    results: list[dict[str, Any]] = []
    for fixture in fixture_set["fixtures"]:
        prompt = str(fixture["prompt"])
        expected = str(fixture["expected_top_workflow_card"])
        execution_mode = str(fixture.get("execution_mode") or "auto")
        baseline = route(mode="workflow_select", prompt=prompt, top_k=top_k, execution_mode=execution_mode)
        with_context = route(
            mode="workflow_select",
            prompt=f"{summary_text}\n\n{prompt}",
            top_k=top_k,
            execution_mode=execution_mode,
        )
        baseline_top = _top_workflow_card(baseline)
        with_context_top = _top_workflow_card(with_context)
        results.append(
            {
                "id": fixture["id"],
                "expected_top_workflow_card": expected,
                "baseline_top_workflow_card": baseline_top,
                "with_context_top_workflow_card": with_context_top,
                "baseline_match": baseline_top == expected,
                "with_context_match": with_context_top == expected,
                "routing_preserved": baseline_top == with_context_top,
                "context_token_delta_estimate": _estimate_tokens(summary_text),
            }
        )
    fixture_count = len(results)
    baseline_accuracy = sum(1 for row in results if row["baseline_match"]) / fixture_count
    with_context_accuracy = sum(1 for row in results if row["with_context_match"]) / fixture_count
    preserved_accuracy = sum(1 for row in results if row["routing_preserved"]) / fixture_count
    thresholds = {
        "min_baseline_top_workflow_card_accuracy": 1.0,
        "min_with_context_top_workflow_card_accuracy": 1.0,
        "min_routing_preservation": 1.0,
        "max_minimal_context_estimated_tokens": DEFAULT_MAX_CONTEXT_TOKENS,
        **fixture_set.get("thresholds", {}),
    }
    context_tokens = _estimate_tokens(summary_text)
    passed = (
        baseline_accuracy >= float(thresholds["min_baseline_top_workflow_card_accuracy"])
        and with_context_accuracy >= float(thresholds["min_with_context_top_workflow_card_accuracy"])
        and preserved_accuracy >= float(thresholds["min_routing_preservation"])
        and context_tokens <= int(thresholds["max_minimal_context_estimated_tokens"])
    )
    return {
        "schema": EFFECTIVENESS_REPORT_SCHEMA,
        "generated_at": _now_iso(),
        "read_only": True,
        "advisory_only": True,
        "target": "task_router(mode='workflow_select')",
        "fixture_schema": EFFECTIVENESS_FIXTURE_SCHEMA,
        "summary": {
            "fixture_count": fixture_count,
            "minimal_context_estimated_tokens": context_tokens,
            "baseline_top_workflow_card_accuracy": round(baseline_accuracy, 4),
            "with_context_top_workflow_card_accuracy": round(with_context_accuracy, 4),
            "routing_preservation": round(preserved_accuracy, 4),
            "passed_thresholds": passed,
        },
        "thresholds": thresholds,
        "results": results,
        "security": {
            "network_access": False,
            "records_file_contents": False,
            "records_absolute_host_paths": False,
            "repo_boundary_enforced": True,
        },
    }
