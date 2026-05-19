# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Deterministic behavioral fuzzing for public MCP tool contracts.

The runner in this module intentionally stays read-only by default.  It calls a
small safe corpus of public tools/router modes directly, validates returned
structured payloads against checked-in contracts where available, and records
replayable findings for contract or security-invariant drift.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from source.tool_output_schemas import (
    ERROR_OUTPUT_SCHEMA,
    TOOL_OUTPUT_SCHEMAS,
    make_tool_error,
    validate_against_schema,
)

REPORT_SCHEMA = "tool_contract_fuzz_report.v1"
FINDING_SCHEMA = "tool_contract_fuzz_finding.v1"
DEFAULT_SEED = 106
SAFE_DEFAULT_CORPUS = "safe-default-read-only.v1"

_SECRET_CANARIES = (
    "ghp_toolfuzz_canary_000000000000000000000000000000000000",
    "sk-toolfuzz-canary-000000000000000000000000000000000000",
    "-----BEGIN PRIVATE KEY-----TOOLFUZZCANARY-----END PRIVATE KEY-----",
)
_SECRET_RE = re.compile(
    r"(?:ghp_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ToolFuzzCase:
    """One replayable behavioral fuzz case."""

    case_id: str
    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)
    expectation: str = "success"
    category: str = "contract"
    read_only: bool = True
    mutation: bool = False
    schema_tool: str | None = None
    expected_error_contains: str | None = None
    allow_absolute_repo_path: bool = False
    notes: str = ""

    def public_surface(self) -> str:
        mode = self.args.get("mode")
        if isinstance(mode, str) and mode:
            return f"{self.tool}:{mode}"
        return self.tool


@dataclass
class _CaseOutcome:
    case: ToolFuzzCase
    passed: bool = True
    findings: list[dict[str, Any]] = field(default_factory=list)
    schema_validated: bool = False
    error_schema_validated: bool = False
    raised: str | None = None


def default_fuzz_corpus(seed: int = DEFAULT_SEED) -> list[ToolFuzzCase]:
    """Return the deterministic safe default corpus.

    The seed controls benign value choices and execution order without changing
    the corpus safety class.  All cases are offline/read-only and bounded.
    """

    rng = random.Random(seed)
    grep_pattern = rng.choice(("def", "class", "README", "alpha"))
    snippet_end = rng.choice((1, 2, 3))
    workflow_prompt = rng.choice(
        (
            "Select a read-only workflow for reviewing changed files.",
            "Select an offline workflow for a release-readiness precheck.",
            "Select a safe workflow for repository documentation review.",
        )
    )
    cases = [
        ToolFuzzCase(
            case_id="repo_info:basic",
            tool="repo_info",
            schema_tool="repo_info",
            category="schema-contract",
            allow_absolute_repo_path=True,
            notes="Schema-backed read-only capability probe.",
        ),
        ToolFuzzCase(
            case_id="git_status:short",
            tool="git_status",
            args={"short": True},
            schema_tool="git_status",
            category="schema-contract",
            notes="Schema-backed git status output.",
        ),
        ToolFuzzCase(
            case_id="find_paths:bounded",
            tool="find_paths",
            args={
                "path": ".",
                "recursive": True,
                "include_hidden": False,
                "max_entries": 25,
                "file_type": "any",
                "output_profile": "compact",
            },
            schema_tool="find_paths",
            category="schema-contract",
            notes="Bounded repository-relative path enumeration.",
        ),
        ToolFuzzCase(
            case_id="grep:bounded_match",
            tool="grep",
            args={
                "pattern": grep_pattern,
                "path": ".",
                "recursive": True,
                "include_hidden": False,
                "max_matches": 10,
                "output_profile": "compact",
                "summary_mode": "full",
            },
            schema_tool="grep",
            category="schema-contract",
            notes="Schema-backed bounded regex search.",
        ),
        ToolFuzzCase(
            case_id="grep:invalid_regex",
            tool="grep",
            args={"pattern": "[", "path": ".", "max_matches": 1},
            expectation="raises",
            schema_tool="grep",
            expected_error_contains="invalid regex pattern",
            category="error-path-heavy",
            notes="Representative error-heavy path with malformed regex input.",
        ),
        ToolFuzzCase(
            case_id="read_snippet:readme_head",
            tool="read_snippet",
            args={
                "path": "README.md",
                "start_line": 1,
                "end_line": snippet_end,
                "context_before": 0,
                "context_after": 0,
                "output_profile": "compact",
            },
            schema_tool="read_snippet",
            category="schema-contract",
            notes="Schema-backed focused read with bounded line range.",
        ),
        ToolFuzzCase(
            case_id="task_router:workflow_select",
            tool="task_router",
            args={
                "mode": "workflow_select",
                "prompt": workflow_prompt,
                "execution_mode": "offline",
                "top_k": 2,
                "output_profile": "compact",
            },
            category="router-mode",
            notes="Read-only workflow-card router mode.",
        ),
        ToolFuzzCase(
            case_id="quality_router:required_tool_chain_missing",
            tool="quality_router",
            args={
                "mode": "required_tool_chain",
                "required_tools": ["repo_info"],
                "required_artifacts": [],
                "required_result_ids": [],
                "require_order": False,
                "max_age_minutes": 60,
            },
            category="router-mode",
            notes="Read-only quality router mode that reports missing telemetry without failing the fuzz case.",
        ),
    ]
    rng.shuffle(cases)
    return cases


