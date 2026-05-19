#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Validate MCP Registry ``server.json`` readiness without publishing.

The official MCP Registry schema covers the generic manifest shape. This script
adds repository-specific release-readiness checks that are intentionally stricter
than the generic schema: official-registry package hosts, OCI ownership marker
consistency, metadata size/namespacing, no checked-in secrets, no host absolute
paths, and version drift against the repository's version metadata defaults.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:  # pragma: no cover - exercised in the normal test/runtime environment.
    import jsonschema
except ImportError:  # pragma: no cover - defensive message for bare systems.
    jsonschema = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_SCHEMA_URL = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
DEFAULT_SCHEMA_PATH = REPO_ROOT / "schemas" / "mcp-registry-server-2025-12-11.schema.json"
DEFAULT_MANIFEST_PATH = REPO_ROOT / "server.json"
DEFAULT_DOCKERFILE_PATH = REPO_ROOT / "source" / "Dockerfile"
DEFAULT_VERSION_SOURCE_PATH = REPO_ROOT / "source" / "version_metadata.py"
EXPECTED_SERVER_NAME = "io.github.ueni/codebase-tooling-mcp"
EXPECTED_PACKAGE_IDENTIFIER_PREFIX = "ghcr.io/ueni/codebase-tooling-mcp:"
OCI_OWNERSHIP_LABEL = "io.modelcontextprotocol.server.name"
PUBLISHER_META_KEY = "io.modelcontextprotocol.registry/publisher-provided"
PUBLISHER_META_MAX_BYTES = 4096

SUPPORTED_REGISTRY_BASE_URLS = {
    "npm": {"https://registry.npmjs.org"},
    "pypi": {"https://pypi.org"},
    "nuget": {"https://api.nuget.org/v3/index.json"},
    "mcpb": {"https://github.com", "https://gitlab.com"},
}
SUPPORTED_OCI_HOSTS = {
    "docker.io",
    "ghcr.io",
    "quay.io",
    "mcr.microsoft.com",
}
SUPPORTED_REGISTRY_TYPES = {"npm", "pypi", "nuget", "oci", "mcpb"}

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}"),
    re.compile(r"(?i)\b(?:password|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;}\]]{8,}"),
]
HOST_ABSOLUTE_PATH_PATTERNS = [
    re.compile(r"(^|[=:\s,])(/home/[^\s,}\]]+)"),
    re.compile(r"(^|[=:\s,])(/Users/[^\s,}\]]+)"),
    re.compile(r"(^|[=:\s,])(/Volumes/[^\s,}\]]+)"),
    re.compile(r"(^|[=:\s,])(/mnt/[a-zA-Z]/[^\s,}\]]+)"),
    re.compile(r"(^|[=:\s,])(/var/folders/[^\s,}\]]+)"),
    re.compile(r"(^|[=:\s,])(/tmp/[^\s,}\]]+)"),
    re.compile(r"\b[A-Za-z]:\\\\[^\s,}\]]+"),
]


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    path: str
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "severity": self.severity,
        }


def _json_path(parts: list[str | int]) -> str:
    if not parts:
        return "$"
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _constant_strings_from_python(path: Path) -> dict[str, str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants: dict[str, str] = {}
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if not node.targets[0].id.startswith("DEFAULT_VERSION_"):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, str):
            constants[node.targets[0].id] = value
    return constants


def expected_default_version(version_source_path: Path = DEFAULT_VERSION_SOURCE_PATH) -> str:
    constants = _constant_strings_from_python(version_source_path)
    missing = [
        name
        for name in (
            "DEFAULT_VERSION_COMPATIBILITY",
            "DEFAULT_VERSION_FEATURE",
            "DEFAULT_VERSION_BUGFIX",
            "DEFAULT_VERSION_SUFFIX",
        )
        if name not in constants
    ]
    if missing:
        raise ValueError(f"missing version constants in {version_source_path}: {', '.join(missing)}")
    return (
        f"{constants['DEFAULT_VERSION_COMPATIBILITY']}."
        f"{constants['DEFAULT_VERSION_FEATURE']}."
        f"{constants['DEFAULT_VERSION_BUGFIX']}"
        f"{constants['DEFAULT_VERSION_SUFFIX']}"
    )


