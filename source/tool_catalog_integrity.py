# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Deterministic public MCP tool-catalog integrity helpers.

This module intentionally works only with public MCP metadata: tools/list
metadata, the tool annotation manifest, checked-in output contracts, and public
repository documentation references.  It must not read repository contents,
absolute host paths, bearer tokens, or runtime secrets.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

BASELINE_SCHEMA = "tool_catalog_integrity_baseline.v1"
REPORT_SCHEMA = "tool_catalog_integrity.v1"
DIGEST_ALGORITHM = "sha256"
BASELINE_PUBLIC_PATH = "source/tool_catalog_baseline.json"
BASELINE_FILE = Path(__file__).with_name("tool_catalog_baseline.json")
CANONICALIZATION = {
    "json": "RFC8259 JSON with sort_keys=true, separators=(',', ':'), ensure_ascii=true",
    "unicode": "metadata strings are hashed exactly as emitted by FastMCP/Pydantic",
    "ordering": "tools are sorted by name; object keys are sorted recursively by JSON encoder",
}

_DOC_REFS_BY_TOOL: dict[str, tuple[dict[str, str], ...]] = {
    "task_router": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Workflow selection", "path": "docs/workflow-selection.md"},
        {"label": "Agent execution modes", "path": "docs/execution-modes.md"},
    ),
    "test_impact_map": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Tooling white paper", "path": "docs/tooling-whitepaper.md#53-static-test-impact-map"},
    ),
    "tool_annotations": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Tool annotation manifest", "path": "docs/tooling-whitepaper.md#55-tool-annotation-manifest"},
    ),
    "tool_output_contracts": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Output schema contracts", "path": "docs/mcp-output-schemas.md"},
    ),
    "tool_catalog_integrity": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Tool catalog integrity", "path": "docs/tool-catalog-integrity.md"},
    ),
    "policy_insights": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Policy insight regression bank", "path": "docs/policy-insights.md"},
    ),
    "workflow_task": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Async workflow tasks", "path": "docs/workflow-tasks.md"},
    ),
    "task_status": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Async workflow tasks", "path": "docs/workflow-tasks.md"},
    ),
    "roots_diagnostics": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "MCP roots diagnostics", "path": "docs/roots-diagnostics.md"},
    ),
    "governance_report": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Governance report workflow", "path": "docs/governance-report.md"},
    ),
    "workflow_lineage": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Workflow lineage manifests", "path": "docs/workflow-lineage.md"},
    ),
    "workflow_diagnostics": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Workflow diagnostics", "path": "docs/workflow-diagnostics.md"},
    ),
    "dependency_security_report": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Dependency security report", "path": "docs/dependency-security.md"},
    ),
    "self_optimization_report": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Self-optimization efficiency report", "path": "docs/self-optimization-report.md"},
    ),
    "artifact_provenance": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Governance report workflow", "path": "docs/governance-report.md"},
    ),
    "interaction_invariant_audit": (
        {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
        {"label": "Interaction invariant audit", "path": "docs/interaction-invariant-audit.md"},
    ),
}
_DEFAULT_DOC_REFS: tuple[dict[str, str], ...] = (
    {"label": "README public MCP surface", "path": "README.md#tool-catalog-by-category"},
    {"label": "Tooling white paper", "path": "docs/tooling-whitepaper.md#51-public-mcp-v1-surface"},
)

