<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow phase telemetry

`workflow_phase_telemetry` is a read-only, local report for understanding the shape of MCP agent workflows without changing tool execution behavior.

It complements `self_optimization_report` and OpenTelemetry spans by showing phase-level workload structure rather than only aggregate time, token, or throughput totals.

## Usage

For caller-provided redacted summaries:

```text
workflow_phase_telemetry(workflow_summary_json='{"tool_calls":[...]}')
```

When no summary JSON is supplied, the report reads the same repository-local redacted sources used by self-optimization:

- `.codebase-tooling-mcp/audit/security_events.jsonl`
- `.codebase-tooling-mcp/traces/otel_spans.jsonl`
- `.codebase-tooling-mcp/tasks/*.json`

The default window is the last 168 hours. Local source reading is bounded to the repository and uses no network.

## Phases

Tool calls are classified into stable phase keys:

- `discover_read` for repository exploration and read-only context gathering;
- `analyze_plan` for planning, summarization, policy, risk, and diagnostic work;
- `mutate_write` for write-capable workspace, git, shell, or replay actions;
- `verify_test` for test, lint, build, impact, and verification markers;
- `review_release` for release readiness, governance, catalog, and security review gates;
- `cleanup` for rollback, restore, cancel, prune, or cleanup activity;
- `other` for unclassified redacted records.

## Output

The report returns `workflow_phase_telemetry.v1` with:

- phase counts and optional duration totals;
- cacheability counts, cache-hit counts, repeated-read ratios, and repeated uncached read anomalies;
- guard, test, release-gate, and write markers;
- write-after-read and post-write verification ordering signals;
- optional hashed trace IDs and workflow checkpoint counts when present;
- actionable optimization hints for cache placement, mutation guard placement, and post-write verification.

## Privacy and safety

The report is advisory-only and local-only. It does not persist or return raw prompts, raw tool outputs, file contents, secret-like values, bearer tokens, or absolute host paths. Trace IDs are represented as short hashes. Caller-provided summaries should already be redacted; the report still avoids echoing raw argument/output fields and applies the existing self-optimization redactors to labels and metadata.

Use this report to tune tool ordering, caching, and guard placement. Do not treat it as permission to mutate, release, upload telemetry, or bypass existing policy gates.
