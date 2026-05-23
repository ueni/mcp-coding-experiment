<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Agent security delta report

`agent_security_delta_report` is a local, read-only security-regression pack for agent-generated feature changes. It compares changed files between `base_ref` and `head_ref`, runs deterministic offline heuristics over the relevant file types, and reports whether the patch introduces new security findings before release or review.

The report is intentionally conservative. It is designed to catch obvious patch regressions and to create review/SARIF evidence; it is not a proof that a change is vulnerability-free.

## Scope

The default changed-file pack covers Python source/tests/scripts, common JSON/YAML/TOML configuration, Dockerfiles, GitHub Actions workflows, and devcontainer configuration. Default exclusions skip `.git`, generated MCP report/cache files, Python caches, virtual environments, and `node_modules`.

Current rules cover:

- command-injection patterns such as `os.system`, `os.popen`, and `subprocess(..., shell=True)`;
- path traversal risk from request/input-controlled file access;
- dynamic code execution through `eval`/`exec`;
- unsafe deserialization through pickle-like loaders and unsafe YAML loading;
- dynamic SQL string construction;
- weak temporary-file creation;
- new HTTP route surfaces without an obvious auth/permission guard;
- privileged container, Docker socket, host-network, and broad Linux capability settings.

## Configuration

Callers can set `base_ref`, `head_ref`, `include_globs`, `exclude_globs`, `block_on_severity`, `warn_on_severity`, and `export`. Severity thresholds use `info`, `low`, `medium`, `high`, or `critical`; the default blocks new/unknown `high` findings and warns on active `medium` findings. The shorthand `agent_security_delta` is an alias for the same report contract.

## Output and gate

The stable schema is `agent_security_delta_report.v1`. `status` is one of:

- `pass` - no findings at or above the warning threshold;
- `warn` - findings exist at or above `warn_on_severity` but none would block;
- `block` - a new or unknown finding exists at or above `block_on_severity`.

Findings include repository-relative paths, line numbers, severity, confidence, CWE/category, redacted evidence excerpts, stable fingerprints, and an `introduction` value of `new`, `pre_existing`, `unknown`, or `removed` (for `removed_findings`). The report never returns raw file contents, host absolute paths, secrets, or tokens.

## Exports

With `export=true`, the tool writes local artifacts under `.codebase-tooling-mcp/reports/`:

- JSON report;
- Markdown summary;
- SARIF 2.1.0 export for IDE/code-scanning consumers;
- local `mcp_artifact_provenance.v1` sidecar for the SARIF export.

Exports are local/offline only. The tool does not upload SARIF, call external scanners, or mutate the repository.

## Relationship to other gates

This pack is narrower than `mcp_threat_model_report`, which models MCP tool-poisoning and client-transparency threats instead of app-code regressions. It differs from `secret_exposure_report`, which searches for credential material and allowlist/baseline secret evidence rather than vulnerability patterns. It differs from `dependency_security_report`, which inventories packages and advisory data. Its SARIF output is an optional local interchange artifact generated from the same redacted findings; it is not a separate online code-scanning gate.

## Limitations

Rules are deterministic heuristics and intentionally avoid network access, package installation, runtime tracing, or auto-fixes. Findings should be reviewed by humans, especially for framework-specific auth conventions or safe wrapper functions that simple static matching cannot understand.

## Governance integration

`governance_report` embeds a compact `agent_security_delta` summary alongside dependency, CI workflow, and secret-exposure checks. This keeps the release/governance bundle aware of security regressions introduced by agent-generated feature patches without expanding governance reports with raw code or unredacted evidence.
