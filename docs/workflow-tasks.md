<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Async workflow tasks

`workflow_task` is a compact prototype for MCP Tasks-style async handles around
long-running repository workflows. It starts a supported workflow in the
background and immediately returns a stable `task_id` plus the first persisted
status record.

Initial supported workflows:

- `governance_report` - read-only audit/governance report generation, including
  JSON/Markdown artifact resource links, local provenance sidecars, and the
  linked `workflow_lineage.v1` manifest when `export=true`.
- `vscode_task_run` - starts one approved VS Code task by label. Because it is a
  mutating/shell workflow, it is explicitly marked `no_auto_retry=true`; callers
  must request human approval before rerunning failed or expired results.

## Progress, cancellation, and polling

When a client supplies MCP `_meta.progressToken` on `workflow_task(action="start")`,
the server emits best-effort `notifications/progress` updates for the persisted
task lifecycle. Progress values are coarse, monotonic percentages, rate-limited
for non-terminal updates, and terminal states force a final `100/100` update.
Clients that do not support progress notifications continue to use persisted
polling without behavior changes.

For Streamable HTTP clients, the server enables the MCP SDK's resumable SSE path
for this workflow-task progress slice. Keep the `Mcp-Session-Id` returned by the
server and, after a disconnect, reconnect to the same MCP stream with
`Last-Event-ID` set to the last received SSE `id`. The replay journal is local,
bounded, and scoped to the original MCP session plus workflow task request
stream; it retains priming markers plus redacted workflow-task
lifecycle/progress messages, not raw prompts, bearer tokens, stdout/stderr, or
host paths. If the event id has expired, belongs to a different MCP session, or
the current SDK/client path cannot resume the stream, keep the task running and
call `task_status(task_id="task-...")` to recover the latest persisted state.

Cancel a running task with:

```text
workflow_task(action="cancel", task_id="task-...", cancel_reason="optional redacted reason")
```

The cancel path records `cancel_requested` / `cancelled` audit metadata, redacts
the reason before persistence, and best-effort terminates active
`vscode_task_run` subprocesses. Protocol `notifications/cancelled` messages are
also mapped back to the originating workflow task when the request id is still
known. Cancellation is race-safe but best-effort: if the workflow already reached
a terminal state, the status remains terminal and the cancel request is recorded
as ignored.

Call `task_status(task_id="task-...")` to read the latest status from:

```text
.codebase-tooling-mcp/tasks/<task_id>.json
```

Status records use `workflow_task.v1` and include:

- `status`: `pending`, `running`, `cancel_requested`, `succeeded`, `failed`,
  `cancelled`, or `expired`
- timestamps: `created_at`, `started_at`, `updated_at`, `finished_at`,
  `expires_at`, `retention_expires_at`
- `progress`: coarse phase/percent fields for current clients
- `result`: compact final workflow summary when available, including small
  fields such as exit code, timeout state, and output character/line counts;
  large VS Code task stdout/stderr/build-log details are written to redacted
  result artifacts and referenced from this summary instead of being embedded in
  the status record
- `artifact_references`: generated artifact resource links using
  `artifact_resource_link.v1`
- `resource_links` / `_meta`: resource link for the task status artifact itself
- `cancellation`: redacted best-effort cancellation metadata when cancellation
  is requested or ignored
- lifecycle retry metadata: `retry_policy` (`none`, `client_may_retry`,
  `server_retrying`, or `human_required`), `retry_after_seconds`,
  `max_attempts`, `attempt`, redacted `last_error_class`, `idempotency_key` for
  read-only retries, optional `replay.retry_of`, and `no_auto_retry` for mutating
  workflows
- lifecycle expiry metadata: `expires_at`, `retention_seconds`,
  `retention_expires_at`, `result_expired`, `result_available`, and
  `expired_result_action` (`rerun_allowed`, `rerun_requires_approval`, or
  `unavailable`)
- `audit_events`: redacted lifecycle events for start, completion, failure,
  retry decisions, cancellation, and expiry decisions

Secrets are not stored intentionally. Arguments, errors, and audit details pass
through the MCP audit redactor before persistence. Status artifacts use
repository-relative paths only.

## Retention and expiry defaults

Defaults are environment-configurable:

- `MCP_WORKFLOW_TASK_EXPIRY_HOURS=24`
- `MCP_WORKFLOW_TASK_RETENTION_DAYS=7`
- `MCP_STREAM_REPLAY_MAX_EVENTS=200`
- `MCP_STREAM_REPLAY_RETENTION_SECONDS=86400`
- `MCP_STREAM_REPLAY_RETRY_INTERVAL_MS=1000`

Non-final tasks observed after `expires_at` are marked `expired` on the next
`task_status` read. Final tasks keep their terminal status, but once the result
expiry has passed the status response reports `result_expired=true`,
`result_available=false`, and replaces the inline result with a
`workflow_task.expired_result.v1` envelope containing the safe next action. This
keeps future result handles (#175) aligned with the same `expires_at`,
`retention_seconds`, and `expired_result_action` semantics.

On task start, status records whose `retention_expires_at` has passed may be
pruned from `.codebase-tooling-mcp/tasks/*.json`; the pruning is limited to task
status JSON records and does not delete final result artifact files such as
`.codebase-tooling-mcp/tasks/artifacts/*`.

## Retry

Start a retry with:

```text
workflow_task(workflow="governance_report", retry_of="task-...")
```

The retry receives a task id derived from the workflow arguments plus the source
handle and records `retry_of`, `replay.retry_of`, and redacted `retry` /
`retry_decision` audit events. Failed read-only tasks use `client_may_retry` for
transient failures and `human_required` for permanent failures. Mutating tasks
(`vscode_task_run`) are never auto-retried by this lifecycle layer; their status
sets `no_auto_retry=true`; failed/cancelled/expired responses use
`human_required` / `rerun_requires_approval`.
