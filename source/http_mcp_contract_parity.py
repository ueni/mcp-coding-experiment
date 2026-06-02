# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Offline HTTP/HarnessAPI-style contract parity reporting for MCP tools.

The report compares repository-local HTTP endpoint expectations with the MCP
``tools/list`` contract metadata produced by this repository. It intentionally
records only repository-relative paths and schema digests, not raw endpoint
payloads, secrets, or host paths, so the compact result can be embedded in
``governance_report``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REPORT_SCHEMA = "http_mcp_contract_parity_report.v1"
EXPECTATIONS_SCHEMA = "http_mcp_contract_expectations.v1"
DEFAULT_CONTRACT_PATH = Path(
    ".config/codebase-tooling-mcp/contracts/http-mcp-contract-parity.json"
)
BASELINE_CATALOG_PATH = Path("source/tool_catalog_baseline.json")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def schema_digest(value: Any) -> str:
    """Return a stable SHA-256 digest for a JSON-schema-like value."""
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_repo_relative(path: str | Path, repo_root: Path) -> str:
    candidate = Path(path)
    absolute = candidate if candidate.is_absolute() else repo_root / candidate
    resolved_root = repo_root.resolve()
    resolved = absolute.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"path is outside repository boundary: {path}")
    return resolved.relative_to(resolved_root).as_posix()


