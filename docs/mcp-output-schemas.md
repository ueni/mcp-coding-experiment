<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# MCP Output Schemas

This repository publishes a schema-first contract layer for the initial agent-critical MCP tool set:

- `repo_info`
- `roots_diagnostics`
- `model_assisted_summary`
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
- `tool_catalog_integrity`
- `dependency_security_report`
- `governance_report`
- `self_optimization_report`
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

### Untrusted content prompt-injection signals

Selected text-bearing outputs expose advisory `prompt_injection_signals.v1` metadata, mirrored under `_meta.untrusted_content`, so clients can tell that returned repository, document, web, grep, or diff text must be treated as data rather than instructions. The current implementation covers `browse_web`, `read_document`, `grep`, `read_snippet`, and `summarize_diff` where practical. The metadata is deterministic, bounded, and non-blocking by default; it does not rewrite or hide the raw text users requested.

Signal categories include instruction override, tool manipulation, credential/data exfiltration wording, system-prompt exposure, and suspicious role/markup remnants. Evidence is capped and redacted: secret-looking values, host paths, URLs, and emails are replaced before bounded excerpts and stable hashes are emitted. `risk_scoring` and `governance_report` expose aggregate `untrusted_content_signals` counts only, without repository contents, host paths, bearer tokens, secrets, or raw suspicious excerpts. See [Untrusted content prompt-injection signals](./untrusted-content-signals.md).

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

`dependency_security_report(export=true)` writes a JSON dependency security report and, by default, a CycloneDX-compatible SBOM JSON artifact under `.codebase-tooling-mcp/reports/`; both use `artifact_resource_link.v1` links and local provenance sidecars. The report status is one of `clean`, `vulnerable`, `skipped`, `stale-cache`, `network-disabled`, or `scanner-unavailable`, so clients can distinguish "not checked" from "no vulnerabilities matched".

`tool_catalog_integrity()` compares live public `mcp.list_tools()` metadata with the checked-in `source/tool_catalog_baseline.json` digests and returns compact baseline/current digest summaries, added/removed/changed metadata diffs, and advisory metadata-lint findings. It only covers public MCP metadata, annotations, output contracts, and documentation references; it never embeds repository contents, host absolute paths, tokens, or runtime secrets.

