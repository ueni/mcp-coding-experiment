# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Schema-first MCP output contracts for agent-critical tools.

The schemas in this module are intentionally conservative: they describe stable
fields clients may rely on while allowing selected experimental fields through
``additionalProperties``.  Tool implementations can continue to return richer
payloads without breaking clients that validate against these contracts.
"""

from __future__ import annotations

import json
from typing import Any

SCHEMA_VERSION = "tool_output_contracts.v1"


SCHEMA_BACKED_TOOL_NAMES: tuple[str, ...] = (
    "repo_info",
    "roots_diagnostics",
    "runtime_state",
    "git_status",
    "grep",
    "find_paths",
    "read_snippet",
    "summarize_diff",
    "risk_scoring",
    "workspace_transaction",
    "policy_simulator",
    "clarification_gate",
    "release_readiness",
    "dependency_security_report",
    "governance_report",
    "self_optimization_report",
    "artifact_provenance",
    "workflow_diagnostics",
    "workflow_lineage",
    "interaction_invariant_audit",
)

STABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "repo_info": ("repo_path", "repo_exists", "is_git_repo", "allow_mutations", "transport"),
    "roots_diagnostics": ("schema", "read_only", "advisory_only", "server_repo", "fetch", "roots", "relationship", "guidance"),
    "runtime_state": ("schema", "timestamp", "transport", "server", "sse", "ollama", "docker", "dependency_locks"),
    "git_status": ("status", "short"),
    "grep": ("path", "line", "column", "match"),
    "find_paths": ("<array item: repository-relative path>",),
    "read_snippet": ("path", "start_line", "end_line", "content"),
    "summarize_diff": ("file_count", "total_added", "total_deleted", "risk_flags"),
    "risk_scoring": ("risk_score", "risk_level", "reasons", "summary"),
    "workspace_transaction": ("schema", "mode", "result"),
    "policy_simulator": ("schema", "ok", "blocking_policies", "docs", "security", "risk", "license"),
    "clarification_gate": ("schema", "ok_to_continue", "status", "missing_fields", "questions", "fallback_checklist", "elicitation"),
    "release_readiness": ("schema", "base_ref", "head_ref", "ok", "checks"),
    "dependency_security_report": ("schema", "report_id", "generated_at", "status", "ok", "summary", "components", "vulnerabilities", "advisory", "gate", "exports", "resource_links"),
    "governance_report": ("schema", "report_id", "generated_at", "audit", "governance_hooks", "exports", "resource_links"),
    "self_optimization_report": ("schema", "report_id", "generated_at", "window", "summary", "metrics", "optimization_candidates", "security"),
    "artifact_provenance": ("schema", "provenance_schema", "attestation_schema", "artifact_count", "ok", "checks"),
    "workflow_diagnostics": ("schema", "ok", "critical_step_candidate", "failure_category", "evidence", "safe_next_actions", "redactions_applied"),
    "workflow_lineage": ("schema", "read_only", "manifest_path", "plan_id", "status", "ok", "checks", "conditions"),
    "interaction_invariant_audit": ("schema", "read_only", "advisory_only", "ok_to_continue", "confidence", "extracted_invariants", "suspected_smells", "safe_next_actions", "linked_gates"),
}

EXPERIMENTAL_FIELDS: dict[str, tuple[str, ...]] = {
    "repo_info": ("docker", "current_branch", "head", "dirty", "max_read_bytes", "max_output_chars"),
    "roots_diagnostics": ("safety", "roots.items", "relationship.per_root_relationships"),
    "runtime_state": ("server.python_server_processes", "ollama.tags_probe", "dependency_locks.sections"),
    "git_status": ("raw",),
    "grep": ("lineText", "schema", "total_matches", "returned", "paths", "result_id", "count", "compressed_observation"),
    "find_paths": (),
    "read_snippet": ("requested_start_line", "requested_end_line", "total_lines"),
    "summarize_diff": ("files", "files_sorted_by_churn", "patch", "patch_unified"),
    "risk_scoring": (),
    "workspace_transaction": ("resource_links", "_meta"),
    "policy_simulator": (),
    "clarification_gate": ("audit", "inputs", "decision_reasons"),
    "release_readiness": ("started_at", "finished_at", "mcp_apps"),
    "dependency_security_report": ("inputs", "skipped", "warnings", "security", "provenance", "_meta"),
    "governance_report": ("window", "git", "snapshots", "security", "workflow_diagnostics", "lineage", "provenance", "compressed_observation", "_meta"),
    "self_optimization_report": ("sources", "bottlenecks", "usage_guidance", "resource_links", "exports", "confidence", "caveats", "github_issue_gate", "_meta"),
    "artifact_provenance": ("checks[].attestation",),
    "workflow_diagnostics": ("audit_source", "read_only", "security", "trajectory", "failure_categories"),
    "workflow_lineage": ("mode", "security"),
    "interaction_invariant_audit": ("security", "redactions_applied", "input_summary"),
}


def _object_schema(
    required: list[str],
    properties: dict[str, Any],
    *,
    additional_properties: bool = True,
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": additional_properties,
    }


COMPRESSED_OBSERVATION_SCHEMA: dict[str, Any] = _object_schema(
    ["schema", "summary", "preserved_signals", "omitted", "raw_reference", "rules", "provenance", "redaction"],
    {
        "schema": {"type": "string", "const": "compressed_observation.v1"},
        "summary": {"type": "string"},
        "preserved_signals": {"type": "object"},
        "omitted": {"type": "array", "items": {"type": "object"}},
        "raw_reference": {"type": "object"},
        "rules": _object_schema(
            ["rule_set", "version", "deterministic"],
            {
                "rule_set": {"type": "string"},
                "version": {"type": "integer"},
                "deterministic": {"type": "boolean"},
                "max_preserved_signals": {"type": "integer"},
            },
        ),
        "provenance": _object_schema(
            ["tool", "generated_by", "input_scope"],
            {
                "tool": {"type": "string"},
                "generated_by": {"type": "string"},
                "input_scope": {"type": "string"},
            },
        ),
        "redaction": _object_schema(
            ["applied", "method", "contains_secrets"],
            {
                "applied": {"type": "boolean"},
                "method": {"type": "string"},
                "contains_secrets": {"type": "boolean"},
            },
        ),
    },
)


RESOURCE_LINK_SCHEMA: dict[str, Any] = _object_schema(
    ["schema", "title", "uri", "mime_type", "created_at", "safety"],
    {
        "schema": {"type": "string", "const": "artifact_resource_link.v1"},
        "title": {"type": "string"},
        "uri": {"type": "string"},
        "path": {"type": "string"},
        "mime_type": {"type": "string"},
        "size_bytes": {"type": "integer"},
        "created_at": {"type": "string"},
        "safety": _object_schema(
            ["redacted", "contains_secrets", "repo_boundary_enforced", "note"],
            {
                "redacted": {"type": "boolean"},
                "contains_secrets": {"type": "boolean"},
                "repo_boundary_enforced": {"type": "boolean"},
                "note": {"type": "string"},
            },
        ),
    },
)

STATE_SNAPSHOT_OUTPUT_SCHEMA: dict[str, Any] = _object_schema(
    ["schema", "snapshot_id", "backend", "base_head", "stash_commit", "stash_ref", "had_changes", "resource_links", "_meta"],
    {
        "schema": {"type": "string", "const": "state_snapshot.v1"},
        "snapshot_id": {"type": "string"},
        "backend": {"type": "string"},
        "base_head": {"type": "string"},
        "stash_commit": {"type": "string"},
        "stash_ref": {"type": "string"},
        "had_changes": {"type": "boolean"},
        "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
        "_meta": {"type": "object"},
    },
)

ERROR_OUTPUT_SCHEMA: dict[str, Any] = _object_schema(
    ["ok", "error"],
    {
        "ok": {"type": "boolean", "const": False},
        "error": _object_schema(
            ["type", "message"],
            {
                "type": {"type": "string"},
                "message": {"type": "string"},
                "tool": {"type": "string"},
                "retryable": {"type": "boolean"},
            },
        ),
    },
)

TOOL_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "repo_info": _object_schema(
        ["repo_path", "repo_exists", "is_git_repo", "allow_mutations", "transport"],
        {
            "repo_path": {"type": "string"},
            "repo_exists": {"type": "boolean"},
            "is_git_repo": {"type": "boolean"},
            "allow_mutations": {"type": "boolean"},
            "transport": {"type": "string"},
            "max_read_bytes": {"type": "integer"},
            "max_output_chars": {"type": "integer"},
            "docker": {"type": "object"},
            "current_branch": {"type": "string"},
            "head": {"type": "string"},
            "dirty": {"type": "boolean"},
        },
    ),
    "roots_diagnostics": _object_schema(
        ["schema", "read_only", "advisory_only", "server_repo", "fetch", "roots", "relationship", "guidance"],
        {
            "schema": {"type": "string", "const": "roots_diagnostics.v1"},
            "read_only": {"type": "boolean", "const": True},
            "advisory_only": {"type": "boolean", "const": True},
            "repo_boundary_enforced": {"type": "boolean"},
            "server_repo": {"type": "object"},
            "fetch": {"type": "object"},
            "roots": {"type": "object"},
            "relationship": {"type": "object"},
            "guidance": {"type": "array", "items": {"type": "string"}},
            "safety": {"type": "object"},
        },
    ),
    "runtime_state": _object_schema(
        ["schema", "timestamp", "transport", "server", "sse", "ollama", "docker", "dependency_locks"],
        {
            "schema": {"type": "string", "const": "runtime_state.v1"},
            "timestamp": {"type": "string"},
            "transport": {"type": "string"},
            "server": {"type": "object"},
            "sse": {"type": "object"},
            "ollama": {"type": "object"},
            "docker": {"type": "object"},
            "dependency_locks": {"type": "object"},
        },
    ),
    "git_status": _object_schema(
        ["status", "short"],
        {
            "status": {"type": "array", "items": {"type": "string"}},
            "short": {"type": "boolean"},
            "raw": {"type": "string"},
        },
    ),
    "grep": {
        "type": "array",
        "items": _object_schema(
            [],
            {
                "path": {"type": "string"},
                "line": {"type": "integer"},
                "column": {"type": "integer"},
                "match": {"type": "string"},
                "lineText": {"type": "string"},
                "schema": {"type": "string"},
                "total_matches": {"type": "integer"},
                "returned": {"type": "integer"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "result_id": {"type": "string"},
                "count": {"type": "integer"},
                "results": {"type": "array", "items": {"type": "object"}},
                "compressed_observation": COMPRESSED_OBSERVATION_SCHEMA,
            },
        ),
    },
    "find_paths": {"type": "array", "items": {"type": "string"}},
    "read_snippet": _object_schema(
        ["path", "start_line", "end_line", "content"],
        {
            "path": {"type": "string"},
            "requested_start_line": {"type": "integer"},
            "requested_end_line": {"type": "integer"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
            "total_lines": {"type": "integer"},
            "content": {"type": "string"},
        },
    ),
    "summarize_diff": _object_schema(
        ["file_count", "total_added", "total_deleted", "risk_flags"],
        {
            "file_count": {"type": "integer"},
            "total_added": {"type": "integer"},
            "total_deleted": {"type": "integer"},
            "files": {"type": "array", "items": {"type": "object"}},
            "risk_flags": {"type": "object"},
            "files_sorted_by_churn": {"type": "array", "items": {"type": "object"}},
            "patch": {"type": "string"},
            "patch_unified": {"type": "integer"},
        },
    ),
    "risk_scoring": _object_schema(
        ["risk_score", "risk_level", "reasons", "summary"],
        {
            "risk_score": {"type": "integer"},
            "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "object"},
        },
    ),
    "workspace_transaction": _object_schema(
        ["schema", "mode", "result"],
        {
            "schema": {"type": "string", "const": "workspace_transaction.v1"},
            "mode": {"type": "string"},
            "result": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "compressed_observation": COMPRESSED_OBSERVATION_SCHEMA,
            "_meta": {"type": "object"},
        },
    ),
    "policy_simulator": _object_schema(
        ["schema", "ok", "blocking_policies", "docs", "security", "risk", "license"],
        {
            "schema": {"type": "string", "const": "policy_simulator.v1"},
            "ok": {"type": "boolean"},
            "blocking_policies": {"type": "array", "items": {"type": "string"}},
            "docs": {"type": "object"},
            "security": {"type": "object"},
            "risk": {"type": "object"},
            "license": {"type": "object"},
        },
    ),

    "clarification_gate": _object_schema(
        ["schema", "ok_to_continue", "status", "missing_fields", "questions", "fallback_checklist", "elicitation"],
        {
            "schema": {"type": "string", "const": "clarification_gate.v1"},
            "ok_to_continue": {"type": "boolean"},
            "status": {"type": "string", "enum": ["ready", "needs_clarification", "declined", "cancelled"]},
            "missing_fields": {"type": "array", "items": {"type": "object"}},
            "questions": {"type": "array", "items": {"type": "string"}},
            "fallback_checklist": {"type": "array", "items": {"type": "string"}},
            "elicitation": {"type": "object"},
            "audit": {"type": "object"},
            "inputs": {"type": "object"},
            "decision_reasons": {"type": "array", "items": {"type": "string"}},
        },
    ),
    "release_readiness": _object_schema(
        ["schema", "base_ref", "head_ref", "ok", "checks"],
        {
            "schema": {"type": "string", "enum": ["release_readiness.v1", "release_readiness.quick.v1"]},
            "base_ref": {"type": "string"},
            "head_ref": {"type": "string"},
            "started_at": {"type": "string"},
            "finished_at": {"type": "string"},
            "ok": {"type": "boolean"},
            "checks": {"type": "object"},
        },
    ),
    "dependency_security_report": _object_schema(
        ["schema", "report_id", "generated_at", "status", "ok", "summary", "components", "vulnerabilities", "advisory", "gate", "exports", "resource_links"],
        {
            "schema": {"type": "string", "const": "dependency_security_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "read_only": {"type": "boolean", "const": True},
            "status": {"type": "string", "enum": ["clean", "vulnerable", "skipped", "stale-cache", "network-disabled", "scanner-unavailable"]},
            "ok": {"type": "boolean"},
            "summary": {"type": "object"},
            "inputs": {"type": "object"},
            "advisory": {"type": "object"},
            "gate": {"type": "object"},
            "components": {"type": "array", "items": {"type": "object"}},
            "vulnerabilities": {"type": "array", "items": {"type": "object"}},
            "skipped": {"type": "array", "items": {"type": "object"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "security": {"type": "object"},
            "exports": {"type": "object"},
            "provenance": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "_meta": {"type": "object"},
        },
    ),
    "governance_report": _object_schema(
        ["schema", "report_id", "generated_at", "audit", "governance_hooks", "exports"],
        {
            "schema": {"type": "string", "const": "governance_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "window": {"type": "object"},
            "git": {"type": "object"},
            "audit": {"type": "object"},
            "workflow_diagnostics": {"type": "object"},
            "governance_hooks": {"type": "object"},
            "snapshots": {"type": "object"},
            "security": {"type": "object"},
            "exports": {"type": "object"},
            "lineage": {"type": "object"},
            "provenance": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "_meta": {"type": "object"},
        },
    ),
    "self_optimization_report": _object_schema(
        ["schema", "report_id", "generated_at", "window", "summary", "metrics", "optimization_candidates", "security"],
        {
            "schema": {"type": "string", "const": "self_optimization_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "window": {"type": "object"},
            "summary": {"type": "object"},
            "metrics": {"type": "object"},
            "sources": {"type": "object"},
            "confidence": {"type": "string"},
            "caveats": {"type": "array", "items": {"type": "string"}},
            "bottlenecks": {"type": "array", "items": {"type": "object"}},
            "optimization_candidates": {"type": "array", "items": {"type": "object"}},
            "github_issue_gate": {"type": "object"},
            "usage_guidance": {"type": "object"},
            "security": {"type": "object"},
            "exports": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "_meta": {"type": "object"},
        },
    ),
    "artifact_provenance": _object_schema(
        ["schema", "provenance_schema", "attestation_schema", "artifact_count", "ok", "checks"],
        {
            "schema": {"type": "string", "const": "artifact_provenance_report.v1"},
            "provenance_schema": {"type": "string", "const": "mcp_artifact_provenance.v1"},
            "attestation_schema": {"type": "string", "const": "mcp_artifact_attestation.v1"},
            "artifact_count": {"type": "integer"},
            "ok": {"type": "boolean"},
            "checks": {"type": "array", "items": {"type": "object"}},
        },
    ),
    "workflow_diagnostics": _object_schema(
        ["schema", "ok", "critical_step_candidate", "failure_category", "evidence", "safe_next_actions", "redactions_applied"],
        {
            "schema": {"type": "string", "const": "workflow_diagnostics.v1"},
            "ok": {"type": "boolean"},
            "step_count": {"type": "integer"},
            "failed_step_count": {"type": "integer"},
            "failure_categories": {"type": "object"},
            "critical_step_candidate": {"type": "object"},
            "failure_category": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "object"}},
            "safe_next_actions": {"type": "array", "items": {"type": "string"}},
            "redactions_applied": {"type": "array", "items": {"type": "string"}},
            "audit_source": {"type": "object"},
            "read_only": {"type": "boolean"},
            "security": {"type": "object"},
            "trajectory": {"type": "array", "items": {"type": "object"}},
        },
    ),
    "workflow_lineage": _object_schema(
        ["schema", "read_only", "manifest_path", "plan_id", "status", "ok", "checks", "conditions"],
        {
            "schema": {"type": "string", "const": "workflow_lineage.verify.v1"},
            "mode": {"type": "string", "const": "verify"},
            "read_only": {"type": "boolean", "const": True},
            "manifest_path": {"type": "string"},
            "plan_id": {"type": "string"},
            "status": {"type": "string", "enum": ["matched", "input_changed", "artifact_changed"]},
            "ok": {"type": "boolean"},
            "conditions": {"type": "array", "items": {"type": "string"}},
            "checks": {
                "type": "object",
                "properties": {
                    "plan": {"type": "object"},
                    "artifacts": {"type": "object"},
                    "non_deterministic_nodes": {"type": "array", "items": {"type": "object"}},
                },
                "additionalProperties": True,
            },
            "security": {"type": "object"},
        },
    ),
    "interaction_invariant_audit": _object_schema(
        ["schema", "read_only", "advisory_only", "ok_to_continue", "confidence", "extracted_invariants", "suspected_smells", "safe_next_actions", "linked_gates"],
        {
            "schema": {"type": "string", "const": "interaction_invariant_audit.v1"},
            "read_only": {"type": "boolean", "const": True},
            "advisory_only": {"type": "boolean", "const": True},
            "ok_to_continue": {"type": "boolean"},
            "confidence": {"type": "number"},
            "extracted_invariants": {"type": "array", "items": {"type": "object"}},
            "suspected_smells": {"type": "array", "items": {"type": "object"}},
            "safe_next_actions": {"type": "array", "items": {"type": "string"}},
            "linked_gates": {"type": "object"},
            "redactions_applied": {"type": "array", "items": {"type": "string"}},
            "security": {"type": "object"},
            "input_summary": {"type": "object"},
        },
    ),
}


def tool_output_contract(tool_name: str) -> dict[str, Any]:
    """Return the checked-in output contract for a schema-backed tool."""
    if tool_name not in TOOL_OUTPUT_SCHEMAS:
        raise KeyError(f"no output schema registered for tool: {tool_name}")
    return {
        "schema": SCHEMA_VERSION,
        "tool": tool_name,
        "outputSchema": TOOL_OUTPUT_SCHEMAS[tool_name],
        "errorOutputSchema": ERROR_OUTPUT_SCHEMA,
        "stableFields": list(STABLE_FIELDS[tool_name]),
        "experimentalFields": list(EXPERIMENTAL_FIELDS[tool_name]),
    }


def all_tool_output_contracts() -> dict[str, Any]:
    """Return the complete initial schema-backed tool list and contracts."""
    return {
        "schema": SCHEMA_VERSION,
        "tools": [tool_output_contract(name) for name in SCHEMA_BACKED_TOOL_NAMES],
    }


def structured_tool_result(tool_name: str, payload: Any) -> dict[str, Any]:
    """Build an MCP-compatible result preserving text and structuredContent.

    FastMCP can serialize plain dict/list returns, but fixtures and direct clients
    need a deterministic envelope that mirrors the MCP 2025-06-18 result shape:
    human-readable JSON text remains in ``content`` and the same typed object is
    available under ``structuredContent``.
    """
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, sort_keys=True, ensure_ascii=False),
            }
        ],
        "structuredContent": payload,
        "outputSchema": TOOL_OUTPUT_SCHEMAS[tool_name],
    }


def make_tool_error(tool_name: str, exc: Exception | str, *, retryable: bool = False) -> dict[str, Any]:
    """Return the shared documented error envelope for schema-backed tools."""
    return {
        "ok": False,
        "error": {
            "tool": tool_name,
            "type": type(exc).__name__ if isinstance(exc, Exception) else "Error",
            "message": str(exc),
            "retryable": retryable,
        },
    }


def validate_against_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Small JSON-Schema subset validator for contract tests and smoke fixtures."""
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise AssertionError(f"{path}: expected object")
        for key in schema.get("required", []):
            if key not in value:
                raise AssertionError(f"{path}: missing required key {key!r}")
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                validate_against_schema(item, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties", True) is False:
                raise AssertionError(f"{path}: unexpected key {key!r}")
    elif expected == "array":
        if not isinstance(value, list):
            raise AssertionError(f"{path}: expected array")
        item_schema = schema.get("items")
        if item_schema:
            for idx, item in enumerate(value):
                validate_against_schema(item, item_schema, f"{path}[{idx}]")
    elif expected == "string":
        if not isinstance(value, str):
            raise AssertionError(f"{path}: expected string")
    elif expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise AssertionError(f"{path}: expected integer")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise AssertionError(f"{path}: expected boolean")
    elif expected == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise AssertionError(f"{path}: expected number")

    if "const" in schema and value != schema["const"]:
        raise AssertionError(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise AssertionError(f"{path}: expected one of {schema['enum']!r}")
