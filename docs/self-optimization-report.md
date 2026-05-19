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

The report is offline/repo-local by default. It does not call GitHub, model APIs, package indexes, or other network services unless GitHub issue updates are explicitly enabled with `github_issue_update_mode="apply"`, `github_repository`, a token environment variable, and server mutation permission.

It reads available local evidence only:

- redacted MCP audit events from `MCP_AUDIT_LOG_FILE`;
- local OpenTelemetry JSONL spans from `MCP_OTEL_SPANS_FILE` when tracing was enabled;
- persisted async task handles under `.codebase-tooling-mcp/tasks/`;
- local cache metadata under `.codebase-tooling-mcp/cache/`;
- local `git log` metadata for issue/PR throughput when `include_git=true`;
- optional caller-supplied `github_issue_metadata` or a local `.codebase-tooling-mcp/reports/SELF_OPTIMIZATION_GITHUB_ISSUES.json` issue index for de-duplicating candidates against already-open GitHub issues without a network lookup;
- optional structured local patch-survivorship metadata from `.codebase-tooling-mcp/reports/PATCH_SURVIVORSHIP_PR_METADATA.json` plus audit/span fields named `patch_survivorship`, `patch_lifecycle`, or `patch_metadata`.

Sibling E2E workflow benchmark summaries from `.codebase-tooling-mcp/reports/E2E_MCP_WORKFLOW_BENCHMARKS.json` can be used alongside this report to correlate benchmark pass/fail, tool-call volume, estimated token volume, retries/rework, safety-gate coverage, snapshot/rollback usage, and test-gate status with local usage metrics.

Use `start_time`/`end_time` for exact ISO-8601 windows, or `window_hours` for a recent rolling window.

## Output

The tool returns `self_optimization_report.v1` with:

- elapsed/spent baseline estimates and observed span duration where available;
- token usage and token-savings estimates where local token/cache/compression fields are available;
- backend/model/execution-mode routing counts when present in local spans or audit-safe arguments;
- first-class task buckets plus state-transition, test-gate, retry/rework, and blocked/waiting-time metrics when task/status artifacts include that evidence;
- cache entry counts, observed cache hits, and conservative cache savings estimates;
- compressed-observation counts and estimated token savings from omitted signals;
- failed/noisy run counts and bottleneck summaries;
- issue/PR/workflow/task throughput attribution from local refs such as `issue #90`, `PR #12`, or persisted task IDs;
- duplicate-suppressed optimization candidates with stable `duplicate_key` values;
- compact `patch_survivorship` data using schema `patch_survivorship_report.v1`.

The patch-survivorship extension aggregates proposed, applied, committed, rewritten, reverted, and `retained_after_n_commits` states by workflow, tool, and execution mode. It keeps only patch IDs or SHA-256 digests plus aggregate diff metrics (line/hunk/add/delete counts), structured local human-pushback labels/review decisions, and available correlations to test gates, security/risk artifacts, and governance artifacts. It does not persist raw prompts, full private patches, or private conversation text.

When evidence is absent, the report uses explicit `unknown` / `not_available` statuses in `metrics.data_availability`, lowers `confidence`, and adds caveats instead of inventing token usage, transition timing, test-gate coverage, or blocked-time savings.

`export=true` writes redacted JSON and Markdown artifacts under `.codebase-tooling-mcp/reports/` and returns resource links.

## Privacy and redaction

The report does not expose raw traces, raw prompts, full patch text, file contents, bearer tokens, or absolute host paths. It passes strings through the MCP audit redactor and additionally redacts configured sensitive project/company/person terms.

Optional extra terms can be supplied per run:

```text
self_optimization_report(window_hours=168, redact_terms=["CustomerName", "Person Name"])
```

The tool also derives local redaction terms from Git config and remotes when possible, so report output should not rely on raw organization, repository, or person names for grouping. Use issue/PR numbers and workflow names for attribution instead.

## Duplicate suppression and optional GitHub issue gating

Optimization candidates are recommendations, not automatic GitHub issues. Each candidate has a stable `duplicate_key`, confidence, and caveats. The tool suppresses duplicates within the same report, against prior local self-optimization report exports, against the optional local recommendation index at `.codebase-tooling-mcp/reports/SELF_OPTIMIZATION_RECOMMENDATIONS.json`, against a local `.codebase-tooling-mcp/reports/SELF_OPTIMIZATION_GITHUB_ISSUES.json` issue index, and against caller-supplied `github_issue_metadata`.

GitHub create/update behavior is gated:

- default `github_issue_update_mode="off"`: no issue writes and no network calls;
- `github_issue_update_mode="dry_run"`: plans create/update actions for high-confidence candidates only, still without contacting GitHub;
- `github_issue_update_mode="apply"`: requires explicit `github_repository`, a configured token environment variable, high candidate confidence, server mutation permission, and an authorized mutation-capable MCP session before creating a new issue or updating a matched issue with a comment.

When turning a candidate into a GitHub issue, first check the current project board/issues and only file unsuppressed high-confidence candidates that still match team priorities.

## #88 proxy/anonymizer/disclosure enrichment

The report does not depend on #88. If an explicit agent API proxy/anonymizer/disclosure/router policy is later available, its redacted aggregate routing, provider, disclosure, anonymization, and policy-decision metadata can enrich routing/disclosure metrics. Those inputs remain non-blocking and must stay aggregate/redacted; raw online-provider traces, prompts, responses, API keys, or disclosure logs must not be embedded in this report.
