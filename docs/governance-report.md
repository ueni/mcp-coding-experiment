<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Governance report workflow

`governance_report` is a read-only MCP workflow for enterprise audit review. It reads redacted events from `MCP_AUDIT_LOG_FILE`, summarizes existing governance artifacts, and exports JSON plus Markdown reports under `.codebase-tooling-mcp/reports/`.

The first-slice schema is `governance_report.v1`. It includes:

- audit counts for sensitive tool calls, blocked attempts, mutation-gate failures, HTTP authorization denials, tool categories, and failure reasons;
- hash-chain digest metadata over redacted audit events (`sha256`, no raw secrets);
- local governance hook summaries from stored `policy_simulator`, `release_readiness`, and `required_tool_chain` results when available;
- snapshot/rollback references from the state snapshot index when available;
- a compact `workflow_diagnostics` summary for failed audit trajectories when blocked steps are present;
- git base/head metadata for PR or release review;
- `resource_links` plus `_meta.artifact_resources` entries for exported JSON and Markdown files, including repository-relative URI/path, MIME type, size when known, created time, and redaction/safety metadata.

Example call:

```json
{
  "start_time": "2026-05-12T00:00:00+00:00",
  "end_time": "2026-05-12T23:59:59+00:00",
  "base_ref": "main",
  "head_ref": "HEAD",
  "export": true
}
```

`release_readiness(summary_mode="quick")` surfaces a non-blocking `governance_report` check showing whether a recent report exists. A missing report is informational by default and does not fail release readiness.

Security boundaries:

- report generation does not require mutation mode;
- relative audit/report paths are resolved inside `REPO_PATH`;
- absolute audit paths outside the repository are not read by the report workflow;
- secrets and tokens are redacted before aggregation and export;
- resource links expose only repository-relative `repo://file/{path}` paths, never host absolute paths or raw secret-bearing inputs;
- external OPA or Agent Governance Toolkit integrations are intentionally out of scope for this first slice.
