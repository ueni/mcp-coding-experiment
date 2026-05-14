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
  the existing JSON/Markdown artifact resource links when `export=true`.
- `vscode_task_run` - starts one approved VS Code task by label, with bounded
  retry metadata for transient failures.

## Status polling

Call `task_status(task_id="task-...")` to read the latest status from:

```text
.codebase-tooling-mcp/tasks/<task_id>.json
```

Status records use `workflow_task.v1` and include:

- `status`: `pending`, `running`, `succeeded`, `failed`, or `expired`
- timestamps: `created_at`, `started_at`, `updated_at`, `finished_at`,
  `expires_at`, `retention_expires_at`
- `progress`: coarse phase/percent fields for current clients
- `result`: compact final workflow summary when available
- `artifact_references`: generated artifact resource links using
  `artifact_resource_link.v1`
- `resource_links` / `_meta`: resource link for the task status artifact itself
- `audit_events`: redacted lifecycle events for start, completion, failure,
  retry, and expiry

Secrets are not stored intentionally. Arguments, errors, and audit details pass
through the MCP audit redactor before persistence. Status artifacts use
repository-relative paths only.

## Retention and expiry defaults

Defaults are environment-configurable:

- `MCP_WORKFLOW_TASK_EXPIRY_HOURS=24`
- `MCP_WORKFLOW_TASK_RETENTION_DAYS=7`

Non-final tasks observed after `expires_at` are marked `expired` on the next
`task_status` read. Retention is recorded for cleanup/orchestration clients; this
prototype does not delete task artifacts.

## Retry

Start a retry with:

```text
workflow_task(workflow="governance_report", retry_of="task-...")
```

The retry receives a task id derived from the workflow arguments plus the source
handle and records `retry_of` plus a redacted `retry` audit event. For VS Code
workflow runs, `max_retries` records transient failure retries in the same status
artifact.
