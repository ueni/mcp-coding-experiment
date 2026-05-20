# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Deterministic public MCP surface integrity helpers.

This module intentionally works only with public MCP metadata: tools/list
metadata, prompt/resource catalog metadata, the tool annotation manifest,
checked-in output contracts, and public repository documentation/discovery
references. It must not read repository contents, absolute host paths, bearer
tokens, runtime secrets, or resource payloads.
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
SUMMARY_SCHEMA = "tool_catalog_integrity_summary.v1"
DIGEST_ALGORITHM = "sha256"
BASELINE_PUBLIC_PATH = "source/tool_catalog_baseline.json"
BASELINE_FILE = Path(__file__).with_name("tool_catalog_baseline.json")
CANONICALIZATION = {
    "json": "RFC8259 JSON with sort_keys=true, separators=(',', ':'), ensure_ascii=true",
    "unicode": "metadata strings are hashed exactly as emitted by FastMCP/Pydantic after explicit prompt-argument redaction",
    "ordering": "tools, prompts, resources, and public-discovery entries are sorted by stable public identifiers; object keys are sorted recursively by JSON encoder",
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
        {"label": "Public MCP surface integrity", "path": "docs/tool-catalog-integrity.md"},
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
        "Metadata appears to steer the model into another tool instead of describing this surface.",
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
_DESCRIPTION_KEYS = {"description", "summary", "title", "text"}
_MUTATION_CATEGORIES = {"write", "git mutation"}
_OPEN_WORLD_CATEGORIES = {"network", "shell/process", "secret-sensitive"}
_HOST_PATH_URI_RE = re.compile(r"^(?:file:|/[A-Za-z0-9_.-]|[A-Za-z]:[\\/])")


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


def _resource_identity(resource: dict[str, Any]) -> str:
    return str(
        resource.get("uriTemplate")
        or resource.get("uri_template")
        or resource.get("uri")
        or resource.get("name")
        or ""
    )


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


def _canonical_prompt_entry(
    prompt: dict[str, Any],
    *,
    template_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    name = str(prompt.get("name", ""))
    arguments = _jsonable(prompt.get("arguments") or [])
    metadata = {
        "list_prompts": {
            "name": name,
            "title": prompt.get("title"),
            "description": str(prompt.get("description", "")),
            "arguments": arguments,
            "icons": _jsonable(prompt.get("icons")),
            "meta": _jsonable(prompt.get("meta")),
        },
        "template": _jsonable(template_payload or {}),
        "documentation": [
            {
                "label": "README MCP prompt pack",
                "path": "README.md#mcp-prompts-in-vs-code-and-copilot",
            }
        ],
    }
    metadata["template_digest"] = sha256_digest(metadata["template"])
    entry = {"name": name, "metadata": metadata}
    entry["digest"] = sha256_digest(_prompt_digest_payload(entry))
    return entry


def _canonical_resource_metadata(resource: dict[str, Any], *, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": str(resource.get("name", "")),
        "title": resource.get("title"),
        "description": str(resource.get("description", "")),
        "uri": resource.get("uri"),
        "uri_template": resource.get("uriTemplate") or resource.get("uri_template"),
        "mime_type": resource.get("mimeType") or resource.get("mime_type"),
        "size": resource.get("size"),
        "icons": _jsonable(resource.get("icons")),
        "annotations": _jsonable(resource.get("annotations")),
        "meta": _jsonable(resource.get("meta")),
        "capabilities": {
            "listed": kind == "resource",
            "templated": kind == "resource_template",
            "readable_without_template_arguments": kind == "resource",
            "read_payload_hashed": False,
        },
    }


def _canonical_resource_entry(resource: dict[str, Any], *, kind: str) -> dict[str, Any]:
    identity = _resource_identity(resource)
    metadata = _canonical_resource_metadata(resource, kind=kind)
    entry = {"identity": identity, "name": str(resource.get("name", "")), "metadata": metadata}
    entry["digest"] = sha256_digest(_resource_digest_payload(entry))
    return entry


def _canonical_public_discovery_entries(
    public_discovery: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(public_discovery, dict):
        return []
    entries: list[dict[str, Any]] = []
    prompts = public_discovery.get("prompts", [])
    if isinstance(prompts, list):
        for prompt in prompts:
            if isinstance(prompt, str):
                payload = {"kind": "prompt", "name": prompt, "documented": {}}
            elif isinstance(prompt, dict):
                payload = {"kind": "prompt", **_jsonable(prompt)}
            else:
                continue
            identity = f"prompt:{payload.get('name', '')}"
            entry = {"identity": identity, "metadata": payload}
            entry["digest"] = sha256_digest(_discovery_digest_payload(entry))
            entries.append(entry)
    resources = public_discovery.get("resources", [])
    if isinstance(resources, list):
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            payload = {"kind": "resource", **_jsonable(resource)}
            identity = f"resource:{payload.get('uri_template') or payload.get('uriTemplate') or payload.get('uri') or payload.get('name', '')}"
            entry = {"identity": identity, "metadata": payload}
            entry["digest"] = sha256_digest(_discovery_digest_payload(entry))
            entries.append(entry)
    return sorted(entries, key=lambda item: item["identity"])


def _tool_digest_payload(tool_entry: dict[str, Any]) -> dict[str, Any]:
    return {"name": tool_entry.get("name", ""), "metadata": tool_entry.get("metadata", {})}


def _prompt_digest_payload(prompt_entry: dict[str, Any]) -> dict[str, Any]:
    return {"name": prompt_entry.get("name", ""), "metadata": prompt_entry.get("metadata", {})}


def _resource_digest_payload(resource_entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": resource_entry.get("identity", ""),
        "name": resource_entry.get("name", ""),
        "metadata": resource_entry.get("metadata", {}),
    }


def _discovery_digest_payload(discovery_entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": discovery_entry.get("identity", ""),
        "metadata": discovery_entry.get("metadata", {}),
    }


def _catalog_digest_payload(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": catalog.get("schema"),
        "digest_algorithm": catalog.get("digest_algorithm"),
        "canonicalization": catalog.get("canonicalization"),
        "generated_from": catalog.get("generated_from"),
        "tool_count": catalog.get("tool_count"),
        "prompt_count": catalog.get("prompt_count", 0),
        "resource_count": catalog.get("resource_count", 0),
        "public_discovery_count": catalog.get("public_discovery_count", 0),
        "tools": catalog.get("tools", []),
        "prompts": catalog.get("prompts", []),
        "resources": catalog.get("resources", []),
        "public_discovery": catalog.get("public_discovery", []),
    }


def refresh_catalog_digests(catalog: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with deterministic per-surface and whole-catalog digests refreshed."""
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

    prompts = refreshed.get("prompts", [])
    if not isinstance(prompts, list):
        prompts = []
    for entry in prompts:
        if isinstance(entry, dict):
            metadata = entry.get("metadata", {}) if isinstance(entry.get("metadata"), dict) else {}
            if "template" in metadata:
                metadata["template_digest"] = sha256_digest(metadata.get("template", {}))
            entry["digest"] = sha256_digest(_prompt_digest_payload(entry))
    refreshed["prompts"] = sorted(
        [entry for entry in prompts if isinstance(entry, dict)], key=lambda item: str(item.get("name", ""))
    )
    refreshed["prompt_count"] = len(refreshed["prompts"])

    resources = refreshed.get("resources", [])
    if not isinstance(resources, list):
        resources = []
    for entry in resources:
        if isinstance(entry, dict):
            entry["digest"] = sha256_digest(_resource_digest_payload(entry))
    refreshed["resources"] = sorted(
        [entry for entry in resources if isinstance(entry, dict)], key=lambda item: str(item.get("identity", ""))
    )
    refreshed["resource_count"] = len(refreshed["resources"])

    discovery = refreshed.get("public_discovery", [])
    if not isinstance(discovery, list):
        discovery = []
    for entry in discovery:
        if isinstance(entry, dict):
            entry["digest"] = sha256_digest(_discovery_digest_payload(entry))
    refreshed["public_discovery"] = sorted(
        [entry for entry in discovery if isinstance(entry, dict)], key=lambda item: str(item.get("identity", ""))
    )
    refreshed["public_discovery_count"] = len(refreshed["public_discovery"])
    refreshed["whole_catalog_digest"] = sha256_digest(_catalog_digest_payload(refreshed))
    return refreshed


def build_tool_catalog_baseline(
    list_tools_payload: list[dict[str, Any]],
    annotation_manifest: dict[str, Any],
    output_contracts: dict[str, Any],
    *,
    list_prompts_payload: list[dict[str, Any]] | None = None,
    prompt_templates: dict[str, dict[str, Any]] | None = None,
    list_resources_payload: list[dict[str, Any]] | None = None,
    list_resource_templates_payload: list[dict[str, Any]] | None = None,
    public_discovery: dict[str, Any] | None = None,
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
    prompt_templates = prompt_templates or {}
    prompts = [
        _canonical_prompt_entry(
            _jsonable(prompt),
            template_payload=prompt_templates.get(str(prompt.get("name", ""))),
        )
        for prompt in (list_prompts_payload or [])
    ]
    resources = [
        _canonical_resource_entry(_jsonable(resource), kind="resource")
        for resource in (list_resources_payload or [])
    ] + [
        _canonical_resource_entry(_jsonable(resource), kind="resource_template")
        for resource in (list_resource_templates_payload or [])
    ]
    catalog = {
        "schema": BASELINE_SCHEMA,
        "digest_algorithm": DIGEST_ALGORITHM,
        "canonicalization": dict(CANONICALIZATION),
        "generated_from": {
            "mcp_list_tools": "live FastMCP mcp.list_tools() public metadata",
            "mcp_list_prompts": "live FastMCP mcp.list_prompts() public prompt metadata",
            "mcp_get_prompt": "live FastMCP mcp.get_prompt() template text with synthetic argument values redacted to placeholders",
            "mcp_list_resources": "live FastMCP mcp.list_resources() public resource metadata only; resource payloads are not read or hashed",
            "mcp_list_resource_templates": "live FastMCP mcp.list_resource_templates() public resource template metadata only",
            "public_discovery": "allowlisted /.well-known/mcp-server.json capability names plus README/docs mention checks",
            "tool_annotations": "tool_annotations.v1 from TOOL_SECURITY_METADATA",
            "tool_output_contracts": "tool_output_contracts.v1 checked-in contracts where present",
            "documentation_references": "public repository documentation paths/anchors only",
        },
        "baseline_path": BASELINE_PUBLIC_PATH,
        "tools": sorted(tools, key=lambda item: item["name"]),
        "prompts": sorted(prompts, key=lambda item: item["name"]),
        "resources": sorted(resources, key=lambda item: item["identity"]),
        "public_discovery": _canonical_public_discovery_entries(public_discovery),
        "security": {
            "public_metadata_only": True,
            "contains_repository_contents": False,
            "contains_secrets": False,
            "contains_host_absolute_paths": False,
            "resource_payloads_hashed": False,
            "prompt_argument_values_redacted": True,
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


def _compare_entries(
    baseline_entries: list[dict[str, Any]],
    current_entries: list[dict[str, Any]],
    *,
    key_field: str,
    item_label: str,
) -> dict[str, Any]:
    baseline_by_key = {
        str(entry.get(key_field, "")): entry
        for entry in baseline_entries
        if isinstance(entry, dict)
    }
    current_by_key = {
        str(entry.get(key_field, "")): entry
        for entry in current_entries
        if isinstance(entry, dict)
    }
    added = sorted(key for key in current_by_key if key not in baseline_by_key)
    removed = sorted(key for key in baseline_by_key if key not in current_by_key)
    changed: list[dict[str, Any]] = []
    for key in sorted(set(baseline_by_key) & set(current_by_key)):
        expected = baseline_by_key[key]
        actual = current_by_key[key]
        if expected.get("digest") != actual.get("digest"):
            changed.append(
                {
                    item_label: key,
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


def compare_tool_catalogs(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    return _compare_entries(
        baseline.get("tools", []) if isinstance(baseline.get("tools"), list) else [],
        current.get("tools", []) if isinstance(current.get("tools"), list) else [],
        key_field="name",
        item_label="tool",
    )


def _compare_surface_catalogs(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    tools = compare_tool_catalogs(baseline, current)
    prompts = _compare_entries(
        baseline.get("prompts", []) if isinstance(baseline.get("prompts"), list) else [],
        current.get("prompts", []) if isinstance(current.get("prompts"), list) else [],
        key_field="name",
        item_label="prompt",
    )
    resources = _compare_entries(
        baseline.get("resources", []) if isinstance(baseline.get("resources"), list) else [],
        current.get("resources", []) if isinstance(current.get("resources"), list) else [],
        key_field="identity",
        item_label="resource",
    )
    public_discovery = _compare_entries(
        baseline.get("public_discovery", []) if isinstance(baseline.get("public_discovery"), list) else [],
        current.get("public_discovery", []) if isinstance(current.get("public_discovery"), list) else [],
        key_field="identity",
        item_label="public_discovery_entry",
    )
    summary = {
        "added": tools["summary"]["added"] + prompts["summary"]["added"] + resources["summary"]["added"] + public_discovery["summary"]["added"],
        "removed": tools["summary"]["removed"] + prompts["summary"]["removed"] + resources["summary"]["removed"] + public_discovery["summary"]["removed"],
        "changed": tools["summary"]["changed"] + prompts["summary"]["changed"] + resources["summary"]["changed"] + public_discovery["summary"]["changed"],
    }
    status = "matched" if summary == {"added": 0, "removed": 0, "changed": 0} else "drift"
    return {
        "status": status,
        "ok": status == "matched",
        "summary": summary,
        "tools": tools,
        "prompts": prompts,
        "resources": resources,
        "public_discovery": public_discovery,
        "added": [
            *(f"tool:{item}" for item in tools["added"]),
            *(f"prompt:{item}" for item in prompts["added"]),
            *(f"resource:{item}" for item in resources["added"]),
            *(f"public_discovery:{item}" for item in public_discovery["added"]),
        ],
        "removed": [
            *(f"tool:{item}" for item in tools["removed"]),
            *(f"prompt:{item}" for item in prompts["removed"]),
            *(f"resource:{item}" for item in resources["removed"]),
            *(f"public_discovery:{item}" for item in public_discovery["removed"]),
        ],
        "changed": [
            *(dict(item, surface="tool") for item in tools["changed"]),
            *(dict(item, surface="prompt") for item in prompts["changed"]),
            *(dict(item, surface="resource") for item in resources["changed"]),
            *(dict(item, surface="public_discovery") for item in public_discovery["changed"]),
        ],
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


def _lint_text_metadata(surface: str, item_name: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path, text in _iter_text_nodes(metadata):
        for finding_type, message, pattern in _TEXT_LINT_PATTERNS:
            if pattern.search(text):
                findings.append(
                    {
                        "type": finding_type,
                        "severity": "advisory",
                        surface: item_name,
                        "surface": surface,
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
                "surface": "tool",
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
                "surface": "tool",
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
                "surface": "tool",
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
                "surface": "tool",
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


def _lint_missing_description(surface: str, item_name: str, value: str, path: str) -> list[dict[str, Any]]:
    if value.strip():
        return []
    return [
        {
            "type": "missing_description",
            "severity": "advisory",
            surface: item_name,
            "surface": surface,
            "path": path,
            "message": f"Public MCP {surface} metadata should include a description.",
        }
    ]


def _lint_prompt_entry(prompt_entry: dict[str, Any]) -> list[dict[str, Any]]:
    name = str(prompt_entry.get("name", ""))
    metadata = prompt_entry.get("metadata", {}) if isinstance(prompt_entry.get("metadata"), dict) else {}
    list_metadata = metadata.get("list_prompts", {}) if isinstance(metadata.get("list_prompts"), dict) else {}
    findings = _lint_missing_description(
        "prompt",
        name,
        str(list_metadata.get("description", "")),
        "metadata.list_prompts.description",
    )
    findings.extend(_lint_text_metadata("prompt", name, metadata))
    template = metadata.get("template", {}) if isinstance(metadata.get("template"), dict) else {}
    template_text = canonical_json(template)
    if "Safety guardrails:" not in template_text:
        findings.append(
            {
                "type": "undocumented_prompt_guardrails",
                "severity": "advisory",
                "prompt": name,
                "surface": "prompt",
                "path": "metadata.template",
                "message": "Public workflow prompts should document Safety guardrails in their template text.",
            }
        )
    return findings


def _lint_resource_entry(resource_entry: dict[str, Any]) -> list[dict[str, Any]]:
    identity = str(resource_entry.get("identity", ""))
    metadata = resource_entry.get("metadata", {}) if isinstance(resource_entry.get("metadata"), dict) else {}
    findings = _lint_missing_description(
        "resource",
        identity,
        str(metadata.get("description", "")),
        "metadata.description",
    )
    findings.extend(_lint_text_metadata("resource", identity, metadata))
    uri = str(metadata.get("uri") or metadata.get("uri_template") or identity)
    if _HOST_PATH_URI_RE.search(uri):
        findings.append(
            {
                "type": "host_path_like_resource_uri",
                "severity": "advisory",
                "resource": identity,
                "surface": "resource",
                "path": "metadata.uri_template",
                "message": "Public resource URI/template appears to expose a host-path-like shape.",
            }
        )
    if "{path}" in uri and (not uri.startswith("repo://") or ".." in uri):
        findings.append(
            {
                "type": "resource_boundary_risk",
                "severity": "advisory",
                "resource": identity,
                "surface": "resource",
                "path": "metadata.uri_template",
                "message": "Path-parameter resource templates should remain repo:// scoped and avoid traversal-looking segments.",
            }
        )
    return findings


def _surface_keys(catalog: dict[str, Any], section: str, key: str) -> set[str]:
    values = catalog.get(section, []) if isinstance(catalog, dict) else []
    if not isinstance(values, list):
        return set()
    return {str(item.get(key, "")) for item in values if isinstance(item, dict)}


def _lint_public_discovery(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    prompt_names = _surface_keys(catalog, "prompts", "name")
    resource_identities = _surface_keys(catalog, "resources", "identity")
    advertised_prompts: set[str] = set()
    advertised_resources: set[str] = set()
    undocumented: list[dict[str, Any]] = []
    entries = catalog.get("public_discovery", []) if isinstance(catalog, dict) else []
    if not isinstance(entries, list):
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        metadata = entry.get("metadata", {}) if isinstance(entry.get("metadata"), dict) else {}
        kind = str(metadata.get("kind", ""))
        if kind == "prompt":
            name = str(metadata.get("name", ""))
            advertised_prompts.add(name)
            documented = metadata.get("documented", {}) if isinstance(metadata.get("documented"), dict) else {}
            if documented and not any(bool(value) for value in documented.values()):
                undocumented.append({"surface": "prompt", "name": name, "path": "metadata.documented"})
        elif kind == "resource":
            identity = str(metadata.get("uri_template") or metadata.get("uriTemplate") or metadata.get("uri") or metadata.get("name", ""))
            advertised_resources.add(identity)
            documented = metadata.get("documented", {}) if isinstance(metadata.get("documented"), dict) else {}
            if documented and not any(bool(value) for value in documented.values()):
                undocumented.append({"surface": "resource", "name": identity, "path": "metadata.documented"})
    findings: list[dict[str, Any]] = []
    for name in sorted(prompt_names - advertised_prompts):
        findings.append(
            {
                "type": "public_discovery_mismatch",
                "severity": "advisory",
                "surface": "prompt",
                "prompt": name,
                "path": "public_discovery.prompts",
                "message": "Live public prompt is missing from the allowlisted discovery manifest snapshot.",
            }
        )
    for name in sorted(advertised_prompts - prompt_names):
        findings.append(
            {
                "type": "public_discovery_mismatch",
                "severity": "advisory",
                "surface": "prompt",
                "prompt": name,
                "path": "public_discovery.prompts",
                "message": "Discovery manifest advertises a prompt that is not present in live MCP prompt metadata.",
            }
        )
    for identity in sorted(resource_identities - advertised_resources):
        findings.append(
            {
                "type": "public_discovery_mismatch",
                "severity": "advisory",
                "surface": "resource",
                "resource": identity,
                "path": "public_discovery.resources",
                "message": "Live public resource/template is missing from the allowlisted discovery manifest snapshot.",
            }
        )
    for identity in sorted(advertised_resources - resource_identities):
        findings.append(
            {
                "type": "public_discovery_mismatch",
                "severity": "advisory",
                "surface": "resource",
                "resource": identity,
                "path": "public_discovery.resources",
                "message": "Discovery manifest advertises a resource/template that is not present in live MCP resource metadata.",
            }
        )
    for item in undocumented:
        findings.append(
            {
                "type": "public_docs_mismatch",
                "severity": "advisory",
                item["surface"]: item["name"],
                "surface": item["surface"],
                "path": item["path"],
                "message": "Public discovery entry is not mentioned in the checked public documentation snapshot.",
            }
        )
    return findings


def lint_tool_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """Return advisory lint findings for public MCP surface metadata."""
    findings: list[dict[str, Any]] = []
    tools = catalog.get("tools", []) if isinstance(catalog, dict) else []
    if isinstance(tools, list):
        for tool_entry in tools:
            if not isinstance(tool_entry, dict):
                continue
            name = str(tool_entry.get("name", ""))
            metadata = tool_entry.get("metadata", {})
            if isinstance(metadata, dict):
                findings.extend(_lint_text_metadata("tool", name, metadata))
            findings.extend(_lint_annotation_mismatches(tool_entry))
    prompts = catalog.get("prompts", []) if isinstance(catalog, dict) else []
    if isinstance(prompts, list):
        for prompt_entry in prompts:
            if isinstance(prompt_entry, dict):
                findings.extend(_lint_prompt_entry(prompt_entry))
    resources = catalog.get("resources", []) if isinstance(catalog, dict) else []
    if isinstance(resources, list):
        for resource_entry in resources:
            if isinstance(resource_entry, dict):
                findings.extend(_lint_resource_entry(resource_entry))
    findings.extend(_lint_public_discovery(catalog))
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
    prompts = catalog.get("prompts", []) if isinstance(catalog, dict) else []
    resources = catalog.get("resources", []) if isinstance(catalog, dict) else []
    discovery = catalog.get("public_discovery", []) if isinstance(catalog, dict) else []
    per_tool = [
        {"tool": str(tool.get("name", "")), "digest": str(tool.get("digest", ""))}
        for tool in tools
        if isinstance(tool, dict)
    ]
    per_prompt = [
        {"prompt": str(prompt.get("name", "")), "digest": str(prompt.get("digest", ""))}
        for prompt in prompts
        if isinstance(prompt, dict)
    ]
    per_resource = [
        {"resource": str(resource.get("identity", "")), "digest": str(resource.get("digest", ""))}
        for resource in resources
        if isinstance(resource, dict)
    ]
    per_discovery = [
        {"entry": str(entry.get("identity", "")), "digest": str(entry.get("digest", ""))}
        for entry in discovery
        if isinstance(entry, dict)
    ]
    return {
        "schema": catalog.get("schema", BASELINE_SCHEMA),
        "baseline_path": catalog.get("baseline_path", BASELINE_PUBLIC_PATH),
        "digest_algorithm": catalog.get("digest_algorithm", DIGEST_ALGORITHM),
        "whole_catalog_digest": catalog.get("whole_catalog_digest", ""),
        "tool_count": catalog.get("tool_count", len(per_tool)),
        "prompt_count": catalog.get("prompt_count", len(per_prompt)),
        "resource_count": catalog.get("resource_count", len(per_resource)),
        "public_discovery_count": catalog.get("public_discovery_count", len(per_discovery)),
        "per_tool": sorted(per_tool, key=lambda item: item["tool"]),
        "per_prompt": sorted(per_prompt, key=lambda item: item["prompt"]),
        "per_resource": sorted(per_resource, key=lambda item: item["resource"]),
        "per_public_discovery": sorted(per_discovery, key=lambda item: item["entry"]),
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
            "tools": {"status": status, "ok": False, "summary": {"added": 0, "removed": 0, "changed": 0}, "added": [], "removed": [], "changed": []},
            "prompts": {"status": status, "ok": False, "summary": {"added": 0, "removed": 0, "changed": 0}, "added": [], "removed": [], "changed": []},
            "resources": {"status": status, "ok": False, "summary": {"added": 0, "removed": 0, "changed": 0}, "added": [], "removed": [], "changed": []},
            "public_discovery": {"status": status, "ok": False, "summary": {"added": 0, "removed": 0, "changed": 0}, "added": [], "removed": [], "changed": []},
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
            "prompt_count": 0,
            "resource_count": 0,
            "public_discovery_count": 0,
            "per_tool": [],
            "per_prompt": [],
            "per_resource": [],
            "per_public_discovery": [],
        }
    else:
        baseline = refresh_catalog_digests(baseline)
        drift = _compare_surface_catalogs(baseline, current)
        status = drift["status"]
        baseline_summary = catalog_digest_summary(baseline)
    report = {
        "schema": REPORT_SCHEMA,
        "ok": bool(drift.get("ok")) and lint.get("status") in {"clean", "advisory_findings"},
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
            "resource_payloads_hashed": False,
            "prompt_argument_values_redacted": True,
        },
    }
    if include_tools:
        report["baseline"]["tools"] = baseline.get("tools", []) if baseline else []
        report["baseline"]["prompts"] = baseline.get("prompts", []) if baseline else []
        report["baseline"]["resources"] = baseline.get("resources", []) if baseline else []
        report["baseline"]["public_discovery"] = baseline.get("public_discovery", []) if baseline else []
        report["current"]["tools"] = current.get("tools", [])
        report["current"]["prompts"] = current.get("prompts", [])
        report["current"]["resources"] = current.get("resources", [])
        report["current"]["public_discovery"] = current.get("public_discovery", [])
    return report
