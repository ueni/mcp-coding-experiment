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
- `clarification_gate`
- `release_readiness`
- `governance_report`
- `workflow_diagnostics`
- `test_impact_map` (public workflow, currently documented contract rather than schema-backed core contract)

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

### `test_impact_map` workflow output

`test_impact_map` returns `test_impact_map.query.v1` in normal mode and `test_impact_map.query.compact.v1` with `output_profile="compact"`. It reads `.codebase-tooling-mcp/reports/TEST_IMPACT_MAP.json` unless `refresh=true` is supplied. Refresh rebuilds that report and is write-mode/mutation-gated; ordinary queries are read-only.

Freshness is explicit in `artifact_status`:

- `fresh` - schema is `test_impact_map.v1`, `generated_at` is within `max_age_hours`, and `source_fingerprint` matches the current Python workspace.
- `absent` - no report exists yet.
- `invalid` - the report cannot be parsed or has an unexpected schema.
- `stale` - the report is too old or the Python source fingerprint changed.

The selected-test contract is intentionally conservative: `selected_tests` lists test paths, `test_details` adds per-test symbols/reasons/confidence, and `confidence` is the highest selected-test confidence. `coverage_gaps` lists source rows from the artifact with no static test mapping. `unmapped_changed_files` lists changed Python files that were missing from the artifact or had no mapped tests; callers should handle these as manual-review gaps rather than proof that no tests are needed.

`impact_tests` consumes a fresh artifact first and otherwise falls back to dependency/naming heuristics. Its normal output includes `impact_map.artifact_status`, optional `impact_map.fallback_used`, artifact `coverage_gaps`, and `unmapped_changed_files`; compact output keeps `test_count`/`tests` and adds `impact_map_status` plus `unmapped_changed_files`. `change_impact_gate` and `quality_router(mode="change_impact")` expose the same selected tests and unmapped files under their gate result.

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
| `clarification_gate` | `schema`, `ok_to_continue`, `status`, `missing_fields`, `questions`, `fallback_checklist`, `elicitation` | audit notes, normalized input presence, decision reasons |
| `release_readiness` | `schema`, `base_ref`, `head_ref`, `ok`, `checks` | timestamps, check-specific detail fields, and optional `mcp_apps` dashboard when `MCP_APPS_DASHBOARD_ENABLED=true` |
| `governance_report` | `schema`, `report_id`, `generated_at`, `audit`, `governance_hooks`, `exports` | `window`, `git`, `snapshots`, `security`, `workflow_diagnostics` |
| `workflow_diagnostics` | `schema`, `ok`, `critical_step_candidate`, `failure_category`, `evidence`, `safe_next_actions`, `redactions_applied` | `audit_source`, `read_only`, `security`, `trajectory`, `failure_categories` |
| `test_impact_map` | `schema`, `artifact_path`, `artifact_status`, `changed_files`, `selected_tests`, `unmapped_changed_files`, `confidence` | `test_details`, `impacted_sources`, `coverage_gaps`, `generated_at` |

## IDE/client smoke fixture

[`docs/fixtures/mcp-structured-grep-response.json`](./fixtures/mcp-structured-grep-response.json) demonstrates an IDE-style client consuming a structured `grep` quick response while still displaying the fallback text content.

For uncertainty-aware workflow gating, see [Clarification Gate](./clarification-gate.md). `clarification_gate` returns both structured MCP output and an elicitation adapter/fallback checklist for clients that need missing non-sensitive fields before mutation, release, or security workflows.
