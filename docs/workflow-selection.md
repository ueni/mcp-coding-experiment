<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow selection cards

`task_router(mode="workflow_select", prompt="...", execution_mode="auto")` is a read-only helper for choosing an existing MCP workflow, prompt, or tool from a natural-language task. It does not execute the selected workflow and does not enable mutations. `execution_mode` layers online/cloud-assisted vs offline/onboard-only routing guidance onto the same selector; it does not create a second workflow selector.

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
- `supported_execution_modes`: `online`, `offline`, or both.
- `mode_routing`: per-mode routing guidance; the selected match also includes `selected_mode_routing`.

The selector returns `workflow_selection.v1` with ranked `matches`, per-match `confidence`, `match_reasons`, global `caveats`, and an `agent_execution_mode.v1` profile. High-risk phrasings surface snapshot, clarification, release, security, or mode-fallback gates where relevant.

## Seeded cards

The built-in card index covers:

- `cloud-assisted-agent-mode`
- `offline-bounded-agent-loop`
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
  "execution_mode": "online",
  "top_k": 3
}
```

Use `execution_mode="offline"` when cloud models are unavailable or disabled. The offline profile constrains local-small-model decisions to structured JSON, confidence thresholds, and hard iteration limits while preserving the same inspect -> workflow selection -> context retrieval -> patch proposal -> controlled apply -> checks -> summary loop documented in [Agent execution modes](./execution-modes.md).

Then call the `recommended_entrypoint` from the top match only after checking caveats. For high-risk mutation or release work, clarify scope and create/verify a rollback path before executing write-capable tools.
