<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Opt-in OpenTelemetry tracing

`codebase-tooling-mcp` can emit a first slice of redacted, OpenTelemetry-shaped
span records for MCP tool and workflow execution. Tracing is disabled by default
and does not replace the existing audit log or provenance sidecars.

## Enable local JSON spans

The offline-safe exporter writes JSONL span records inside the repository
boundary:

```bash
export MCP_OTEL_TRACING_ENABLED=true
export MCP_OTEL_EXPORTER=jsonl
export MCP_OTEL_SPANS_FILE=.codebase-tooling-mcp/traces/otel_spans.jsonl
```

If `MCP_OTEL_TRACING_ENABLED` is false, no span file is written. If the exporter
is unset while tracing is enabled, `jsonl` is used. Unsupported exporters are
no-op in this first slice so missing OpenTelemetry SDK/OTLP exporter packages do
not break tool calls. `MCP_OTEL_SPANS_FILE` must resolve under `REPO_PATH`; paths
outside the repository boundary are ignored.

## Current span coverage

This first slice records local spans for:

- `mcp.tool.task_router` public router execution.
- `mcp.workflow.select` for `task_router(mode="workflow_select")`.
- `mcp.tool.workflow_task` and `mcp.tool.task_status` async task entrypoints.
- `mcp.tool.governance_report` and `mcp.tool.artifact_provenance` report/provenance
  entrypoints, including compact repository-relative artifact refs when exported.
- `mcp.workflow_task.lifecycle` for task start/running/completed/failed/expired
  lifecycle events.
- `mcp.policy_gate` for mutation/auth policy denials.
- Security-audited tool wrappers such as `apply_unified_diff`, `command_runner`,
  `docker_router`, and `vscode_router`.

## Attribute conventions

Span attributes intentionally stay low-cardinality and redacted:

| Attribute | Meaning |
|---|---|
| `gen_ai.operation.name=execute_tool` | Aligns public tool spans with OpenTelemetry GenAI tool execution conventions. |
| `gen_ai.system=mcp` | Identifies MCP as the tool system. |
| `gen_ai.tool.name` / `mcp.tool.name` | Public tool name. |
| `mcp.tool.mode` / `mcp.workflow.mode` | Router or workflow mode when applicable. |
| `mcp.execution_mode` | Resolved online/offline execution mode for workflow selection. |
| `mcp.policy.decision` / `mcp.policy.reason` | Bounded policy-gate outcome, for example `deny` + `mutations disabled`. |
| `mcp.workflow.task_id` | Async task correlation id when available. |
| `mcp.artifact.refs` | Repository-relative artifact references, never host absolute paths. |
| `mcp.response.schema`, `mcp.response.ok`, `mcp.response.status/state` | Compact result metadata. |
| `error.type` | Exception class only; raw messages are not recorded. |

Span records also include `trace_id`, `span_id`, `parent_span_id`,
`correlation_id`, start/end timestamps, duration, status, and
`resource.service.name`. Async workflow spans use the task id as the correlation
id; direct tool calls get a generated local correlation id when no task id exists.

## Redaction and content-capture boundaries

Before any span attribute is recorded, values pass through the same audit
redaction helpers used by `MCP_AUDIT_LOG_FILE`, then through path redaction.
Spans must not contain raw prompts, file contents, command output, bearer tokens,
API keys, private keys, credentials, or host absolute paths. Prompt text is not
captured; workflow-selection spans record only prompt length/token count and set
`mcp.content_capture.enabled=false`.

Use the audit log and provenance sidecars for review evidence. Use these spans for
latency, correlation, bounded policy-denial metadata, and local workflow lifecycle
debugging.
