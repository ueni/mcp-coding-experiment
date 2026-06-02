<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Review outcome report

`review_outcome_report` / `scripts/review_outcome_report.py` is a deterministic,
offline, read-only first slice for tracking what happened after automated
code-review comments were posted. It complements the
[review signal/noise evaluator](./review-signal-noise-evaluator.md), which scores
whether a review found the right issues in fixtures, and the patch-survivorship
metrics in [self-optimization report](./self-optimization-report.md), which track
whether generated patches survived later review and commits.

Use this report when you have a redacted local metadata export describing review
findings, follow-up commits, and structured human decisions, and you want
aggregate outcome metrics and gate-state correlations without uploading code or
storing raw comments.

```bash
python3 scripts/review_outcome_report.py \
  --fixture-dir tests/fixtures/review_outcomes
```

## Input fixture shape

A fixture pack has `manifest.json` plus one or more `fixture.json` files using
schema `review_outcome_fixture.v1`. Keep fixtures synthetic or redacted. Do not
include raw private comments, full patches, secrets, or host absolute paths.

```json
{
  "schema": "review_outcome_fixture.v1",
  "id": "resolved-feedback",
  "findings": [
    {
      "id": "authz-delete-user",
      "rule_id": "authz-required",
      "path": "source/auth.py",
      "line": 42,
      "comment_digest": "sha256:...",
      "created_at": "2026-05-24T08:00:00Z"
    }
  ],
  "follow_up_commits": [
    {
      "sha": "abc1234",
      "timestamp": "2026-05-24T10:30:00Z",
      "resolved_findings": ["authz-delete-user"]
    }
  ],
  "gate_state": {"tests": "passed", "security": "passed", "release": "ready"}
}
```

## Classification outcomes

The first slice classifies each finding into stable, deterministic buckets:

- `resolved_by_change` - a follow-up commit explicitly resolves/fixes the finding;
- `accepted_no_change` - structured review metadata says the comment was accepted
  without a code change, for example a documented intentional risk;
- `dismissed` - dismissed/noisy/false-positive/not-actionable feedback;
- `stale` - stale or replaced by later file movement, digest changes, or a
  replacement finding;
- `open` - still left open after follow-up evidence, or no terminal evidence;
- `unverifiable` - not enough stable path/rule/digest evidence to link the
  comment to follow-up commits;
- `regressed` - previously addressed feedback reopened or failed again.

The report returns aggregate counts, resolved/dismissed/open rates, evidence
coverage, line-evidence coverage, and median time-to-resolution when timestamps
are available. Per-finding rows expose only stable IDs, rule IDs, repository-
relative paths, line numbers, commit refs, and caller-supplied digests.

Optional `gate_state` fields are aggregated by `tests`, `security`, and
`release` status so maintainers can compare review outcomes with local gate
results without embedding CI logs. The compact `self_optimization_inputs` block
contains only aggregate resolution, noise, evidence, timing, and gate-correlation
metrics for future self-optimization or governance ingestion.

## Privacy and runtime behavior

By default the report only reads local JSON fixture files. It does not call
GitHub, model APIs, package indexes, or other network services, and it does not
shell out. `export=true` writes redacted JSON and Markdown artifacts under
`.codebase-tooling-mcp/reports/`; otherwise no report files are written.

The output is designed to be safe for future aggregation by
`self_optimization_report` or governance reports: it keeps outcome counts,
evidence coverage, timing metrics, and aggregate test/security/release gate
correlations while excluding raw review comments and full patch contents.
