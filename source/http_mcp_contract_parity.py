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


def _json_pointer_get(value: Any, pointer: str) -> tuple[bool, Any]:
    if pointer in ("", "/"):
        return True, value
    if not pointer.startswith("/"):
        return False, None
    current = value
    for raw_part in pointer.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, current


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def schema_digest(value: Any) -> str:
    """Return a stable SHA-256 digest for a JSON-schema-like value."""
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return "sha256:" + digest


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
        metadata = (
            entry.get("metadata", {}) if isinstance(entry.get("metadata"), dict) else {}
        )
        listed = (
            metadata.get("list_tools", {})
            if isinstance(metadata.get("list_tools"), dict)
            else {}
        )
        contracts[name] = {
            "input_schema": listed.get("input_schema"),
            "output_schema": listed.get("output_schema"),
        }
    return contracts


def _load_default_tool_catalog(repo_root: Path) -> dict[str, Any]:
    payload, _ = _load_json_file(BASELINE_CATALOG_PATH, repo_root)
    return payload or {"tools": []}


def _expected_digest(
    endpoint: dict[str, Any], schema_key: str, digest_key: str
) -> tuple[str, str]:
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


def _doc_surface_finding(
    *,
    kind: str,
    severity: str,
    surface_id: str,
    path: str,
    contract_path: str,
    message: str,
    evidence: dict[str, str] | None = None,
) -> dict[str, Any]:
    finding_evidence = {"contract_path": contract_path, "path": path}
    if evidence:
        finding_evidence.update(evidence)
    return {
        "kind": kind,
        "severity": severity,
        "surface_id": surface_id,
        "path": path,
        "message": message,
        "evidence": finding_evidence,
    }


def _argument_default(
    mcp_contracts: dict[str, dict[str, Any]], mcp_tool: str, argument: str
) -> tuple[bool, Any]:
    input_schema = mcp_contracts.get(mcp_tool, {}).get("input_schema")
    if not isinstance(input_schema, dict):
        return False, None
    properties = input_schema.get("properties", {})
    if not isinstance(properties, dict):
        return False, None
    argument_schema = properties.get(argument)
    if not isinstance(argument_schema, dict) or "default" not in argument_schema:
        return False, None
    return True, argument_schema.get("default")