def _append(
    findings: list[Finding],
    code: str,
    message: str,
    path: str,
    severity: str = "error",
) -> None:
    findings.append(Finding(code=code, message=message, path=path, severity=severity))


def _validate_schema_fallback(manifest: Any, findings: list[Finding]) -> None:
    """Small no-dependency fallback for the official schema fields this repo uses."""

    if not isinstance(manifest, dict):
        _append(findings, "schema_error", "server.json must be an object", "$")
        return

    for key in ("name", "description", "version"):
        if key not in manifest:
            _append(findings, "schema_error", f"{key!r} is a required property", "$")
    name = manifest.get("name")
    if isinstance(name, str):
        if len(name) < 3 or len(name) > 200 or not re.match(r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$", name):
            _append(findings, "schema_error", "name does not match the official server name pattern", "$.name")
    elif name is not None:
        _append(findings, "schema_error", "name must be a string", "$.name")

    description = manifest.get("description")
    if isinstance(description, str):
        if not (1 <= len(description) <= 100):
            _append(findings, "schema_error", "description length must be between 1 and 100", "$.description")
    elif description is not None:
        _append(findings, "schema_error", "description must be a string", "$.description")

    version = manifest.get("version")
    if version is not None and not isinstance(version, str):
        _append(findings, "schema_error", "version must be a string", "$.version")

    repository = manifest.get("repository")
    if repository is not None:
        if not isinstance(repository, dict):
            _append(findings, "schema_error", "repository must be an object", "$.repository")
        else:
            for key in ("url", "source"):
                if key not in repository:
                    _append(findings, "schema_error", f"repository.{key} is required", "$.repository")
                elif not isinstance(repository[key], str):
                    _append(findings, "schema_error", f"repository.{key} must be a string", f"$.repository.{key}")

    packages = manifest.get("packages")
    if packages is not None:
        if not isinstance(packages, list):
            _append(findings, "schema_error", "packages must be an array", "$.packages")
        else:
            for index, package in enumerate(packages):
                package_path = f"$.packages[{index}]"
                if not isinstance(package, dict):
                    _append(findings, "schema_error", "package must be an object", package_path)
                    continue
                for key in ("registryType", "identifier", "transport"):
                    if key not in package:
                        _append(findings, "schema_error", f"{key!r} is a required package property", package_path)
                transport = package.get("transport")
                if not isinstance(transport, dict):
                    _append(findings, "schema_error", "transport must be an object", f"{package_path}.transport")
                elif transport.get("type") not in {"stdio", "streamable-http", "sse"}:
                    _append(findings, "schema_error", "transport.type is unsupported", f"{package_path}.transport.type")


def _validate_against_schema(manifest: Any, schema: Any, findings: list[Finding]) -> None:
    if jsonschema is None:
        _append(
            findings,
            "schema_validator_unavailable",
            "jsonschema is unavailable; using a minimal no-dependency fallback for the vendored official schema fields",
            "$",
            severity="warning",
        )
        _validate_schema_fallback(manifest, findings)
        return

    validator = jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.Draft7Validator.FORMAT_CHECKER,
    )
    for error in sorted(validator.iter_errors(manifest), key=lambda item: list(item.absolute_path)):
        _append(
            findings,
            "schema_error",
            error.message,
            _json_path(list(error.absolute_path)),
        )


def _is_supported_oci_host(host: str) -> bool:
    host = host.lower()
    return (
        host in SUPPORTED_OCI_HOSTS
        or host.endswith(".pkg.dev")
        or host.endswith(".azurecr.io")
    )


def _host_from_registry_base_url(registry_base_url: str) -> str:
    parsed = urlparse(registry_base_url)
    return (parsed.netloc or parsed.path).lower()


