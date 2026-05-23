<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Agent quality delta

`agent_quality_delta` is a deterministic, read-only maintainability report for autonomous or agent-authored changes. It compares `base_ref...head_ref` for changed files and answers a narrow question: did this patch add maintainability risk even if tests and merge checks pass?

The report is local/offline and stores no raw prompts. Provenance is limited to git refs, changed-file metrics, a stable patch-survivorship-style patch id, release-readiness vocabulary, and redaction flags.

## What it measures

- changed-file churn with normalized static-analysis deltas per KLOC of churn;
- static-analysis findings by severity and category, using `ruff` when available and deterministic Python fallback heuristics otherwise;
- cyclomatic and cognitive complexity deltas for touched Python functions;
- duplication hints from repeated added lines and large-function growth hints;
- policy status: `pass`, `warn`, `block`, or `review-only`.

`review-only` is used when the change cannot be judged by the Python maintainability heuristics, such as non-Python-only churn. It is intentionally distinct from a pass.

## Release readiness integration

`release_readiness` runs `agent_quality_delta` by default and exposes a concise `checks.agent_quality_delta` summary. Blocking deltas make readiness fail unless a maintainer explicitly passes `agent_quality_delta_maintainer_override=true`; overrides downgrade only the effective readiness decision and keep the raw blocking decision plus `maintainer_override_applied=true` for audit.

This differs from aggregate self-optimization metrics: `self_optimization_report` looks across workflow efficiency and anti-gaming trends, while `agent_quality_delta` gates one concrete patch. It also differs from SARIF export: SARIF is an interchange format for findings, while this report compares base/head deltas and normalizes them by churn.
