<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Dependency security report

`dependency_security_report` is a read-only workflow for dependency inventory, SBOM export, and vulnerability-gate evidence. It inspects repository-local dependency declarations such as `requirements*.txt`, `constraints*.txt`, and `pyproject.toml`; it never edits dependency files, installs packages, or auto-fixes versions.

Default behavior is offline-safe. If no advisory fixture or caller-provided scanner report is supplied, the report returns `status="network-disabled"` (or `skipped` when no dependency inputs are available) instead of implying the dependency set is clean.

## Inputs and advisory sources

Use one or both offline/caller-provided advisory sources:

- `advisory_fixture_path`: repository-relative JSON with `schema`, `generated_at`, and `advisories`. Each advisory should include `package`, `id`, `affected_versions` (PEP 440 specifier such as `<2.0`), optional `severity`, `fixed_versions`, `summary`, and `url`.
- `pip_audit_json_path`: repository-relative `pip-audit --format json` output produced by a caller or CI job. The MCP server consumes the JSON but does not run the scanner in this first slice.

Set `allow_network=true` only as an explicit signal that online advisory lookup is permitted by the caller. The current implementation still requires caller-provided JSON and reports `scanner-unavailable` when no report is supplied; this preserves repository boundaries and avoids hidden cache/network writes. To refresh advisory data online, run a scanner outside the MCP server (for example in CI), save the JSON under the repository, then pass its path to `dependency_security_report`.

## Status values

- `clean`: fresh advisory data was available and no vulnerabilities matched resolved dependency versions.
- `vulnerable`: one or more advisories matched resolved dependency versions.
- `skipped`: no dependency inputs, or only unresolved/unpinned inputs, were available for matching.
- `stale-cache`: advisory data exists but is older than `advisory_max_age_hours`.
- `network-disabled`: no advisory source was supplied and network lookup was not enabled.
- `scanner-unavailable`: online lookup was requested but no caller-provided scanner report was supplied.

`gate.blocking_enabled` defaults to false. Enable blocking explicitly per call with `block_on_vulnerabilities=true` or by setting `MCP_DEPENDENCY_SECURITY_BLOCKING=true`; otherwise vulnerable/stale/not-checked states are informational warnings for release/governance reports.

## Artifacts

With `export=true`, the workflow writes:

- `.codebase-tooling-mcp/reports/dependency-security-report-*.json`
- `.codebase-tooling-mcp/reports/dependency-security-report-*.sarif`
- `.codebase-tooling-mcp/reports/dependency-security-report-*.sbom.cdx.json` when `include_sbom=true`
- adjacent `mcp_artifact_provenance.v1` sidecars for all exported artifacts

Artifacts are linked through `artifact_resource_link.v1` and use repository-relative `repo://file/...` URIs. The SARIF export is SARIF 2.1.0, offline/no-upload by default, uses stable `dependency-security/known-vulnerability` rule IDs, repository-relative file/line locations for declared vulnerable requirements, severity/help metadata, and deterministic partial fingerprints based on redacted rule/path/line/advisory context. Clean dependency reports still write a SARIF run with zero results. The SBOM is CycloneDX-compatible JSON generated from declared or caller-provided dependency metadata. Unsupported, option, VCS, URL, and direct-reference requirement lines are reported with safe diagnostics only; credentials, URLs, and host absolute paths are redacted before return or export, including SARIF and provenance sidecars.

`release_readiness` includes a compact non-blocking `dependency_security` check, and `governance_report` summarizes the latest exported dependency security report when present.
