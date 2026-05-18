<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Context retrieval regression suite

The task-router context retrieval smoke suite is a checked-in, offline-safe ContextBench-style benchmark for `task_router(mode="workflow_select")`. It verifies that natural-language tasks still retrieve the expected workflow-card context after router, card, or keyword changes.

## Fixture format

Fixtures live in [`tests/fixtures/context_retrieval_task_routing.json`](../tests/fixtures/context_retrieval_task_routing.json) with schema `context_retrieval_fixture_set.v1`.

Each fixture contains:

- `id`: stable fixture identifier.
- `coverage`: scenario bucket covered by the fixture.
- `task`: task label used by humans to understand the scenario.
- `prompt`: natural-language request passed to `task_router(mode="workflow_select")`.
- `execution_mode`: normally `auto`, unless the fixture intentionally checks online/offline routing.
- `gold_context_anchors`: stable expected context anchors, currently workflow-card anchors shaped as `{ "type": "workflow_card", "id": "..." }`.
- `expected_top_workflow_card`: the workflow card that should rank first.

The initial smoke bank covers review/security triage, release readiness, test impact, devcontainer health, and rollback/snapshot routing.

## Running the evaluator

Run the deterministic smoke suite from the repository root:

```bash
python scripts/context_retrieval_eval.py --fail-on-threshold
```

The command imports the local `source.server` module, calls only the read-only `workflow_select` router path, and emits a `context_retrieval_regression_report.v1` JSON report. It does not call network services or mutate repository files.

## Metrics

For each fixture the report includes:

- `recall`: fraction of gold anchors returned in the selected top-k workflow cards.
- `precision`: fraction of returned workflow-card anchors that are gold anchors.
- `efficiency`: rank-sensitive score for how early gold anchors appear, computed as the mean reciprocal rank of returned gold anchors; missing anchors score `0`.
- `top_workflow_card`: the first selected card, plus whether it matches `expected_top_workflow_card`.

The summary reports mean recall, mean precision, mean efficiency, and top workflow-card accuracy. The checked-in thresholds are smoke thresholds intended to catch obvious regressions, not to replace review.

## Interpreting results

Use failures as routing evidence:

- Low recall means the router stopped retrieving required workflow-card context; inspect prompt wording, `routing_terms`, card intent text, or scoring changes.
- Low precision means too much unrelated card context is being returned; inspect overly broad terms or reduce top-k for the fixture only if the workflow still has enough context.
- Low efficiency means the right card appears too late; inspect rank boosts, ambiguous keywords, or competing cards.
- A top-card mismatch means the agent would likely start with the wrong workflow.

Do not treat the score as the sole release gate. Pair the report with normal tests, code review, and scenario-specific judgment before changing workflow cards or task-routing behavior.
