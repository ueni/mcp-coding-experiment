<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# MCP Output Schemas

This repository publishes a schema-first contract layer for the initial agent-critical MCP tool set:

- `repo_info`
- `roots_diagnostics`
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
- `artifact_provenance`
- `workflow_diagnostics`
- `workflow_lineage`
- `interaction_invariant_audit`
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


### Adaptive observation compression

Verbose tools and reports may expose an opt-in `compressed_observation` field using `compressed_observation.v1`. The field is disabled by default and must never be the only copy of raw data. It includes a deterministic `summary`, `preserved_signals`, `omitted` categories with reason codes, a `raw_reference`, rule metadata, provenance, and redaction metadata. See [Adaptive observation compression](./observation-compression.md) for client guidance and candidate expansion paths.

### Artifact resource links

Artifact-producing tools expose generated outputs through a compact `artifact_resource_link.v1` contract in `resource_links` and mirror the same list under `_meta.artifact_resources` for clients that prefer metadata fields. Links use repository-relative `repo://file/{path}` URIs and never expose host absolute paths. Each link includes a title, URI/path when file-backed, MIME type, size when the file exists, created time, and safety metadata indicating redaction, repository-boundary enforcement, and no secret exposure.

Example client response excerpt for `governance_report(export=true)`:

```json
{
  "structuredContent": {
    "schema": "governance_report.v1",
    "exports": {
      "json": ".codebase-tooling-mcp/reports/governance-report-20260514T194800Z-abcd1234.json",
      "markdown": ".codebase-tooling-mcp/reports/governance-report-20260514T194800Z-abcd1234.md",
      "lineage": ".codebase-tooling-mcp/reports/governance-report-20260514T194800Z-abcd1234.workflow-lineage.json"
    },
    "lineage": {
      "schema": "workflow_lineage.v1",
      "manifest": ".codebase-tooling-mcp/reports/governance-report-20260514T194800Z-abcd1234.workflow-lineage.json",
      "plan_id": "workflow-plan-...",
      "verify": {"tool": "workflow_lineage", "mode": "verify"}
    },
    "resource_links": [
      {
        "schema": "artifact_resource_link.v1",
        "title": "Governance report JSON",
        "uri": "repo://file/.codebase-tooling-mcp%2Freports%2Fgovernance-report-20260514T194800Z-abcd1234.json",
        "path": ".codebase-tooling-mcp/reports/governance-report-20260514T194800Z-abcd1234.json",
        "mime_type": "application/json",
        "size_bytes": 4096,
        "created_at": "2026-05-14T19:48:00+00:00",
        "safety": {
          "redacted": true,
          "contains_secrets": false,
          "repo_boundary_enforced": true,
          "note": "JSON export contains redacted audit summaries only; raw secrets are not persisted."
        }
      }
    ],
    "_meta": {"artifact_resources": {"schema": "artifact_resource_links.v1"}}
  }
}
```

`state_snapshot` uses the same contract for the repository-local snapshot index and, when a stash-backed rollback object exists, adds a `git-ref://refs/mcp-snapshots/...` rollback pointer without embedding snapshot contents. These links are intended to become task artifact references in future async task work, but this contract does not add async task behavior.