def _load_json_file(path: Path, repo_root: Path) -> tuple[dict[str, Any] | None, str]:
    rel_path = _safe_repo_relative(path, repo_root)
    absolute = repo_root / rel_path
    if not absolute.exists():
        return None, rel_path
    payload = json.loads(absolute.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON contract file must contain an object: {rel_path}")
    return payload, rel_path


def _tool_contracts_from_catalog(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = catalog.get("tools", []) if isinstance(catalog, dict) else []
    contracts: dict[str, dict[str, Any]] = {}
    if not isinstance(entries, list):
        return contracts
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", ""))
        metadata = entry.get("metadata", {}) if isinstance(entry.get("metadata"), dict) else {}
        listed = metadata.get("list_tools", {}) if isinstance(metadata.get("list_tools"), dict) else {}
        contracts[name] = {
            "input_schema": listed.get("input_schema"),
            "output_schema": listed.get("output_schema"),
        }
    return contracts


def _load_default_tool_catalog(repo_root: Path) -> dict[str, Any]:
    payload, _ = _load_json_file(BASELINE_CATALOG_PATH, repo_root)
    return payload or {"tools": []}


def _expected_digest(endpoint: dict[str, Any], schema_key: str, digest_key: str) -> tuple[str, str]:
    if digest_key in endpoint and endpoint[digest_key]:
        return str(endpoint[digest_key]), "digest"
    if schema_key in endpoint:
        return schema_digest(endpoint.get(schema_key)), "schema"
    return "", "missing"


def _endpoint_identity(endpoint: dict[str, Any], index: int) -> str:
    explicit = str(endpoint.get("id", "")).strip()
    if explicit:
        return explicit
    method = str(endpoint.get("method", "POST")).upper()
    path = str(endpoint.get("path", f"endpoint-{index}"))
    return f"{method} {path}"


def _finding(
    *,
    kind: str,
    severity: str,
    endpoint: dict[str, Any],
    endpoint_id: str,
    mcp_tool: str,
    contract_path: str,
    expected_digest: str = "",
    actual_digest: str = "",
    message: str,
) -> dict[str, Any]:
    evidence: dict[str, str] = {"contract_path": contract_path}
    if expected_digest:
        evidence["expected_digest"] = expected_digest
    if actual_digest:
        evidence["actual_digest"] = actual_digest
    return {
        "kind": kind,
        "severity": severity,
        "endpoint_id": endpoint_id,
        "endpoint": {
            "method": str(endpoint.get("method", "POST")).upper(),
            "path": str(endpoint.get("path", "")),
        },
        "mcp_tool": mcp_tool,
        "message": message,
        "evidence": evidence,
    }


def generate_http_mcp_contract_parity_report(
    contract_path: str | Path = DEFAULT_CONTRACT_PATH,
    *,
    repo_root: str | Path = Path("."),
    tool_catalog: dict[str, Any] | None = None,
    include_passes: bool = False,
) -> dict[str, Any]:
    """Compare repo-local HTTP endpoint contract expectations with MCP tool schemas.

    Contract files use ``http_mcp_contract_expectations.v1`` and contain an
    ``endpoints`` array. Each endpoint names ``mcp_tool`` plus either inline
    ``request_schema``/``response_schema`` values or precomputed
    ``request_schema_digest``/``response_schema_digest`` values.
    """
    root = Path(repo_root).resolve()
    payload, rel_contract_path = _load_json_file(Path(contract_path), root)
    if payload is None:
        return {
            "schema": REPORT_SCHEMA,
            "ok": True,
            "status": "missing-contract-docs",
            "read_only": True,
            "network_used": False,
            "contract_path": rel_contract_path,
            "summary": {
                "endpoints": 0,
                "matched": 0,
                "drifted": 0,
                "missing_tools": 0,
                "unchecked": 0,
                "findings": 0,
            },
            "comparisons": [],
            "findings": [],
            "security": _security_summary(),
        }

    if payload.get("schema") != EXPECTATIONS_SCHEMA:
        raise ValueError(
            f"unsupported contract expectation schema in {rel_contract_path}: {payload.get('schema')}"
        )
    endpoints = payload.get("endpoints", [])
    if not isinstance(endpoints, list):
        raise ValueError(f"contract endpoints must be a list: {rel_contract_path}")

    catalog = tool_catalog if tool_catalog is not None else _load_default_tool_catalog(root)
    mcp_contracts = _tool_contracts_from_catalog(catalog)
    comparisons: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    matched = drifted = missing_tools = unchecked = 0

    for index, endpoint_value in enumerate(endpoints):
        if not isinstance(endpoint_value, dict):
            unchecked += 1
            continue
        endpoint = endpoint_value
        endpoint_id = _endpoint_identity(endpoint, index)
        mcp_tool = str(endpoint.get("mcp_tool", "")).strip()
        row = {
            "endpoint_id": endpoint_id,
            "endpoint": {
                "method": str(endpoint.get("method", "POST")).upper(),
                "path": str(endpoint.get("path", "")),
            },
            "mcp_tool": mcp_tool,
            "status": "pass",
            "checks": {},
        }
        if not mcp_tool:
            unchecked += 1
            row["status"] = "unchecked"
            findings.append(
                _finding(
                    kind="missing_mcp_tool_mapping",
                    severity="warn",
                    endpoint=endpoint,
                    endpoint_id=endpoint_id,
                    mcp_tool="",
                    contract_path=rel_contract_path,
                    message="HTTP contract endpoint has no mcp_tool mapping.",
                )
            )
            comparisons.append(row)
            continue

        actual = mcp_contracts.get(mcp_tool)
        if actual is None:
            missing_tools += 1
            row["status"] = "missing-tool"
            findings.append(
                _finding(
                    kind="missing_mcp_tool",
                    severity="block",
                    endpoint=endpoint,
                    endpoint_id=endpoint_id,
                    mcp_tool=mcp_tool,
                    contract_path=rel_contract_path,
                    message="HTTP contract maps to an MCP tool that is not declared by tools/list.",
                )
            )
            comparisons.append(row)
            continue

        row_drift = False
        for label, schema_key, digest_key, actual_key in (
            ("request", "request_schema", "request_schema_digest", "input_schema"),
            ("response", "response_schema", "response_schema_digest", "output_schema"),
        ):
            expected, expected_source = _expected_digest(endpoint, schema_key, digest_key)
            actual_schema = actual.get(actual_key)
            actual_digest = schema_digest(actual_schema)
            check = {
                "expected_digest": expected,
                "expected_source": expected_source,
                "actual_digest": actual_digest,
                "status": "pass",
            }
            if not expected:
                check["status"] = "unchecked"
                unchecked += 1
            elif expected != actual_digest:
                check["status"] = "drift"
                row_drift = True
                findings.append(
                    _finding(
                        kind=f"{label}_schema_drift",
                        severity="warn",
                        endpoint=endpoint,
                        endpoint_id=endpoint_id,
                        mcp_tool=mcp_tool,
                        contract_path=rel_contract_path,
                        expected_digest=expected,
                        actual_digest=actual_digest,
                        message=(
                            f"HTTP {label} contract digest differs from the declared MCP "
                            f"{actual_key.replace('_', ' ')} digest."
                        ),
                    )
                )
            row["checks"][label] = check

        if row_drift:
            drifted += 1
            row["status"] = "drift"
        else:
            matched += 1
        if include_passes or row["status"] != "pass":
            comparisons.append(row)

    status = "drift" if findings else "pass"
    return {
        "schema": REPORT_SCHEMA,
        "ok": not findings,
        "status": status,
        "read_only": True,
        "network_used": False,
        "contract_path": rel_contract_path,
        "summary": {
            "endpoints": len(endpoints),
            "matched": matched,
            "drifted": drifted,
            "missing_tools": missing_tools,
            "unchecked": unchecked,
            "findings": len(findings),
        },
        "comparisons": comparisons,
        "findings": findings,
        "security": _security_summary(),
    }


def _security_summary() -> dict[str, Any]:
    return {
        "repo_relative_paths_only": True,
        "raw_schemas_embedded": False,
        "schema_evidence": "sha256 digests only",
        "network_used": False,
        "contains_secrets": False,
        "redaction": "findings include endpoint ids, repo-relative contract path, tool names, and schema digests only",
    }


def compact_http_mcp_contract_parity(report: dict[str, Any], *, max_findings: int = 10) -> dict[str, Any]:
    """Return a governance-report-friendly compact parity summary."""
    findings = report.get("findings", []) if isinstance(report.get("findings"), list) else []
    return {
        "schema": report.get("schema", REPORT_SCHEMA),
        "ok": bool(report.get("ok", False)),
        "status": str(report.get("status", "unknown")),
        "read_only": True,
        "network_used": False,
        "contract_path": str(report.get("contract_path", "")),
        "summary": report.get("summary", {}),
        "findings": findings[:max_findings],
        "security": report.get("security", _security_summary()),
    }
