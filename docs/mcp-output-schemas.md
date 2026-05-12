<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# MCP Output Schemas

This repository publishes a schema-first contract layer for the initial agent-critical MCP tool set:

- `repo_info`
- `runtime_state`
- `git_status`
- `grep`
- `find_paths`
- `read_snippet`
- `summarize_diff`
- `risk_scoring`
- `workspace_transaction`
- `policy_simulator`
- `release_readiness`
- `governance_report`

The checked-in contracts live in [`source/tool_output_schemas.py`](../source/tool_output_schemas.py). The public helper tool `tool_output_contracts` returns either all contracts or one contract by `tool_name`.

## Structured content contract

Schema-backed responses keep backwards-compatible JSON/text available while exposing typed data for clients that read `structuredContent`.

A client-side result envelope has this shape:

```json
{
  "content": [{"type": "text", "text": "...json summary..."}],
  "structuredContent": {"or": ["typed", "tool", "payload"]},
  "outputSchema": {"type": "object"}
}
```

For direct Python tool calls, existing return types are preserved where clients already depend on them. For example, `grep` and `find_paths` still return lists; `git_status` now returns structured fields and preserves the legacy text under `raw`.

## Error shape

All schema-backed tools share this documented error envelope for clients that normalize exceptions into structured results:

```json
{
  "ok": false,
  "error": {
    "tool": "grep",
    "type": "ValueError",
    "message": "invalid regex pattern",
    "retryable": false
  }
}
```

## Stable vs experimental fields

Stable fields are the fields clients may rely on for routing, validation, and UI rendering. Experimental fields may change shape or disappear in later schema versions.

| Tool | Stable fields | Experimental fields |
|---|---|---|
| `repo_info` | `repo_path`, `repo_exists`, `is_git_repo`, `allow_mutations`, `transport` | `docker`, `current_branch`, `head`, `dirty`, limits |
| `runtime_state` | `schema`, `timestamp`, `transport`, `server`, `sse`, `ollama`, `docker` | process counts and dependency probe details |
| `git_status` | `status`, `short` | `raw` |
| `grep` | match rows: `path`, `line`, `column`, `match` | `lineText`, quick summaries, result handles |
| `find_paths` | array items as repository-relative paths | none |
| `read_snippet` | `path`, `start_line`, `end_line`, `content` | requested line bounds and `total_lines` |
| `summarize_diff` | `file_count`, `total_added`, `total_deleted`, `risk_flags` | file lists, sorted churn, patches |
| `risk_scoring` | `risk_score`, `risk_level`, `reasons`, `summary` | none |
| `workspace_transaction` | `schema`, `mode`, `result` | mode-specific result internals |
| `policy_simulator` | `schema`, `ok`, `blocking_policies`, `docs`, `security`, `risk`, `license` | nested policy implementation details |
| `release_readiness` | `schema`, `base_ref`, `head_ref`, `ok`, `checks` | timestamps and check-specific detail fields |
| `governance_report` | `schema`, `report_id`, `generated_at`, `audit`, `governance_hooks`, `exports` | `window`, `git`, `snapshots`, `security` |

## IDE/client smoke fixture

[`docs/fixtures/mcp-structured-grep-response.json`](./fixtures/mcp-structured-grep-response.json) demonstrates an IDE-style client consuming a structured `grep` quick response while still displaying the fallback text content.
