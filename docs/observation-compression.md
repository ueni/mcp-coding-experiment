<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Adaptive observation compression

`compressed_observation` is an opt-in helper field for verbose MCP workflow outputs. It gives agents a deterministic, redacted summary while preserving access to the raw result through inline return data, a result handle, or an exported artifact path.

The first slice is intentionally conservative: no default behavior changes, no learned rules, and no persistence of compression rules outside the normal result/artifact paths. For stored workflow/task/tool observations, use [`observation_compression_report`](./observation-compression-report.md) to get advisory TACO-style bucket guidance without deleting raw evidence.

## Schema

`compressed_observation.v1` contains:

- `summary`: short deterministic text summary.
- `preserved_signals`: task-critical counts, top paths/tools, and redacted sample rows.
- `omitted`: categories and reason codes for content not copied into the summary.
- `raw_reference`: where to inspect the raw data (`inline_return`, `result_handle`, or `artifact`).
- `rules`: deterministic rule-set name, version, and preservation caps.
- `provenance`: source tool and input scope used for compression.
- `redaction`: whether the MCP audit redactor was applied and whether the compressed layer is expected to contain secrets.

Clients may use the summary for routing, progress updates, and quick triage. They must inspect `raw_reference` before making destructive decisions, final release decisions, security conclusions, or claims that depend on complete output.

## Implemented opt-in paths

- `grep(..., compressed_observation=true)` adds `compressed_observation` to quick, compressed, result-handle, or wrapped full responses. Raw matches remain available inline or through a result handle.
- `governance_report(..., compressed_observation=true)` adds a report-level summary. When `export=true`, `raw_reference` points at the generated JSON artifact; otherwise it points at the inline report.

## Stored-observation report

`observation_compression_report.v1` classifies stored audit, trace, and task observations into `preserve_raw`, `summarize`, `deduplicate`, `drop_low_value`, and `redact_blocked` buckets with stable reason codes. It preserves safety-critical signals such as failing commands, exit codes, changed-file paths, security findings, rollback/snapshot IDs, policy gates, user constraints, and first occurrences of novel errors. Repeated passing logs, package-install noise, and repeated stack traces are represented by hashes/fingerprints and counts rather than raw excerpts. The report emits conservative token-savings estimates, retained critical-signal counts, low-confidence caveats, and deterministic Markdown/JSON output. It is advisory only and never deletes logs or artifacts.

## Safe candidate paths for expansion

These high-volume paths are useful candidates because they already have structured output, compact modes, result handles, or generated artifacts:

1. `grep`: many match rows across many files; implemented first because row compression is deterministic.
2. `governance_report`: generated JSON/Markdown artifacts with redacted audit samples; implemented as the first report path.
3. `workflow_diagnostics`: redacted trajectories and failure evidence can become verbose during failed multi-step workflows.
4. `release_readiness`: check details can include test, docs, impact, risk, license, and optional dashboard payloads.
5. Code graph/search workflows such as `semantic_find`, `dependency_map`, and `call_graph`: large tabular/list outputs already support compact/compressed result forms.

## Redaction and raw-data rules

The compressed layer uses the MCP audit redactor for preserved samples. It should never be treated as the only copy of raw data. If the response is a quick summary that would otherwise omit rows, opting into compression stores the returned rows in the result handle store and records that handle in `raw_reference`.