def run_tool_contract_fuzz(
    *,
    seed: int = DEFAULT_SEED,
    server_module: Any | None = None,
    cases: Sequence[ToolFuzzCase] | None = None,
    max_cases: int | None = None,
    include_mutations: bool = False,
    mutation_snapshot_label: str | None = None,
) -> dict[str, Any]:
    """Run deterministic behavioral fuzzing and return a JSON-safe report."""

    if include_mutations and not mutation_snapshot_label:
        raise ValueError(
            "write-mode fuzzing requires an explicit mutation snapshot label; "
            "default behavioral fuzzing is read-only"
        )

    if server_module is None:
        from source import server as server_module  # type: ignore[no-redef]

    selected = list(cases if cases is not None else default_fuzz_corpus(seed))
    if max_cases is not None:
        if max_cases < 1:
            raise ValueError("max_cases must be >= 1")
        selected = selected[:max_cases]

    outcomes: list[_CaseOutcome] = []
    findings: list[dict[str, Any]] = []
    schema_validation_count = 0
    error_schema_validation_count = 0
    skipped_mutation_cases = 0
    mutation_snapshot: dict[str, Any] | None = None
    mutation_restore: dict[str, Any] | None = None
    mutation_cases_requested = any(case.mutation for case in selected)

    if include_mutations and mutation_cases_requested:
        _assert_mutation_gate(server_module, mutation_snapshot_label)
        mutation_snapshot = _begin_mutation_snapshot(
            server_module, str(mutation_snapshot_label)
        )

    try:
        for case in selected:
            if case.mutation and not include_mutations:
                skipped_mutation_cases += 1
                continue
            if case.mutation:
                _assert_mutation_gate(server_module, mutation_snapshot_label)
            outcome = _run_case(server_module, case, seed)
            outcomes.append(outcome)
            findings.extend(outcome.findings)
            if outcome.schema_validated:
                schema_validation_count += 1
            if outcome.error_schema_validated:
                error_schema_validation_count += 1
    finally:
        if mutation_snapshot:
            mutation_restore = _restore_mutation_snapshot(server_module, mutation_snapshot)

    covered_surfaces = sorted({outcome.case.public_surface() for outcome in outcomes})
    covered_schema_tools = sorted(
        {
            str(outcome.case.schema_tool)
            for outcome in outcomes
            if outcome.case.schema_tool and outcome.schema_validated
        }
    )
    error_path_cases = sorted(
        outcome.case.case_id
        for outcome in outcomes
        if outcome.case.expectation == "raises" or "error" in outcome.case.category
    )

    return {
        "schema": REPORT_SCHEMA,
        "ok": not findings,
        "seed": seed,
        "corpus": SAFE_DEFAULT_CORPUS,
        "safe_default": True,
        "mutation": {
            "enabled": include_mutations,
            "snapshot_label": mutation_snapshot_label or "",
            "snapshot": _preview(mutation_snapshot) if mutation_snapshot else {},
            "restore": _preview(mutation_restore) if mutation_restore else {},
            "skipped_cases": skipped_mutation_cases,
            "gate": "write-mode cases require include_mutations plus mutation_snapshot_label and workspace_transaction snapshot/restore",
        },
        "summary": {
            "case_count": len(outcomes),
            "passed": sum(1 for outcome in outcomes if outcome.passed),
            "failed": sum(1 for outcome in outcomes if not outcome.passed),
            "finding_count": len(findings),
            "schema_validations": schema_validation_count,
            "error_schema_validations": error_schema_validation_count,
            "covered_public_surfaces": len(covered_surfaces),
        },
        "coverage": {
            "public_surfaces": covered_surfaces,
            "schema_backed_tools": covered_schema_tools,
            "error_path_heavy_cases": error_path_cases,
            "meets_initial_acceptance": len(covered_surfaces) >= 5
            and bool(covered_schema_tools)
            and bool(error_path_cases),
        },
        "cases": [_case_report(outcome) for outcome in outcomes],
        "findings": findings,
    }


