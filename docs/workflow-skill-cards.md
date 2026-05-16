<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow skill cards

`workflow_skill_search` is a read-only helper for choosing the right MCP workflow when a user or agent is unsure which prompt, router, or safety gate to start with. It returns the top ranked workflow cards for a natural-language task plus confidence, matched terms, and caveats.

## Card schema: `workflow_card.v1`

Each card is intentionally concise so agents can retrieve only the relevant recipe instead of loading the full README/tool catalog.

| Field | Meaning |
| --- | --- |
| `id` | Stable workflow card identifier. |
| `schema` | Card schema version, currently `workflow_card.v1`. |
| `title` | Short human-readable workflow name. |
| `intent` | Task intent the workflow is designed for. |
| `entrypoints` | MCP prompt/router/tool sequence to start from. |
| `prerequisites` | Inputs or state to confirm before using the workflow. |
| `risk_level` | `low`, `medium`, or `high` operational risk. |
| `mutation_mode` | Whether the workflow is read-only, write-capable, or requires explicit mutation mode. |
| `outputs` | Expected result artifacts or decision outputs. |
| `do_not_use_when` | Conditions where the card is the wrong workflow. |
| `caveats` | Safety notes, confidence caveats, or follow-up gates. |

The helper response schema is `workflow_skill_search.v1` and includes `matches`, per-match `confidence`, `matched_terms`, global `caveats`, and `high_risk_terms`. It never requires mutation access.

## Seeded cards

The built-in card index covers:

- `release-readiness` - release gate checks via `release_readiness` / `quality_router(mode='release_readiness')`.
- `devcontainer-health` - VS Code/devcontainer MCP endpoint, auth, port, and Ollama diagnostics.
- `snapshot-before-refactor` - rollback-point planning before broad refactors or risky edits.
- `security-triage` - security-sensitive review without secret exposure or policy bypasses.
- `test-impact` - focused test selection and coverage-gap reporting.
- `governance-report` - audit/release evidence export and provenance checks.
- `workflow-diagnostics` - failed-workflow critical-step and recovery diagnostics.

## Retrieval behavior

Ranking is deterministic and offline: card keywords, title/intent overlap, and high-risk wording contribute to a score. High-risk task language such as release/deploy/refactor/security adds caveats that point agents toward clarification, snapshot/rollback, and release-readiness gates before mutation or release decisions.

Example:

```json
{
  "schema": "workflow_skill_search.v1",
  "card_schema": "workflow_card.v1",
  "matches": [
    {
      "id": "snapshot-before-refactor",
      "confidence": 0.62,
      "entrypoints": ["snapshot_before_refactor prompt", "workspace_transaction(mode='snapshot')", "state_snapshot"]
    }
  ],
  "caveats": ["High-risk wording detected; prefer clarification_gate and a snapshot/rollback plan before mutation, and release_readiness before deploy/release decisions."]
}
```