def _compare_doc_surfaces(
    *,
    surfaces: Any,
    repo_root: Path,
    rel_contract_path: str,
    mcp_contracts: dict[str, dict[str, Any]],
    include_passes: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    empty_summary = {
        "doc_surfaces": 0,
        "doc_surfaces_checked": 0,
        "stale_doc_surfaces": 0,
        "default_drifts": 0,
    }
    if not isinstance(surfaces, list):
        return [], [], empty_summary

    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    checked = stale = default_drifts = 0

    for index, surface_value in enumerate(surfaces):
        if not isinstance(surface_value, dict):
            continue
        surface_id = str(surface_value.get("id") or f"doc-surface-{index}")
        rel_path = _safe_repo_relative(str(surface_value.get("path", "")), repo_root)
        absolute = repo_root / rel_path
        row = {
            "surface_id": surface_id,
            "path": rel_path,
            "kind": str(surface_value.get("kind", "repo_doc_surface")),
            "status": "pass",
            "checks": {},
        }
        surface_stale = False
        surface_default_drift = False
        checked += 1
        if not absolute.exists():
            row["status"] = "missing"
            stale += 1
            findings.append(
                _doc_surface_finding(
                    kind="repo_doc_surface_missing",
                    severity="warn",
                    surface_id=surface_id,
                    path=rel_path,
                    contract_path=rel_contract_path,
                    message=(
                        "Repository-owned documentation/example surface is missing."
                    ),
                )
            )
            rows.append(row)
            continue

        text = absolute.read_text(encoding="utf-8")
        row["content_digest"] = schema_digest(text)
        required_texts = surface_value.get("required_text", []) or []
        for required_index, required in enumerate(required_texts):
            required_text = str(required)
            check_key = f"required_text:{required_index}"
            present = required_text in text
            row["checks"][check_key] = {
                "status": "pass" if present else "stale",
                "expected_digest": schema_digest(required_text),
            }
            if not present:
                row["status"] = "stale-doc"
                surface_stale = True
                findings.append(
                    _doc_surface_finding(
                        kind="repo_doc_surface_stale",
                        severity="warn",
                        surface_id=surface_id,
                        path=rel_path,
                        contract_path=rel_contract_path,
                        message=(
                            "Repository-owned documentation/example surface is missing "
                            "expected parity text."
                        ),
                        evidence={"expected_digest": schema_digest(required_text)},
                    )
                )

        expected_values = surface_value.get("expected_values", []) or []
        parsed_json: Any = None
        if expected_values:
            try:
                parsed_json = json.loads(text)
            except json.JSONDecodeError:
                parsed_json = None
        for value_index, expected_value in enumerate(expected_values):
            if not isinstance(expected_value, dict):
                continue
            pointer = str(expected_value.get("pointer", ""))
            expected = expected_value.get("equals")
            found, actual = (
                _json_pointer_get(parsed_json, pointer)
                if parsed_json is not None
                else (False, None)
            )
            passed = found and actual == expected
            check_key = f"expected_value:{value_index}"
            row["checks"][check_key] = {
                "pointer": pointer,
                "status": "pass" if passed else "stale",
                "expected_digest": schema_digest(expected),
                "actual_digest": schema_digest(actual) if found else "missing",
            }
            if not passed:
                row["status"] = "stale-doc"
                surface_stale = True
                findings.append(
                    _doc_surface_finding(
                        kind="repo_doc_surface_stale",
                        severity="warn",
                        surface_id=surface_id,
                        path=rel_path,
                        contract_path=rel_contract_path,
                        message=(
                            "Repository-owned JSON example differs from expected MCP "
                            "onboarding value."
                        ),
                        evidence={
                            "pointer": pointer,
                            "expected_digest": schema_digest(expected),
                            "actual_digest": (
                                schema_digest(actual) if found else "missing"
                            ),
                        },
                    )
                )

        for default_index, default_expectation in enumerate(
            surface_value.get("argument_defaults", []) or []
        ):
            if not isinstance(default_expectation, dict):
                continue
            mcp_tool = str(default_expectation.get("mcp_tool", ""))
            argument = str(default_expectation.get("argument", ""))
            expected_default = default_expectation.get("default")
            found, actual_default = _argument_default(mcp_contracts, mcp_tool, argument)
            passed = found and actual_default == expected_default
            check_key = f"argument_default:{default_index}"
            row["checks"][check_key] = {
                "mcp_tool": mcp_tool,
                "argument": argument,
                "status": "pass" if passed else "drift",
                "expected_digest": schema_digest(expected_default),
                "actual_digest": schema_digest(actual_default) if found else "missing",
            }
            if not passed:
                row["status"] = "default-drift"
                surface_default_drift = True
                findings.append(
                    _doc_surface_finding(
                        kind="repo_doc_argument_default_drift",
                        severity="warn",
                        surface_id=surface_id,
                        path=rel_path,
                        contract_path=rel_contract_path,
                        message=(
                            "Repository-owned documentation/example default "
                            "differs from the MCP tools/list argument default."
                        ),
                        evidence={
                            "mcp_tool": mcp_tool,
                            "argument": argument,
                            "expected_digest": schema_digest(expected_default),
                            "actual_digest": (
                                schema_digest(actual_default) if found else "missing"
                            ),
                        },
                    )
                )
        if surface_stale:
            stale += 1
        if surface_default_drift:
            default_drifts += 1
        if include_passes or row["status"] != "pass":
            rows.append(row)

    return rows, findings, {
        "doc_surfaces": len(surfaces),
        "doc_surfaces_checked": checked,
        "stale_doc_surfaces": stale,
        "default_drifts": default_drifts,
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
                "doc_surfaces": 0,
                "doc_surfaces_checked": 0,
                "stale_doc_surfaces": 0,
                "default_drifts": 0,
                "findings": 0,
            },
            "comparisons": [],
            "doc_surfaces": [],
            "findings": [],
            "security": _security_summary(),
        }

    if payload.get("schema") != EXPECTATIONS_SCHEMA:
        raise ValueError(
            "unsupported contract expectation schema in "
            f"{rel_contract_path}: {payload.get('schema')}"
        )
    endpoints = payload.get("endpoints", [])
    if not isinstance(endpoints, list):
        raise ValueError(f"contract endpoints must be a list: {rel_contract_path}")

    catalog = (
        tool_catalog if tool_catalog is not None else _load_default_tool_catalog(root)
    )
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
                    message=(
                        "HTTP contract maps to an MCP tool that is not declared by "
                        "tools/list."
                    ),
                )
            )
            comparisons.append(row)
            continue

        row_drift = False
        for label, schema_key, digest_key, actual_key in (
            ("request", "request_schema", "request_schema_digest", "input_schema"),
            ("response", "response_schema", "response_schema_digest", "output_schema"),
        ):
            expected, expected_source = _expected_digest(
                endpoint, schema_key, digest_key
            )
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
                            f"HTTP {label} contract digest differs from the "
                            "declared MCP "
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

    doc_surface_rows, doc_surface_findings, doc_summary = _compare_doc_surfaces(
        surfaces=payload.get("repo_doc_surfaces", []),
        repo_root=root,
        rel_contract_path=rel_contract_path,
        mcp_contracts=mcp_contracts,
        include_passes=include_passes,
    )
    findings.extend(doc_surface_findings)

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
            **doc_summary,
            "findings": len(findings),
        },
        "comparisons": comparisons,
        "doc_surfaces": doc_surface_rows,
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
        "redaction": (
            "findings include endpoint ids, repo-relative docs/contract paths, "
            "tool names, JSON pointers, and value/schema digests only"
        ),
    }


def compact_http_mcp_contract_parity(
    report: dict[str, Any], *, max_findings: int = 10
) -> dict[str, Any]:
    """Return a governance-report-friendly compact parity summary."""
    findings = (
        report.get("findings", []) if isinstance(report.get("findings"), list) else []
    )
    return {
        "schema": report.get("schema", REPORT_SCHEMA),
        "ok": bool(report.get("ok", False)),
        "status": str(report.get("status", "unknown")),
        "read_only": True,
        "network_used": False,
        "contract_path": str(report.get("contract_path", "")),
        "summary": report.get("summary", {}),
        "doc_surfaces": report.get("doc_surfaces", []),
        "findings": findings[:max_findings],
        "security": report.get("security", _security_summary()),
    }
