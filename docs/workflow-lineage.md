<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow lineage manifests

`workflow_lineage.v1` is the first deterministic replayability slice for MCP workflows. The initial implementation covers `governance_report` and `workflow_task(workflow="governance_report")` because both share the same exported report path.

When `governance_report(export=true)` runs, it writes a repository-local lineage manifest next to the report artifacts:

```text
.codebase-tooling-mcp/reports/governance-report-...workflow-lineage.json
```

The manifest records:

- a stable plan id derived from redacted deterministic inputs;
- request constraints such as normalized time window, base/head refs, export mode, and compression mode;
- git base/head commit identities, not host absolute repository paths;
- lineage nodes for audit-window loading, git-ref resolution, redaction/policy aggregation, diagnostics summary, and observed report artifact emission;
- edges connecting those nodes;
- artifact references for the governance report JSON and Markdown outputs, including SHA-256 digests;
- explicit non-deterministic-node markers for generated report artifacts whose names/timestamps or future model-authored summaries are observed outputs rather than promised bit-for-bit regenerations.

Security boundaries:

- no raw prompts, transcript snippets, secrets, file contents, bearer tokens, or host absolute paths are written to the lineage manifest;
- free-form strings pass through the same audit redaction helpers used by governance reports;
- repository-local absolute paths are normalized to repository-relative paths, and external absolute paths are replaced with placeholders;
- lineage is local replay metadata only and does not replace OpenTelemetry tracing, SLSA/GitHub attestations, Sigstore/cosign signing, or external governance backends.

Use `workflow_lineage(mode="verify", manifest_path="...")` to recompute the deterministic plan inputs read-only and compare recorded artifact digests. Verification reports:

- `matched` - plan identity and recorded artifacts still match;
- `input_changed` - deterministic inputs such as audit digest, time window, refs, or git commits no longer match;
- `artifact_changed` - one or more recorded artifacts are missing or have different digests;
- `non_deterministic_node` - the manifest contains observed-output nodes that are intentionally not claimed as deterministic.

Governance report JSON/Markdown provenance sidecars link back to the lineage manifest through `links.workflow_lineage`.
