<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Self-optimization efficiency report

`self_optimization_report` is the repo-local, on-demand report for measuring how the software team uses `codebase-tooling-mcp` on this repository. It is intended for the self-optimization loop from issue-driven development: inspect recent MCP/agent usage, estimate time and token savings, identify blockers/noisy runs/rework, and turn non-duplicate findings into follow-up optimization issues.

## When to run it

Run the tool directly, rather than through a natural-language router, when the question is specifically about MCP usage efficiency on the current repository:

```text
self_optimization_report(window_hours=168, export=true)
```

Good checkpoints:

- after a batch of issue/PR work;
- after failed, noisy, or repeatedly retried MCP workflows;
- before creating workflow/process optimization issues;
- before software-team retrospectives where token savings, elapsed time, cache use, or throughput are discussed.

Use `task_router(mode="workflow_select", ...)` only when the operator is unsure which workflow to use. For this report, the direct tool is the canonical entrypoint.

## Inputs and sources

The report is offline/repo-local. It does not call GitHub, model APIs, package indexes, or other network services.

It reads available local evidence only:

- redacted MCP audit events from `MCP_AUDIT_LOG_FILE`;
- local OpenTelemetry JSONL spans from `MCP_OTEL_SPANS_FILE` when tracing was enabled;
- persisted async task handles under `.codebase-tooling-mcp/tasks/`;
- local cache metadata under `.codebase-tooling-mcp/cache/`;
- local `git log` metadata for issue/PR throughput when `include_git=true`.

Use `start_time`/`end_time` for exact ISO-8601 windows, or `window_hours` for a recent rolling window.

## Output

The tool returns `self_optimization_report.v1` with:

- elapsed/spent baseline estimates and observed span duration where available;
- token usage and token-savings estimates where local token/cache/compression fields are available;
- backend/model/execution-mode routing counts when present in local spans or audit-safe arguments;
- cache entry counts, observed cache hits, and conservative cache savings estimates;
- compressed-observation counts and estimated token savings from omitted signals;
- failed/noisy run counts and bottleneck summaries;
- issue/PR/workflow throughput attribution from local refs such as `issue #90` or `PR #12`;
- duplicate-suppressed optimization candidates with stable `duplicate_key` values.

`export=true` writes redacted JSON and Markdown artifacts under `.codebase-tooling-mcp/reports/` and returns resource links.

## Privacy and redaction

The report does not expose raw traces, raw prompts, file contents, bearer tokens, or absolute host paths. It passes strings through the MCP audit redactor and additionally redacts configured sensitive project/company/person terms.

Optional extra terms can be supplied per run:

```text
self_optimization_report(window_hours=168, redact_terms=["CustomerName", "Person Name"])
```

The tool also derives local redaction terms from Git config and remotes when possible, so report output should not rely on raw organization, repository, or person names for grouping. Use issue/PR numbers and workflow names for attribution instead.

## Duplicate suppression

Optimization candidates are recommendations, not automatic GitHub issues. Each candidate has a stable `duplicate_key`. The tool suppresses duplicates within the same report and against prior local self-optimization report exports or the optional local recommendation index at `.codebase-tooling-mcp/reports/SELF_OPTIMIZATION_RECOMMENDATIONS.json`.

When turning a candidate into a GitHub issue, first check the current project board/issues and only file unsuppressed candidates that still match team priorities.
