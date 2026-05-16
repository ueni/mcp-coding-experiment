<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow selection cards

`task_router(mode="workflow_select", prompt="...")` is a read-only helper for choosing an existing MCP workflow, prompt, or tool from a natural-language task. It does not execute the selected workflow and does not enable mutations.

## Card schema

Current card schema: `workflow_card.v1`.

Each card contains:

- `id`: stable workflow-card identifier.
- `schema`: schema version, currently `workflow_card.v1`.
- `title`: short human-readable name.
- `intent`: when to use the workflow.
- `triggers`: common user phrasings or contexts.
- `prerequisites`: information or state required before use.
- `risk`: `low`, `medium`, or `high`.
- `mutation_mode`: whether the workflow is read-only, writes generated state, or requires explicit mutation enablement.
- `outputs`: expected result artifacts or decision records.
- `do_not_use_when`: negative guidance to avoid wrong routing.
- `recommended_entrypoint`: existing MCP tool, router mode, or prompt to call next.
- `routing_terms`: deterministic keyword/phrase hints used by the selector.

The selector returns `workflow_selection.v1` with ranked `matches`, per-match `confidence`, `match_reasons`, and global `caveats`. High-risk phrasings surface snapshot, clarification, release, or security gates where relevant.

## Seeded cards

The built-in card index covers:

- `release-readiness`
- `devcontainer-health`
- `snapshot-before-refactor`
- `security-triage`
- `test-impact`
- `governance-report`
- `workflow-diagnostics`

## Usage

When an agent is unsure which workflow to use, call:

```json
{
  "mode": "workflow_select",
  "prompt": "We need to ship this branch; what gates should run?",
  "top_k": 3
}
```

Then call the `recommended_entrypoint` from the top match only after checking caveats. For high-risk mutation or release work, clarify scope and create/verify a rollback path before executing write-capable tools.