def _run_case(server_module: Any, case: ToolFuzzCase, seed: int) -> _CaseOutcome:
    outcome = _CaseOutcome(case=case)
    try:
        tool = getattr(server_module, case.tool)
    except AttributeError as exc:
        finding = _finding(
            case=case,
            seed=seed,
            category="contract.missing_tool",
            expected={"behavior": "public tool is callable", "tool": case.tool},
            actual={"behavior": "missing", "error": str(exc)},
        )
        outcome.passed = False
        outcome.findings.append(finding)
        return outcome

    returned = None
    raised: Exception | None = None
    try:
        returned = tool(**dict(case.args))
        if inspect.isawaitable(returned):
            returned = _run_awaitable(returned)
    except Exception as exc:  # noqa: BLE001 - fuzz runner must capture all behavior.
        raised = exc
        outcome.raised = type(exc).__name__

    if case.expectation == "raises":
        if raised is None:
            outcome.passed = False
            outcome.findings.append(
                _finding(
                    case=case,
                    seed=seed,
                    category="contract.expected_error_missing",
                    expected={
                        "behavior": "raises",
                        "error_contains": case.expected_error_contains or "",
                    },
                    actual={"behavior": "returned", "value": _preview(returned)},
                )
            )
        else:
            message = str(raised)
            if case.expected_error_contains and case.expected_error_contains not in message:
                outcome.passed = False
                outcome.findings.append(
                    _finding(
                        case=case,
                        seed=seed,
                        category="contract.unexpected_error",
                        expected={
                            "behavior": "raises",
                            "error_contains": case.expected_error_contains,
                        },
                        actual={
                            "behavior": f"raised {type(raised).__name__}",
                            "message": _redact_text(message),
                        },
                    )
                )
            if case.schema_tool:
                try:
                    validate_against_schema(
                        make_tool_error(case.schema_tool, raised), ERROR_OUTPUT_SCHEMA
                    )
                    outcome.error_schema_validated = True
                except AssertionError as exc:
                    outcome.passed = False
                    outcome.findings.append(
                        _finding(
                            case=case,
                            seed=seed,
                            category="contract.error_schema_validation",
                            expected={"behavior": "documented error envelope validates"},
                            actual={"behavior": "validation failed", "message": str(exc)},
                        )
                    )
            _check_redaction_invariants(
                outcome=outcome,
                case=case,
                seed=seed,
                value={"error": type(raised).__name__, "message": message},
                server_module=server_module,
            )
        return outcome

    if raised is not None:
        outcome.passed = False
        outcome.findings.append(
            _finding(
                case=case,
                seed=seed,
                category="contract.unexpected_exception",
                expected={"behavior": "success"},
                actual={
                    "behavior": f"raised {type(raised).__name__}",
                    "message": _redact_text(str(raised)),
                },
            )
        )
        _check_redaction_invariants(
            outcome=outcome,
            case=case,
            seed=seed,
            value={"error": type(raised).__name__, "message": str(raised)},
            server_module=server_module,
        )
        return outcome

    if case.schema_tool:
        schema = TOOL_OUTPUT_SCHEMAS.get(case.schema_tool)
        if schema is None:
            outcome.passed = False
            outcome.findings.append(
                _finding(
                    case=case,
                    seed=seed,
                    category="contract.missing_schema",
                    expected={"behavior": "checked-in output schema exists"},
                    actual={"behavior": "missing", "schema_tool": case.schema_tool},
                )
            )
        else:
            try:
                validate_against_schema(returned, schema)
                outcome.schema_validated = True
            except AssertionError as exc:
                outcome.passed = False
                outcome.findings.append(
                    _finding(
                        case=case,
                        seed=seed,
                        category="contract.schema_validation",
                        expected={
                            "behavior": "structured output validates",
                            "schema_tool": case.schema_tool,
                        },
                        actual={"behavior": "validation failed", "message": str(exc)},
                    )
                )

    _check_redaction_invariants(
        outcome=outcome,
        case=case,
        seed=seed,
        value=returned,
        server_module=server_module,
    )
    return outcome


