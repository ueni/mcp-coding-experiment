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
    "runtime_state",
    "git_status",
    "grep",
    "find_paths",
    "read_snippet",
    "summarize_diff",
    "risk_scoring",
    "workspace_transaction",
    "policy_simulator",
    "release_readiness",
    "governance_report",
)

STABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "repo_info": ("repo_path", "repo_exists", "is_git_repo", "allow_mutations", "transport"),
    "runtime_state": ("schema", "timestamp", "transport", "server", "sse", "ollama", "docker"),
    "git_status": ("status", "short"),
    "grep": ("path", "line", "column", "match"),
    "find_paths": ("<array item: repository-relative path>",),
    "read_snippet": ("path", "start_line", "end_line", "content"),
    "summarize_diff": ("file_count", "total_added", "total_deleted", "risk_flags"),
    "risk_scoring": ("risk_score", "risk_level", "reasons", "summary"),
    "workspace_transaction": ("schema", "mode", "result"),
    "policy_simulator": ("schema", "ok", "blocking_policies", "docs", "security", "risk", "license"),
    "release_readiness": ("schema", "base_ref", "head_ref", "ok", "checks"),
    "governance_report": ("schema", "report_id", "generated_at", "audit", "governance_hooks", "exports"),
}

EXPERIMENTAL_FIELDS: dict[str, tuple[str, ...]] = {
    "repo_info": ("docker", "current_branch", "head", "dirty", "max_read_bytes", "max_output_chars"),
    "runtime_state": ("server.python_server_processes", "ollama.tags_probe"),
    "git_status": ("raw",),
    "grep": ("lineText", "schema", "total_matches", "returned", "paths", "result_id", "count"),
    "find_paths": (),
    "read_snippet": ("requested_start_line", "requested_end_line", "total_lines"),
    "summarize_diff": ("files", "files_sorted_by_churn", "patch", "patch_unified"),
    "risk_scoring": (),
    "workspace_transaction": (),
    "policy_simulator": (),
    "release_readiness": ("started_at", "finished_at"),
    "governance_report": ("window", "git", "snapshots", "security"),
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
    "runtime_state": _object_schema(
        ["schema", "timestamp", "transport", "server", "sse", "ollama", "docker"],
        {
            "schema": {"type": "string", "const": "runtime_state.v1"},
            "timestamp": {"type": "string"},
            "transport": {"type": "string"},
            "server": {"type": "object"},
            "sse": {"type": "object"},
            "ollama": {"type": "object"},
            "docker": {"type": "object"},
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
    "governance_report": _object_schema(
        ["schema", "report_id", "generated_at", "audit", "governance_hooks", "exports"],
        {
            "schema": {"type": "string", "const": "governance_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "window": {"type": "object"},
            "git": {"type": "object"},
            "audit": {"type": "object"},
            "governance_hooks": {"type": "object"},
            "snapshots": {"type": "object"},
            "security": {"type": "object"},
            "exports": {"type": "object"},
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