_TEXT_LINT_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "hidden_instruction_text",
        "Metadata contains language resembling hidden/system/developer instruction override text.",
        re.compile(
            r"\b(?:ignore|disregard|override|bypass)\s+(?:all\s+|any\s+)?"
            r"(?:previous|prior|system|developer|user)\s+instructions\b"
            r"|\bhidden\s+instructions?\b"
            r"|\b(?:reveal|print|expose|leak)\s+(?:the\s+)?system\s+prompt\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cross_tool_manipulation",
        "Metadata appears to steer the model into another tool instead of describing this tool.",
        re.compile(
            r"\b(?:must|always|force|prefer|prioriti[sz]e)\s+(?:call|use|invoke)\s+[`\"']?[A-Za-z_][\w-]*"
            r"|\b(?:before|after)\s+(?:calling|using|invoking)\s+(?:any\s+)?(?:other\s+)?tool\b",
            re.IGNORECASE,
        ),
    ),
    (
        "exfiltration_wording",
        "Metadata contains wording associated with secret or repository-content exfiltration.",
        re.compile(
            r"\b(?:exfiltrate|leak|upload|send|transmit)\b.{0,96}"
            r"\b(?:secret|token|credential|private|repository\s+contents?|source\s+code)\b"
            r"|\b(?:secret|token|credential|private|repository\s+contents?|source\s+code)\b.{0,96}"
            r"\b(?:exfiltrate|leak|upload|send|transmit)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)
_DESCRIPTION_KEYS = {"description", "summary", "title"}
_MUTATION_CATEGORIES = {"write", "git mutation"}
_OPEN_WORLD_CATEGORIES = {"network", "shell/process", "secret-sensitive"}


def canonical_json(value: Any) -> str:
    """Return the stable JSON byte string used for all catalog digests."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_digest(value: Any) -> str:
    """Return a prefixed SHA-256 digest over the canonical JSON representation."""
    return f"sha256:{hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()}"


def documentation_references(tool_name: str) -> list[dict[str, str]]:
    refs = _DOC_REFS_BY_TOOL.get(tool_name, _DEFAULT_DOC_REFS)
    return [dict(item) for item in refs]


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True, default=str))


def _tool_name(tool: dict[str, Any]) -> str:
    return str(tool.get("name") or tool.get("tool") or "")


def _annotations_by_tool(annotation_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = annotation_manifest.get("tools", []) if isinstance(annotation_manifest, dict) else []
    if not isinstance(entries, list):
        return {}
    return {str(entry.get("tool", "")): _jsonable(entry) for entry in entries if isinstance(entry, dict)}


def _contracts_by_tool(output_contracts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = output_contracts.get("tools", []) if isinstance(output_contracts, dict) else []
    if not isinstance(entries, list):
        return {}
    return {str(entry.get("tool", "")): _jsonable(entry) for entry in entries if isinstance(entry, dict)}


def _canonical_list_tool_metadata(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(tool.get("name", "")),
        "title": tool.get("title"),
        "description": str(tool.get("description", "")),
        "input_schema": _jsonable(tool.get("inputSchema", {})),
        "output_schema": _jsonable(tool.get("outputSchema")),
        "mcp_annotations": _jsonable(tool.get("annotations")),
        "icons": _jsonable(tool.get("icons")),
        "meta": _jsonable(tool.get("meta")),
        "execution": _jsonable(tool.get("execution")),
    }


def _canonical_tool_entry(
    tool: dict[str, Any],
    *,
    annotation_entry: dict[str, Any] | None,
    output_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    name = _tool_name(tool)
    metadata = {
        "list_tools": _canonical_list_tool_metadata(tool),
        "security": _jsonable(annotation_entry or {}),
        "output_contract": _jsonable(output_contract),
        "documentation": documentation_references(name),
    }
    entry = {"name": name, "metadata": metadata}
    entry["digest"] = sha256_digest(_tool_digest_payload(entry))
    return entry


def _tool_digest_payload(tool_entry: dict[str, Any]) -> dict[str, Any]:
    return {"name": tool_entry.get("name", ""), "metadata": tool_entry.get("metadata", {})}


def _catalog_digest_payload(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": catalog.get("schema"),
        "digest_algorithm": catalog.get("digest_algorithm"),
        "canonicalization": catalog.get("canonicalization"),
        "generated_from": catalog.get("generated_from"),
        "tool_count": catalog.get("tool_count"),
        "tools": catalog.get("tools", []),
    }


def refresh_catalog_digests(catalog: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with deterministic per-tool and whole-catalog digests refreshed."""
    refreshed = copy.deepcopy(catalog)
    tools = refreshed.get("tools", [])
    if not isinstance(tools, list):
        tools = []
    for entry in tools:
        if isinstance(entry, dict):
            entry["digest"] = sha256_digest(_tool_digest_payload(entry))
    refreshed["tools"] = sorted(
        [entry for entry in tools if isinstance(entry, dict)], key=lambda item: str(item.get("name", ""))
    )
    refreshed["tool_count"] = len(refreshed["tools"])
    refreshed["whole_catalog_digest"] = sha256_digest(_catalog_digest_payload(refreshed))
    return refreshed


def build_tool_catalog_baseline(
    list_tools_payload: list[dict[str, Any]],
    annotation_manifest: dict[str, Any],
    output_contracts: dict[str, Any],
) -> dict[str, Any]:
    """Build the deterministic baseline shape from live public MCP metadata."""
    annotations = _annotations_by_tool(annotation_manifest)
    contracts = _contracts_by_tool(output_contracts)
    tools = [
        _canonical_tool_entry(
            _jsonable(tool),
            annotation_entry=annotations.get(_tool_name(tool)),
            output_contract=contracts.get(_tool_name(tool)),
        )
        for tool in list_tools_payload
    ]
    catalog = {
        "schema": BASELINE_SCHEMA,
        "digest_algorithm": DIGEST_ALGORITHM,
        "canonicalization": dict(CANONICALIZATION),
        "generated_from": {
            "mcp_list_tools": "live FastMCP mcp.list_tools() public metadata",
            "tool_annotations": "tool_annotations.v1 from TOOL_SECURITY_METADATA",
            "tool_output_contracts": "tool_output_contracts.v1 checked-in contracts where present",
            "documentation_references": "public repository documentation paths/anchors only",
        },
        "baseline_path": BASELINE_PUBLIC_PATH,
        "tools": sorted(tools, key=lambda item: item["name"]),
        "security": {
            "public_metadata_only": True,
            "contains_repository_contents": False,
            "contains_secrets": False,
            "contains_host_absolute_paths": False,
        },
    }
    return refresh_catalog_digests(catalog)


def load_baseline(path: Path | None = None) -> dict[str, Any]:
    baseline_path = path or BASELINE_FILE
    return json.loads(baseline_path.read_text(encoding="utf-8"))


def write_baseline(catalog: dict[str, Any], path: Path | None = None) -> None:
    baseline_path = path or BASELINE_FILE
    baseline_path.write_text(
        json.dumps(refresh_catalog_digests(catalog), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _short_value(value: Any, *, max_chars: int = 240) -> Any:
    if isinstance(value, (dict, list)):
        text = canonical_json(value)
    else:
        text = str(value)
    if len(text) <= max_chars:
        return value
    return text[: max_chars - 3] + "..."


def _diff_values(expected: Any, actual: Any, path: str, out: list[dict[str, Any]], limit: int) -> None:
    if len(out) >= limit or expected == actual:
        return
    if isinstance(expected, dict) and isinstance(actual, dict):
        keys = sorted(set(expected) | set(actual), key=str)
        for key in keys:
            child_path = f"{path}.{key}" if path else str(key)
            if key not in expected:
                out.append({"path": child_path, "expected": "<missing>", "actual": _short_value(actual[key])})
            elif key not in actual:
                out.append({"path": child_path, "expected": _short_value(expected[key]), "actual": "<missing>"})
            else:
                _diff_values(expected[key], actual[key], child_path, out, limit)
            if len(out) >= limit:
                return
        return
    if isinstance(expected, list) and isinstance(actual, list):
        max_len = max(len(expected), len(actual))
        for index in range(max_len):
            child_path = f"{path}[{index}]"
            if index >= len(expected):
                out.append({"path": child_path, "expected": "<missing>", "actual": _short_value(actual[index])})
            elif index >= len(actual):
                out.append({"path": child_path, "expected": _short_value(expected[index]), "actual": "<missing>"})
            else:
                _diff_values(expected[index], actual[index], child_path, out, limit)
            if len(out) >= limit:
                return
        return
    out.append({"path": path, "expected": _short_value(expected), "actual": _short_value(actual)})


def metadata_diff(expected: dict[str, Any], actual: dict[str, Any], *, limit: int = 40) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    _diff_values(expected.get("metadata", {}), actual.get("metadata", {}), "metadata", diffs, limit)
    return diffs


def compare_tool_catalogs(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    baseline_tools = {
        str(tool.get("name", "")): tool
        for tool in baseline.get("tools", [])
        if isinstance(tool, dict)
    }
    current_tools = {
        str(tool.get("name", "")): tool
        for tool in current.get("tools", [])
        if isinstance(tool, dict)
    }
    added = sorted(name for name in current_tools if name not in baseline_tools)
    removed = sorted(name for name in baseline_tools if name not in current_tools)
    changed: list[dict[str, Any]] = []
    for name in sorted(set(baseline_tools) & set(current_tools)):
        expected = baseline_tools[name]
        actual = current_tools[name]
        if expected.get("digest") != actual.get("digest"):
            changed.append(
                {
                    "tool": name,
                    "baseline_digest": expected.get("digest", ""),
                    "current_digest": actual.get("digest", ""),
                    "metadata_diff": metadata_diff(expected, actual),
                }
            )
    status = "matched" if not added and not removed and not changed else "drift"
    return {
        "status": status,
        "ok": status == "matched",
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def _iter_text_nodes(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if isinstance(item, str) and str(key).lower() in _DESCRIPTION_KEYS:
                yield child_path, item
            else:
                yield from _iter_text_nodes(item, child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_text_nodes(item, f"{path}[{index}]")


def _lint_text_metadata(tool_name: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path, text in _iter_text_nodes(metadata):
        for finding_type, message, pattern in _TEXT_LINT_PATTERNS:
            if pattern.search(text):
                findings.append(
                    {
                        "type": finding_type,
                        "severity": "advisory",
                        "tool": tool_name,
                        "path": path,
                        "message": message,
                        "snippet": _short_value(" ".join(text.split()), max_chars=180),
                    }
                )
    return findings


def _annotation_mismatch_findings(
    *,
    tool_name: str,
    categories: list[str],
    annotations: dict[str, Any],
    path: str,
) -> list[dict[str, Any]]:
    category_set = set(categories)
    findings: list[dict[str, Any]] = []
    read_only = annotations.get("readOnlyHint")
    destructive = annotations.get("destructiveHint")
    open_world = annotations.get("openWorldHint")
    if category_set & _MUTATION_CATEGORIES and read_only is not False:
        findings.append(
            {
                "type": "annotation_category_mismatch",
                "severity": "advisory",
                "tool": tool_name,
                "path": f"{path}.annotations.readOnlyHint",
                "message": "Mutation-capable categories must advertise readOnlyHint=false.",
                "categories": sorted(category_set),
            }
        )
    if "destructive" in category_set and destructive is not True:
        findings.append(
            {
                "type": "annotation_category_mismatch",
                "severity": "advisory",
                "tool": tool_name,
                "path": f"{path}.annotations.destructiveHint",
                "message": "Destructive categories must advertise destructiveHint=true.",
                "categories": sorted(category_set),
            }
        )
    if category_set & _OPEN_WORLD_CATEGORIES and open_world is not True:
        findings.append(
            {
                "type": "annotation_category_mismatch",
                "severity": "advisory",
                "tool": tool_name,
                "path": f"{path}.annotations.openWorldHint",
                "message": "Network/shell/secret-sensitive categories must advertise openWorldHint=true.",
                "categories": sorted(category_set),
            }
        )
    if category_set == {"read-only"} and read_only is not True:
        findings.append(
            {
                "type": "annotation_category_mismatch",
                "severity": "advisory",
                "tool": tool_name,
                "path": f"{path}.annotations.readOnlyHint",
                "message": "Pure read-only categories should advertise readOnlyHint=true.",
                "categories": sorted(category_set),
            }
        )
    return findings


def _lint_annotation_mismatches(tool_entry: dict[str, Any]) -> list[dict[str, Any]]:
    name = str(tool_entry.get("name", ""))
    security = tool_entry.get("metadata", {}).get("security", {})
    if not isinstance(security, dict):
        return []
    findings = _annotation_mismatch_findings(
        tool_name=name,
        categories=list(security.get("categories", [])),
        annotations=security.get("annotations", {}) if isinstance(security.get("annotations"), dict) else {},
        path="metadata.security",
    )
    modes = security.get("modes", [])
    if isinstance(modes, list):
        for index, mode_entry in enumerate(modes):
            if not isinstance(mode_entry, dict):
                continue
            findings.extend(
                _annotation_mismatch_findings(
                    tool_name=name,
                    categories=list(mode_entry.get("categories", [])),
                    annotations=mode_entry.get("annotations", {}) if isinstance(mode_entry.get("annotations"), dict) else {},
                    path=f"metadata.security.modes[{index}]",
                )
            )
    return findings


def lint_tool_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """Return advisory lint findings for public tool metadata and annotations."""
    findings: list[dict[str, Any]] = []
    tools = catalog.get("tools", []) if isinstance(catalog, dict) else []
    if isinstance(tools, list):
        for tool_entry in tools:
            if not isinstance(tool_entry, dict):
                continue
            name = str(tool_entry.get("name", ""))
            metadata = tool_entry.get("metadata", {})
            if isinstance(metadata, dict):
                findings.extend(_lint_text_metadata(name, metadata))
            findings.extend(_lint_annotation_mismatches(tool_entry))
    by_type: dict[str, int] = {}
    for finding in findings:
        finding_type = str(finding.get("type", "unknown"))
        by_type[finding_type] = by_type.get(finding_type, 0) + 1
    return {
        "advisory_only": True,
        "status": "clean" if not findings else "advisory_findings",
        "finding_count": len(findings),
        "by_type": dict(sorted(by_type.items())),
        "findings": findings,
    }


def catalog_digest_summary(catalog: dict[str, Any]) -> dict[str, Any]:
    tools = catalog.get("tools", []) if isinstance(catalog, dict) else []
    per_tool = [
        {"tool": str(tool.get("name", "")), "digest": str(tool.get("digest", ""))}
        for tool in tools
        if isinstance(tool, dict)
    ]
    return {
        "schema": catalog.get("schema", BASELINE_SCHEMA),
        "baseline_path": catalog.get("baseline_path", BASELINE_PUBLIC_PATH),
        "digest_algorithm": catalog.get("digest_algorithm", DIGEST_ALGORITHM),
        "whole_catalog_digest": catalog.get("whole_catalog_digest", ""),
        "tool_count": catalog.get("tool_count", len(per_tool)),
        "per_tool": sorted(per_tool, key=lambda item: item["tool"]),
    }


def integrity_report(
    *,
    baseline: dict[str, Any] | None,
    current: dict[str, Any],
    baseline_error: str = "",
    include_tools: bool = False,
) -> dict[str, Any]:
    current = refresh_catalog_digests(current)
    lint = lint_tool_catalog(current)
    if baseline is None:
        status = "baseline_missing" if baseline_error == "missing" else "baseline_invalid"
        drift = {
            "status": status,
            "ok": False,
            "summary": {"added": 0, "removed": 0, "changed": 0},
            "added": [],
            "removed": [],
            "changed": [],
            "error": baseline_error,
        }
        baseline_summary = {
            "schema": BASELINE_SCHEMA,
            "baseline_path": BASELINE_PUBLIC_PATH,
            "digest_algorithm": DIGEST_ALGORITHM,
            "whole_catalog_digest": "",
            "tool_count": 0,
            "per_tool": [],
        }
    else:
        baseline = refresh_catalog_digests(baseline)
        drift = compare_tool_catalogs(baseline, current)
        status = drift["status"]
        baseline_summary = catalog_digest_summary(baseline)
    report = {
        "schema": REPORT_SCHEMA,
        "ok": bool(drift.get("ok")),
        "status": status,
        "read_only": True,
        "baseline": baseline_summary,
        "current": catalog_digest_summary(current),
        "drift": drift,
        "lint": lint,
        "security": {
            "public_metadata_only": True,
            "contains_repository_contents": False,
            "contains_secrets": False,
            "contains_host_absolute_paths": False,
        },
    }
    if include_tools:
        report["baseline"]["tools"] = baseline.get("tools", []) if baseline else []
        report["current"]["tools"] = current.get("tools", [])
    return report