def _oci_host_from_identifier(identifier: str) -> str:
    return identifier.split("/", 1)[0].lower()


def _oci_tag_from_identifier(identifier: str) -> str | None:
    image = identifier.rsplit("/", 1)[-1]
    if "@sha256:" in image:
        return None
    if ":" not in image:
        return None
    return image.rsplit(":", 1)[1]


def _validate_registry_constraints(manifest: dict[str, Any], findings: list[Finding]) -> None:
    if manifest.get("$schema") != SERVER_SCHEMA_URL:
        _append(
            findings,
            "schema_url_mismatch",
            f"$schema must be {SERVER_SCHEMA_URL}",
            "$.$schema",
        )

    if manifest.get("name") != EXPECTED_SERVER_NAME:
        _append(
            findings,
            "registry_name_mismatch",
            f"server name must be {EXPECTED_SERVER_NAME}",
            "$.name",
        )

    packages = manifest.get("packages")
    if not isinstance(packages, list) or not packages:
        _append(findings, "missing_packages", "server.json must include at least one package", "$.packages")
        return

    server_version = manifest.get("version")
    for index, package in enumerate(packages):
        if not isinstance(package, dict):
            continue
        package_path = f"$.packages[{index}]"
        registry_type = package.get("registryType")
        registry_base_url = package.get("registryBaseUrl")
        identifier = str(package.get("identifier", ""))

        if registry_type not in SUPPORTED_REGISTRY_TYPES:
            _append(
                findings,
                "unsupported_registry_type",
                f"registryType {registry_type!r} is not supported by the official registry readiness gate",
                f"{package_path}.registryType",
            )
            continue

        if package.get("version") not in (None, server_version):
            _append(
                findings,
                "package_version_drift",
                "package version must match top-level server.json version",
                f"{package_path}.version",
            )

        if registry_type == "oci":
            if not identifier.startswith(EXPECTED_PACKAGE_IDENTIFIER_PREFIX):
                _append(
                    findings,
                    "package_identifier_mismatch",
                    f"OCI identifier must start with {EXPECTED_PACKAGE_IDENTIFIER_PREFIX}",
                    f"{package_path}.identifier",
                )
            host = _oci_host_from_identifier(identifier)
            if not _is_supported_oci_host(host):
                _append(
                    findings,
                    "unsupported_oci_registry",
                    f"OCI registry host {host!r} is not allowlisted by the official registry",
                    f"{package_path}.identifier",
                )
            if isinstance(registry_base_url, str):
                base_host = _host_from_registry_base_url(registry_base_url)
                if not _is_supported_oci_host(base_host):
                    _append(
                        findings,
                        "unsupported_registry_base_url",
                        f"OCI registryBaseUrl host {base_host!r} is not allowlisted by the official registry",
                        f"{package_path}.registryBaseUrl",
                    )
                elif base_host != host:
                    _append(
                        findings,
                        "registry_base_url_identifier_mismatch",
                        "OCI registryBaseUrl host must match the identifier host",
                        f"{package_path}.registryBaseUrl",
                    )
            tag = _oci_tag_from_identifier(identifier)
            if tag is None:
                _append(
                    findings,
                    "missing_oci_version_tag",
                    "OCI identifier must include an immutable version tag for registry readiness",
                    f"{package_path}.identifier",
                )
            elif tag == "latest" or tag != server_version:
                _append(
                    findings,
                    "oci_tag_version_drift",
                    "OCI identifier tag must match top-level server.json version and must not be latest",
                    f"{package_path}.identifier",
                )
        elif isinstance(registry_type, str):
            allowed = SUPPORTED_REGISTRY_BASE_URLS.get(registry_type, set())
            if isinstance(registry_base_url, str) and registry_base_url not in allowed:
                _append(
                    findings,
                    "unsupported_registry_base_url",
                    f"registryBaseUrl {registry_base_url!r} is not allowed for {registry_type}",
                    f"{package_path}.registryBaseUrl",
                )


