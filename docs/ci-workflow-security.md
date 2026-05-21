<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# GitHub Actions workflow security report

`ci_workflow_security_report` is an offline, read-only posture check for repository-local GitHub Actions workflows under `.github/workflows/*.yml` and `.github/workflows/*.yaml`.

The schema is `ci_workflow_security_report.v1`. It returns `ok`, `status`, checked workflow count, findings grouped by severity, action-reference classifications, and repository-relative evidence with secret names/values and host absolute paths redacted.

The first slice flags advisory signals for:

- missing, broad, or elevated `permissions:` blocks;
- mutable remote action references instead of full-length SHA pins;
- risky triggers such as `pull_request_target` and `workflow_run`;
- self-hosted runners and privileged Docker/container usage;
- Docker/Buildx, secret-bearing publish steps, OIDC/id-token, and artifact upload/download paths;
- malformed workflow YAML and missing workflow evidence.

A clean report is not proof that a workflow is secure. Status values distinguish `clean`, `warnings`, `findings`, `parse-error`, and `no-workflows` so release/governance callers can tell unknown evidence apart from checked posture.

## Suppressions and allowlists

Use `.github/ci-workflow-security.yml` or `.codebase-tooling-mcp/ci-workflow-security.yml` for deliberate, time-bound exceptions:

```yaml
suppressions:
  - id: mutable-third-party-action-ref
    path: .github/workflows/ci.yml
    rationale: Temporary exception while SHA pin automation is rolled out.
    expires: 2026-12-31

action_ref_allowlist:
  - actions/checkout
```

Suppressions must include a rule id, rationale, and unexpired `expires` date. Optional `path`, `line`, and `contains` fields narrow a suppression match. Action allowlists only suppress mutable action-ref findings; broader risk exceptions should use suppressions with rationale and expiry.

## SARIF export

With `export=true`, the workflow writes JSON, Markdown, and SARIF 2.1.0 artifacts under `.codebase-tooling-mcp/reports/`, each with an adjacent `mcp_artifact_provenance.v1` sidecar. Clean workflow reports still write a SARIF run with zero results so downstream jobs can distinguish "checked clean" from "no artifact".

SARIF results use stable `ci-workflow-security/<rule-id>` rule IDs, repository-relative `artifactLocation.uri` values, severity-to-level mapping, rule help text, and deterministic partial fingerprints derived from redacted rule/path/line evidence. The export is offline-only: it does not upload to GitHub code scanning, use network access, include host absolute paths, expose bearer tokens, reveal secret names/values, or persist raw prompts/transcripts. Maintainers who want code-scanning alerts can upload the generated `.sarif` from a separate GitHub Actions workflow using GitHub's `upload-sarif` action.

`governance_report` embeds a compact `ci_workflow_security` section. `release_readiness` runs the check inline and reports workflow posture separately from dependency-security freshness.
