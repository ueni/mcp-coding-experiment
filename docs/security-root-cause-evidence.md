<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Security root-cause evidence report

`security_root_cause_evidence` is a local, read-only evidence pack for security-sensitive agent fixes. It ranks likely files/functions that deserve root-cause review by combining deterministic static signals from the changed diff with optional `agent_security_delta_report` findings.

The first slice is deliberately conservative:

- offline only: no network calls, uploads, model calls, package installation, or advisory lookups;
- read-only/advisory-only: it does not edit files, run exploit checks, or auto-fix findings;
- redacted output: repository-relative paths and bounded redacted line excerpts only;
- no proof claims: confidence describes shallow evidence density, not exploitability or proof that a vulnerability is fixed.

## Inputs

Typical call:

```python
security_root_cause_evidence(
    base_ref="origin/main",
    head_ref="WORKTREE",
    vulnerability_hint="path traversal in uploaded file handling",
    local_evidence={
        "tests": [{"test_id": "tests/test_upload.py::test_blocks_traversal", "status": "failing-before passing-after"}],
        "sanitizer_traces": [{"path": "src/upload.py", "summary": "trace reaches upload boundary"}],
        "fixtures": [{"fixture_id": "upload-traversal-local-fixture"}],
    },
)
```

Supported inputs:

- `base_ref` / `head_ref`: refs or `WORKTREE`, using the same local diff model as `agent_security_delta_report`.
- `vulnerability_hint`: optional short reviewer hint. Matching terms can raise confidence but never create a proof claim.
- `include_globs` / `exclude_globs`: optional path filters shared with the security-delta scanner.
- `max_locations`: capped number of ranked locations.
- `include_security_delta`: when true, the report generates an offline `agent_security_delta_report(export=False)` and consumes only compact findings.
- `security_delta_report`: optional caller-provided `agent_security_delta_report.v1`; when supplied it is used instead of generating a new one.
- `local_evidence`: optional bounded caller-provided local reproducer/test metadata. Accepted shapes include `items`, `tests`, `failing_tests`, `passing_tests`, `sanitizer_traces`, `error_traces`, `fixtures`, or `reproducers`. Values are never read as files; they are redacted, capped to 20 items, and reported only as compact metadata such as test IDs, repo-relative paths, status, summaries, fixture IDs, and trace excerpts.

## Stable schema

The stable report schema is `security_root_cause_evidence.v1` with these stable fields:

- `schema`, `report_id`, `generated_at`
- `base_ref`, `head_ref`
- `status`: `evidence_found`, `insufficient_evidence`, or `no_changed_files`
- `ok`: false only when changed files exist but no root-cause evidence signals were found
- `summary`
- `ranked_locations`
- `evidence_inputs`
- `security`

Each `ranked_locations[]` entry includes:

- `path`, `kind`, `symbol`, `line_start`, `line_end`
- `rank`, `score`, `confidence`, `confidence_score`
- `reasons[]` with a reason type, weight, line, and redacted evidence
- optional `security_delta_finding_ids[]`

Reason types currently include removed/active security-delta findings, changed security-sensitive paths, input sources, sinks, validators/boundary checks, related tests, caller-provided local test/reproducer/trace evidence, vulnerability-hint matches, and import-neighbor signals.

The report also emits advisory `shallow_fix_warnings[]` and summary counters/status when the diff looks like a shallow security fix:

- `wrapper_symptom_only`: wrapper, adapter, service, handler, or error-path code changed while an imported sink remains reachable.
- `warning_suppression_only`: added security/static-analysis suppression such as `# nosec`, `# noqa`, `type: ignore`, pylint disables, or semgrep ignores.
- `missing_regression_evidence`: changed code lacks related changed tests and removed security-delta findings.

`evidence_inputs.local_evidence` summarizes the accepted/rejected local metadata count, how many entries matched changed files, and the redacted bounded items. Local test evidence can also appear in `related_tests[]` with `source="caller_local_evidence"`, and matched local reproducer/trace metadata can add ranked-location reasons without exposing raw logs or absolute host paths.

`release_readiness` includes a compact `security_root_cause_evidence` check with `pass`, `warn`, or `needs-review` status plus ranked-location, related-test, local-evidence, and shallow-warning counts.

## How it differs from `agent_security_delta_report`

`agent_security_delta_report` is a regression gate: it asks whether a patch introduced or removed known heuristic security findings, and can export SARIF.

`security_root_cause_evidence` is a reviewer support pack: it asks where a reviewer should look for root-cause evidence in a security-sensitive fix. It may consume security-delta signals, but it also ranks validators, sinks/sources, related tests, hint matches, and import neighbors. Its output is advisory and does not block releases or claim exploit proof.

## Limitations

This report is useful for prioritization, not assurance. High confidence means multiple deterministic signals point at the same location. It still needs human review, regression tests, and threat context before maintainers conclude that a fix addresses the root cause.
