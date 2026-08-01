<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow event-log checkpoint report

`workflow_event_log_report` is a read-only, audit-first projection for compact workflow event logs. It consumes a repository-local JSONL log, normalizes typed workflow events, and returns `workflow_checkpoint_report.v1` with:

- typed `workflow_event.v1` rows for workflow lifecycle, tool summaries, guard decisions, catalog fingerprints, mutation proposals, test gates, release gates, checkpoints, artifacts, and fork markers;
- `workflow_checkpoint.v1` checkpoint summaries with redacted evidence and artifact references;
- `workflow_event_projection.v1`, a deterministic compact timeline plus artifact lineage rebuilt from the log without rerunning tools;
- `workflow_fork_diff.v1`, a local fork-vs-parent comparison for what-if branches;
- privacy metadata proving raw prompts, raw tool outputs, secrets/tokens, and host absolute paths were not persisted in the projected event log.

The default input is `.codebase-tooling-mcp/workflow-events.jsonl`. Callers may pass a repository-relative fixture or audit-log path. Missing and corrupt JSONL lines return explicit `missing`, `invalid`, or `corrupt` status instead of throwing away the rest of the usable log.

## Event shape

Each JSONL row should be a compact object. The stable fields are:

```json
{
  "event_id": "evt-tests",
  "sequence": 5,
  "event_type": "test.gate",
  "timestamp": "2026-05-31T08:00:06Z",
  "workflow_id": "wf-189",
  "checkpoint_id": "cp-tests",
  "fork_id": "main",
  "parent_fork_id": "",
  "status": "failed",
  "evidence_refs": [
    {"ref_id": "test-evidence", "kind": "pytest", "digest": "sha256:...", "summary": "2 failed tests, names only"}
  ],
  "artifact_refs": [
    {"artifact_id": "test-report", "kind": "test_gate", "digest": "sha256:...", "path": ".codebase-tooling-mcp/reports/test-gate.json"}
  ]
}
```

Evidence and artifact references are references only: repo-relative paths, digests, summaries, trace ids, and redaction status. They must not contain raw prompt text, raw tool output, bearer tokens, environment values, or host absolute paths.

## Fork comparison

Fork markers use `event_type: "workflow.fork"` with `fork_id`, `parent_fork_id`, and `parent_checkpoint_id`. When `workflow_event_log_report(fork_id="safer-branch", parent_fork_id="main")` is called, the report compares event counts and artifact digests between those local branches so maintainers can inspect what changed after a checkpoint without re-executing expensive or mutating steps.

## How this differs from adjacent mechanisms

- **Transport replay** resumes or replays MCP transport messages for clients. This report does not replay protocol messages and does not drive tool execution; it only projects a local audit log into an auditable view.
- **Trace spans** describe runtime observability timing and parent/child execution context. This report may reference redacted trace ids, but its source of truth is the typed event log and checkpoint sequence, not an OpenTelemetry span tree.
- **Mutation idempotency guards** suppress duplicate mutating requests and protect request semantics. This report is not a mutation guard and grants no permission; it records redacted decisions and artifacts so humans can audit or compare branches later.

The first slice is intentionally additive and fixture-driven. It is suitable for local audit replay and what-if comparison, not for restoring runtime state or guaranteeing external side effects.