`governance_report(export=true)` and `state_snapshot` also write local `mcp_artifact_provenance.v1` sidecars next to their generated artifacts. `governance_report(export=true)` additionally writes a redacted `workflow_lineage.v1` manifest and links it from the report/provenance metadata. `self_optimization_report(export=true)` writes redacted JSON/Markdown efficiency reports under `.codebase-tooling-mcp/reports/` with the same artifact resource-link shape but does not persist raw traces or prompts. The read-only `artifact_provenance` helper verifies artifact presence, sidecar presence, SHA-256 digest match, schema match, freshness, and optional `mcp_artifact_attestation.v1` status without mutating artifacts. Unsigned sidecars report `unsigned` / `local-only`; `local-dsse-fixture` verifies deterministic inline DSSE fixture envelopes with no network access. The opt-in `github-artifact-attestations` backend uses the same stable attestation fields (`backend`, `subject_digest`, `signer_identity`, `bundle_ref`/`envelope_ref`, and `verification.status`) plus policy data under `signing.verification`: `enabled`, `mode`, `trusted_root_ref`, expected owner/repo, workflow path or name, ref or commit, and predicate type. It is offline by default and verifies caller-provided bundle/trusted-root files for the artifact digest; online verification requires explicit enablement and reports `network_access=true` only when network verification is attempted. Disabled/missing prerequisites produce `unavailable`, mismatched digest/repo/workflow/ref/commit/predicate evidence produces `invalid`, and unknown future backends such as Sigstore/cosign remain `unsupported`. Results redact tokens, unnecessary bundle internals, artifact contents, and host absolute paths. The read-only `workflow_lineage(mode="verify")` helper verifies deterministic governance-report plan identity and observed artifact digests without mutating artifacts; its `status` is one of `matched`, `input_changed`, or `artifact_changed`, with `non_deterministic_node` listed in `conditions` when a node is intentionally observed-only.

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
| `model_assisted_summary` | `schema`, `ok`, `status`, `read_only`, `advisory_only`, `purpose`, `policy`, `capability`, `request`, `audit` | execution mode, bounded context metadata, sampling response digest/summary, and guidance |
| `runtime_state` | `schema`, `timestamp`, `transport`, `server`, `sse`, `ollama`, `docker`, `dependency_locks` | process counts, dependency probe details, and per-lock section digest status |
| `git_status` | `status`, `short` | `raw` |
| `grep` | match rows: `path`, `line`, `column`, `match` | `lineText`, quick summaries, result handles, opt-in `compressed_observation`, `prompt_injection_signals`, `_meta` |
| `find_paths` | array items as repository-relative paths | none |
| `read_snippet` | `path`, `start_line`, `end_line`, `content` | requested line bounds, `total_lines`, `prompt_injection_signals`, `_meta` |
| `summarize_diff` | `file_count`, `total_added`, `total_deleted`, `risk_flags` | file lists, sorted churn, patches, `prompt_injection_signals`, `_meta` |
| `risk_scoring` | `risk_score`, `risk_level`, `reasons`, `summary` | aggregate `untrusted_content_signals` |
| `workspace_transaction` | `schema`, `mode`, `result` | mode-specific result internals, `resource_links`, `_meta` |
| `policy_simulator` | `schema`, `ok`, `blocking_policies`, `docs`, `security`, `risk`, `license` | nested policy implementation details |
| `clarification_gate` | `schema`, `ok_to_continue`, `status`, `missing_fields`, `questions`, `fallback_checklist`, `elicitation` | audit notes, normalized input presence, decision reasons |
| `release_readiness` | `schema`, `base_ref`, `head_ref`, `ok`, `checks` | timestamps, check-specific detail fields, and optional `mcp_apps` dashboard when `MCP_APPS_DASHBOARD_ENABLED=true` |
| `tool_catalog_integrity` | `schema`, `ok`, `status`, `baseline`, `current`, `drift`, `lint`, `security` | `read_only`, per-tool digest lists, bounded metadata diffs, advisory lint finding details |
| `dependency_security_report` | `schema`, `report_id`, `generated_at`, `status`, `ok`, `summary`, `components`, `vulnerabilities`, `advisory`, `gate`, `exports`, `resource_links` | `inputs`, skipped/unresolved details, warnings, local provenance sidecars, and SBOM export metadata |
| `governance_report` | `schema`, `report_id`, `generated_at`, `audit`, `governance_hooks`, `exports`, `resource_links` | `window`, `git`, `snapshots`, `security`, `workflow_diagnostics`, `tool_catalog_integrity`, aggregate `untrusted_content_signals`, `lineage`, `provenance`, opt-in `compressed_observation`, `_meta` |
| `self_optimization_report` | `schema`, `report_id`, `generated_at`, `window`, `summary`, `metrics`, `optimization_candidates`, `security` | `sources`, `bottlenecks`, `usage_guidance`, `resource_links`, `exports`, `confidence`, `caveats`, `github_issue_gate`, `patch_survivorship`, `_meta` |
| `artifact_provenance` | `schema`, `provenance_schema`, `attestation_schema`, `artifact_count`, `ok`, `checks` | per-check `attestation` verification details |
| `workflow_diagnostics` | `schema`, `ok`, `critical_step_candidate`, `failure_category`, `evidence`, `safe_next_actions`, `redactions_applied` | `audit_source`, `read_only`, `security`, `trajectory`, `failure_categories` |
| `workflow_lineage` | `schema`, `read_only`, `manifest_path`, `plan_id`, `status`, `ok`, `checks`, `conditions` | `mode`, `security` |
| `interaction_invariant_audit` | `schema`, `read_only`, `advisory_only`, `ok_to_continue`, `confidence`, `extracted_invariants`, `suspected_smells`, `safe_next_actions`, `linked_gates` | `security`, `redactions_applied`, `input_summary` |
| `test_impact_map` | `schema`, `artifact_path`, `artifact_status`, `changed_files`, `selected_tests`, `unmapped_changed_files`, `confidence` | `test_details`, `impacted_sources`, `coverage_gaps`, `generated_at` |

## IDE/client smoke fixture

[`docs/fixtures/mcp-structured-grep-response.json`](./fixtures/mcp-structured-grep-response.json) demonstrates an IDE-style client consuming a structured `grep` quick response while still displaying the fallback text content.

`self_optimization_report.patch_survivorship` uses compact schema `patch_survivorship_report.v1` for redacted aggregate survivorship data. It reports state counts for proposed/applied/committed/rewritten/reverted/retained patches, aggregations by workflow/tool/execution mode, structured local human-pushback labels only, and correlations to test/security/governance artifacts when those local fields are available. Raw prompts, full private patch text, and private conversation snippets are not included.

For uncertainty-aware workflow gating, see [Clarification Gate](./clarification-gate.md). `clarification_gate` returns both structured MCP output and an elicitation adapter/fallback checklist for clients that need missing non-sensitive fields before mutation, release, or security workflows. For multi-turn task-constraint drift before mutation/readiness summaries, see [Interaction invariant audit](./interaction-invariant-audit.md).