`governance_report(export=true)` and `state_snapshot` also write local `mcp_artifact_provenance.v1` sidecars next to their generated artifacts. `governance_report(export=true)` additionally writes a redacted `workflow_lineage.v1` manifest and links it from the report/provenance metadata. The read-only `artifact_provenance` helper verifies artifact presence, sidecar presence, SHA-256 digest match, schema match, freshness, and optional `mcp_artifact_attestation.v1` status without mutating artifacts. Unsigned sidecars report `unsigned` / `local-only`; the only supported attestation backend is deterministic offline `local-dsse-fixture`, which verifies inline DSSE fixture envelopes with no network access. GitHub Artifact Attestations and Sigstore/cosign are future backends and currently report `unsupported` behind the same stable fields (`backend`, `subject_digest`, `signer_identity`, `bundle_ref`/`envelope_ref`, and `verification.status`). The read-only `workflow_lineage(mode="verify")` helper verifies deterministic governance-report plan identity and observed artifact digests without mutating artifacts; its `status` is one of `matched`, `input_changed`, or `artifact_changed`, with `non_deterministic_node` listed in `conditions` when a node is intentionally observed-only.

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
| `roots_diagnostics` | `schema`, `read_only`, `advisory_only`, `server_repo`, `fetch`, `roots`, `relationship`, `guidance` | safety metadata and redacted per-root relationship details |
| `runtime_state` | `schema`, `timestamp`, `transport`, `server`, `sse`, `ollama`, `docker` | process counts and dependency probe details |
| `git_status` | `status`, `short` | `raw` |
| `grep` | match rows: `path`, `line`, `column`, `match` | `lineText`, quick summaries, result handles, opt-in `compressed_observation` |
| `find_paths` | array items as repository-relative paths | none |
| `read_snippet` | `path`, `start_line`, `end_line`, `content` | requested line bounds and `total_lines` |
| `summarize_diff` | `file_count`, `total_added`, `total_deleted`, `risk_flags` | file lists, sorted churn, patches |
| `risk_scoring` | `risk_score`, `risk_level`, `reasons`, `summary` | none |
| `workspace_transaction` | `schema`, `mode`, `result` | mode-specific result internals, `resource_links`, `_meta` |
| `policy_simulator` | `schema`, `ok`, `blocking_policies`, `docs`, `security`, `risk`, `license` | nested policy implementation details |
| `clarification_gate` | `schema`, `ok_to_continue`, `status`, `missing_fields`, `questions`, `fallback_checklist`, `elicitation` | audit notes, normalized input presence, decision reasons |
| `release_readiness` | `schema`, `base_ref`, `head_ref`, `ok`, `checks` | timestamps, check-specific detail fields, and optional `mcp_apps` dashboard when `MCP_APPS_DASHBOARD_ENABLED=true` |
| `governance_report` | `schema`, `report_id`, `generated_at`, `audit`, `governance_hooks`, `exports`, `resource_links` | `window`, `git`, `snapshots`, `security`, `workflow_diagnostics`, `lineage`, `provenance`, opt-in `compressed_observation`, `_meta` |
| `artifact_provenance` | `schema`, `provenance_schema`, `attestation_schema`, `artifact_count`, `ok`, `checks` | per-check `attestation` verification details |
| `workflow_diagnostics` | `schema`, `ok`, `critical_step_candidate`, `failure_category`, `evidence`, `safe_next_actions`, `redactions_applied` | `audit_source`, `read_only`, `security`, `trajectory`, `failure_categories` |
| `workflow_lineage` | `schema`, `read_only`, `manifest_path`, `plan_id`, `status`, `ok`, `checks`, `conditions` | `mode`, `security` |
| `interaction_invariant_audit` | `schema`, `read_only`, `advisory_only`, `ok_to_continue`, `confidence`, `extracted_invariants`, `suspected_smells`, `safe_next_actions`, `linked_gates` | `security`, `redactions_applied`, `input_summary` |
| `test_impact_map` | `schema`, `artifact_path`, `artifact_status`, `changed_files`, `selected_tests`, `unmapped_changed_files`, `confidence` | `test_details`, `impacted_sources`, `coverage_gaps`, `generated_at` |

## IDE/client smoke fixture

[`docs/fixtures/mcp-structured-grep-response.json`](./fixtures/mcp-structured-grep-response.json) demonstrates an IDE-style client consuming a structured `grep` quick response while still displaying the fallback text content.

For uncertainty-aware workflow gating, see [Clarification Gate](./clarification-gate.md). `clarification_gate` returns both structured MCP output and an elicitation adapter/fallback checklist for clients that need missing non-sensitive fields before mutation, release, or security workflows. For multi-turn task-constraint drift before mutation/readiness summaries, see [Interaction invariant audit](./interaction-invariant-audit.md).
