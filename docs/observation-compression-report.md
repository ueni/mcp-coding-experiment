<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Observation compression report

`observation_compression_report` emits an advisory, deterministic TACO-style compression plan for stored workflow, task, trace, and tool observations. It is read-only with respect to evidence: it never deletes logs, task records, traces, result handles, or exported artifacts. Optional `export=true` writes redacted JSON and Markdown reports under `.codebase-tooling-mcp/reports/`.

## Inputs

```text
observation_compression_report(window_hours=168, export=true)
```

Use `start_time` and `end_time` for deterministic windows. Sources can be toggled with `include_audit`, `include_traces`, and `include_tasks`; `max_observations` bounds local classification work.

## Schema

The response schema is `observation_compression_report.v1` with stable top-level fields:

- `summary`: observation count, bucket counts, reason counts, conservative raw-token and savings estimates, retained critical-signal count, and duplicate-fingerprint count.
- `classification_buckets`: stable bucket/reason guidance for `preserve_raw`, `summarize`, `deduplicate`, `drop_low_value`, and `redact_blocked`.
- `observations`: per-observation redacted metadata, bucket, stable reason code, fingerprint, retained critical-signal flags, token estimate, and raw reference.
- `fingerprints`: aggregate duplicate fingerprints and counts without raw sensitive excerpts.
- `compression_opportunities`: advisory savings estimate and `no_evidence_deleted=true` policy.
- `low_confidence_caveats`: estimator and source-coverage caveats.
- `markdown`: deterministic Markdown rendering of the same advisory summary.

## Bucket policy

| Bucket | Stable reason | Use |
| --- | --- | --- |
| `preserve_raw` | `safety_critical_signal` | Keep raw reference prominent for failing commands, exit codes, changed-file paths, security findings, rollback/snapshot IDs, policy gates, user constraints, and first occurrences of novel errors. |
| `summarize` | `unique_noncritical_observation` | Replace unique non-critical verbose output with a deterministic structured summary in model context. |
| `deduplicate` | `duplicate_fingerprint` | Keep first occurrence and refer to repeated boilerplate, passing logs, or stack traces by SHA-256 fingerprint/count. |
| `drop_low_value` | `low_value_boilerplate` | Omit package-install noise and passing-test boilerplate from model context when no critical signal is present. |
| `redact_blocked` | `redaction_sensitive_content` | Do not place raw secret-looking values or host paths in compressed context; keep only redacted metadata and secured raw references. |

## Safety guidance

The report is guidance for model-context construction, not a data-retention policy. Before destructive actions, release decisions, or security conclusions, clients must inspect the raw reference or exported artifact authorized for that workflow. Fingerprints and token savings are conservative routing aids and must not be treated as proof that raw evidence is unimportant.