def _validate_meta(manifest: dict[str, Any], findings: list[Finding]) -> None:
    meta = manifest.get("_meta")
    if meta is None:
        return
    if not isinstance(meta, dict):
        return
    keys = set(meta)
    if keys - {PUBLISHER_META_KEY}:
        _append(
            findings,
            "disallowed_meta_key",
            f"official registry preserves only _meta.{PUBLISHER_META_KEY}",
            "$._meta",
        )
    publisher_meta = meta.get(PUBLISHER_META_KEY)
    if publisher_meta is None:
        return
    encoded = json.dumps(publisher_meta, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > PUBLISHER_META_MAX_BYTES:
        _append(
            findings,
            "publisher_meta_too_large",
            f"publisher-provided _meta is {len(encoded)} bytes; limit is {PUBLISHER_META_MAX_BYTES}",
            f"$._meta.{PUBLISHER_META_KEY}",
        )


def _walk_json(value: Any, path: list[str | int] | None = None):
    path = [] if path is None else path
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_json(child, [*path, str(key)])
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_json(child, [*path, index])


def _validate_no_secrets_or_host_paths(manifest: Any, findings: list[Finding]) -> None:
    for path, value in _walk_json(manifest):
        rendered_path = _json_path(path)
        if isinstance(value, dict) and value.get("isSecret") is True:
            _append(
                findings,
                "secret_input_metadata",
                "checked-in registry metadata must not declare secret inputs for this server",
                rendered_path,
            )
        if not isinstance(value, str):
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                _append(
                    findings,
                    "secret_looking_value",
                    "server.json contains a secret-looking literal value",
                    rendered_path,
                )
                break
        for pattern in HOST_ABSOLUTE_PATH_PATTERNS:
            if pattern.search(value):
                _append(
                    findings,
                    "host_absolute_path",
                    "server.json contains a host absolute path; use variables/placeholders instead",
                    rendered_path,
                )
                break


def _dockerfile_oci_label(dockerfile_text: str) -> str | None:
    label_re = re.compile(
        r"(?m)^\s*LABEL\s+.*?\b"
        + re.escape(OCI_OWNERSHIP_LABEL)
        + r"=(?:\"([^\"]+)\"|'([^']+)'|([^\\\s]+))"
    )
    match = label_re.search(dockerfile_text)
    if not match:
        return None
    for group in match.groups():
        if group is not None:
            return group
    return None


def _validate_ownership_marker(
    manifest: dict[str, Any],
    dockerfile_path: Path,
    findings: list[Finding],
) -> None:
    packages = manifest.get("packages")
    has_oci = any(isinstance(item, dict) and item.get("registryType") == "oci" for item in packages or [])
    if not has_oci:
        return
    try:
        dockerfile_text = dockerfile_path.read_text(encoding="utf-8")
    except OSError as exc:
        _append(
            findings,
            "dockerfile_unreadable",
            f"unable to read Dockerfile for OCI ownership marker validation: {exc}",
            str(dockerfile_path),
        )
        return
    label_value = _dockerfile_oci_label(dockerfile_text)
    if label_value is None:
        _append(
            findings,
            "missing_oci_ownership_label",
            f"Dockerfile must set LABEL {OCI_OWNERSHIP_LABEL}=\"{manifest.get('name')}\"",
            str(dockerfile_path),
        )
    elif label_value != manifest.get("name"):
        _append(
            findings,
            "oci_ownership_label_mismatch",
            f"Dockerfile {OCI_OWNERSHIP_LABEL} label must match server.json name",
            str(dockerfile_path),
        )


def _validate_version_drift(
    manifest: dict[str, Any],
    version_source_path: Path,
    findings: list[Finding],
    expected_version: str | None = None,
) -> None:
    if expected_version is None:
        try:
            expected = expected_default_version(version_source_path)
        except (OSError, ValueError) as exc:
            _append(
                findings,
                "version_source_unreadable",
                str(exc),
                str(version_source_path),
            )
            return
        source_label = "default source/version_metadata.py rendering"
    else:
        expected = expected_version
        source_label = "requested release version"
    if manifest.get("version") != expected:
        _append(
            findings,
            "version_drift",
            f"server.json version must match {source_label} {expected!r}",
            "$.version",
        )


def validate_registry_readiness(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    dockerfile_path: Path = DEFAULT_DOCKERFILE_PATH,
    version_source_path: Path = DEFAULT_VERSION_SOURCE_PATH,
    expected_version: str | None = None,
) -> dict[str, Any]:
    findings: list[Finding] = []

    try:
        manifest = _load_json(manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        _append(findings, "manifest_unreadable", str(exc), str(manifest_path))
        return _report(manifest_path, schema_path, findings)

    try:
        schema = _load_json(schema_path)
    except (OSError, json.JSONDecodeError) as exc:
        _append(findings, "schema_unreadable", str(exc), str(schema_path))
        return _report(manifest_path, schema_path, findings)

    if isinstance(schema, dict) and schema.get("$id") != SERVER_SCHEMA_URL:
        _append(
            findings,
            "vendored_schema_id_mismatch",
            f"vendored schema $id must be {SERVER_SCHEMA_URL}",
            str(schema_path),
        )

    _validate_against_schema(manifest, schema, findings)
    if isinstance(manifest, dict):
        _validate_registry_constraints(manifest, findings)
        _validate_meta(manifest, findings)
        _validate_no_secrets_or_host_paths(manifest, findings)
        _validate_ownership_marker(manifest, dockerfile_path, findings)
        _validate_version_drift(manifest, version_source_path, findings, expected_version)
    else:
        _append(findings, "manifest_not_object", "server.json must be a JSON object", "$")

    return _report(manifest_path, schema_path, findings)


def _report(manifest_path: Path, schema_path: Path, findings: list[Finding]) -> dict[str, Any]:
    error_count = sum(1 for finding in findings if finding.severity == "error")
    return {
        "schema": "mcp_registry_readiness.v1",
        "ok": error_count == 0,
        "manifest_path": str(manifest_path),
        "schema_path": str(schema_path),
        "schema_url": SERVER_SCHEMA_URL,
        "finding_count": len(findings),
        "error_count": error_count,
        "findings": [finding.as_dict() for finding in findings],
    }


def _print_text_report(report: dict[str, Any], *, compact: bool) -> None:
    status = "PASS" if report["ok"] else "FAIL"
    print(
        f"{status} MCP Registry server.json readiness: "
        f"findings={report['finding_count']} errors={report['error_count']}"
    )
    if compact:
        for finding in report["findings"]:
            print(f"{finding['severity'].upper()} {finding['code']} {finding['path']}: {finding['message']}")
        return
    if report["findings"]:
        print(json.dumps(report["findings"], indent=2, sort_keys=True))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    validate = subparsers.add_parser("validate", help="validate server.json without publishing")
    validate.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    validate.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH)
    validate.add_argument("--dockerfile", type=Path, default=DEFAULT_DOCKERFILE_PATH)
    validate.add_argument("--version-source", type=Path, default=DEFAULT_VERSION_SOURCE_PATH)
    validate.add_argument(
        "--expected-version",
        help="release version expected in server.json and package tags; defaults to source/version_metadata.py defaults",
    )
    validate.add_argument("--json", action="store_true", help="print the full machine-readable report")
    validate.add_argument("--compact", action="store_true", help="print one-line status plus bounded findings")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["validate", *(argv or [])])

    report = validate_registry_readiness(
        manifest_path=args.manifest,
        schema_path=args.schema,
        dockerfile_path=args.dockerfile,
        version_source_path=args.version_source,
        expected_version=args.expected_version,
    )
    if args.json:
        print(json.dumps(report, indent=None if args.compact else 2, sort_keys=True))
    else:
        _print_text_report(report, compact=args.compact)
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint.
    raise SystemExit(main())