def _run_awaitable(awaitable: Any) -> Any:
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    if loop.is_running():
        raise RuntimeError("tool contract fuzzer cannot run awaitable inside an active event loop")
    return loop.run_until_complete(awaitable)


def _assert_mutation_gate(server_module: Any, snapshot_label: str | None) -> None:
    if not bool(getattr(server_module, "ALLOW_MUTATIONS", False)):
        raise ValueError("write-mode fuzzing requires server ALLOW_MUTATIONS=true")
    if not snapshot_label:
        raise ValueError("write-mode fuzzing requires a mutation snapshot label")
    if not hasattr(server_module, "workspace_transaction"):
        raise ValueError("write-mode fuzzing requires workspace_transaction snapshot support")


def _begin_mutation_snapshot(server_module: Any, snapshot_label: str) -> dict[str, Any]:
    snapshot = server_module.workspace_transaction(mode="snapshot", label=snapshot_label)
    snapshot_id = _snapshot_id(snapshot)
    if not snapshot_id:
        raise ValueError("write-mode fuzzing could not create a workspace snapshot")
    return snapshot


def _restore_mutation_snapshot(server_module: Any, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    snapshot_id = _snapshot_id(snapshot)
    if not snapshot_id:
        return {"ok": False, "error": "missing snapshot_id"}
    try:
        return server_module.workspace_transaction(mode="restore", snapshot_id=snapshot_id)
    except Exception as exc:  # noqa: BLE001 - fuzz cleanup must report restore failures.
        return {"ok": False, "error": type(exc).__name__, "message": _redact_text(str(exc))}


def _snapshot_id(snapshot: Mapping[str, Any]) -> str:
    result = snapshot.get("result")
    if isinstance(result, Mapping):
        value = result.get("snapshot_id")
        if isinstance(value, str) and value:
            return value
    value = snapshot.get("snapshot_id")
    return value if isinstance(value, str) else ""


def _check_redaction_invariants(
    *,
    outcome: _CaseOutcome,
    case: ToolFuzzCase,
    seed: int,
    value: Any,
    server_module: Any,
) -> None:
    serialized = _json_preview(value, limit=20000)
    repo_path = str(getattr(server_module, "REPO_PATH", "") or "")
    if repo_path and not case.allow_absolute_repo_path and repo_path in serialized:
        outcome.passed = False
        outcome.findings.append(
            _finding(
                case=case,
                seed=seed,
                category="security.redaction.absolute_repo_path",
                security_category="redaction-invariant",
                expected={"behavior": "output omits host absolute repository path"},
                actual={"behavior": "absolute repository path was present"},
            )
        )
    secret_hits = [canary for canary in _SECRET_CANARIES if canary in serialized]
    regex_hit = _SECRET_RE.search(serialized)
    if secret_hits or regex_hit:
        outcome.passed = False
        outcome.findings.append(
            _finding(
                case=case,
                seed=seed,
                category="security.redaction.secret_canary",
                security_category="redaction-invariant",
                expected={"behavior": "output omits unredacted secret-looking canaries"},
                actual={"behavior": "secret-looking value was present"},
            )
        )


def _case_report(outcome: _CaseOutcome) -> dict[str, Any]:
    return {
        "case_id": outcome.case.case_id,
        "tool": outcome.case.tool,
        "surface": outcome.case.public_surface(),
        "category": outcome.case.category,
        "read_only": outcome.case.read_only,
        "mutation": outcome.case.mutation,
        "expectation": outcome.case.expectation,
        "schema_tool": outcome.case.schema_tool or "",
        "passed": outcome.passed,
        "schema_validated": outcome.schema_validated,
        "error_schema_validated": outcome.error_schema_validated,
        "raised": outcome.raised or "",
        "finding_ids": [finding["id"] for finding in outcome.findings],
    }


def _finding(
    *,
    case: ToolFuzzCase,
    seed: int,
    category: str,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    security_category: str = "contract-invariant",
) -> dict[str, Any]:
    replay_args = _minimize_args(case.args)
    raw = json.dumps(
        {"seed": seed, "case_id": case.case_id, "tool": case.tool, "category": category},
        sort_keys=True,
    )
    finding_id = "TFZ-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    prompt = replay_args.get("prompt", "") if isinstance(replay_args, dict) else ""
    return {
        "schema": FINDING_SCHEMA,
        "id": finding_id,
        "tool": case.tool,
        "case_id": case.case_id,
        "seed": seed,
        "category": category,
        "contract_category": category.split(".", 1)[0],
        "security_category": security_category,
        "repro": {
            "runner": "scripts/tool_contract_fuzzer.py",
            "seed": seed,
            "case_id": case.case_id,
            "tool": case.tool,
            "args": replay_args,
            "prompt": prompt,
        },
        "expected": _json_sanitize(expected),
        "actual": _json_sanitize(actual),
        "minimized_replay": {
            "schema": "tool_contract_fuzz_replay.v1",
            "seed": seed,
            "case_id": case.case_id,
            "tool": case.tool,
            "args": replay_args,
            "expectation": case.expectation,
        },
    }


def _minimize_args(args: Mapping[str, Any]) -> dict[str, Any]:
    return _json_sanitize(copy.deepcopy(dict(args)), string_limit=500, list_limit=20)


def _json_sanitize(value: Any, *, string_limit: int = 1000, list_limit: int = 50) -> Any:
    if isinstance(value, str):
        text = _redact_text(value)
        if len(text) > string_limit:
            return text[:string_limit] + "...<truncated>"
        return text
    if isinstance(value, Mapping):
        return {
            str(key): _json_sanitize(item, string_limit=string_limit, list_limit=list_limit)
            for key, item in value.items()
        }
    if isinstance(value, list):
        items = [
            _json_sanitize(item, string_limit=string_limit, list_limit=list_limit)
            for item in value[:list_limit]
        ]
        if len(value) > list_limit:
            items.append(f"...<{len(value) - list_limit} more items>")
        return items
    if isinstance(value, tuple):
        return _json_sanitize(list(value), string_limit=string_limit, list_limit=list_limit)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _redact_text(str(value))


def _redact_text(text: str) -> str:
    redacted = text
    for canary in _SECRET_CANARIES:
        redacted = redacted.replace(canary, "<redacted:secret-canary>")
    return _SECRET_RE.sub("<redacted:secret-like-value>", redacted)


def _preview(value: Any) -> Any:
    return _json_sanitize(value, string_limit=300, list_limit=5)


def _json_preview(value: Any, *, limit: int) -> str:
    try:
        rendered = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        rendered = str(value)
    return rendered[:limit]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic ToolFuzz-style behavioral fuzzing for MCP tool contracts."
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument(
        "--include-mutations",
        action="store_true",
        help="Include write-mode cases. Requires --mutation-snapshot-label.",
    )
    parser.add_argument(
        "--mutation-snapshot-label",
        default="",
        help="Explicit snapshot label required before write-mode fuzz cases run.",
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=Path.cwd(),
        help="Repository root used for direct tool calls; defaults to the current directory.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    from source import server

    server.REPO_PATH = args.repo_path.resolve()
    report = run_tool_contract_fuzz(
        seed=args.seed,
        server_module=server,
        max_cases=args.max_cases,
        include_mutations=args.include_mutations,
        mutation_snapshot_label=args.mutation_snapshot_label or None,
    )
    rendered = json.dumps(
        report,
        indent=2 if args.pretty else None,
        sort_keys=True,
        ensure_ascii=False,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if report["ok"] else 1
