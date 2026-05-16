<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Interaction invariant audit

`interaction_invariant_audit` is a read-only guardrail for multi-turn coding-agent workflows. Call it before mutation-capable steps or readiness summaries when the conversation contains constraints that may be lost across turns.

The audit is advisory only. It extracts likely task invariants from a short task summary plus optional recent notes, flags interaction-smell risks, and recommends safer next tools. It does not execute changes, judge correctness autonomously, or persist caller-supplied conversation snippets by default.

## Input contract

- `task_summary` - concise non-sensitive statement of the original task and constraints.
- `recent_notes` - optional short trajectory snippets as strings or `{role, text}` objects. These are redacted in memory and not stored by default.
- `planned_next_step` - optional non-sensitive description of the next action being considered.
- `log_audit` - defaults to `false`. When enabled, only redacted metadata is written to the governance audit log: smell categories, invariant IDs, confidence, and input counts.

Do not pass credentials, raw private conversations, or full logs. Prefer brief summaries such as “user said no mutation; agent plans apply_diff”.

## Output schema

Structured output is registered as `interaction_invariant_audit.v1` in `source/tool_output_schemas.py`.

Stable fields:

- `read_only=true` and `advisory_only=true` - confirms the tool is a diagnostic guardrail.
- `ok_to_continue` - false when the audit found smells or low-confidence constraints that should be clarified first.
- `confidence` - deterministic confidence score for the audit finding, not a proof of correctness.
- `extracted_invariants` - task constraints such as mutation mode, secret safety, validation, rollback, scope, release gates, or client compatibility.
- `suspected_smells` - suspected categories: `intent_drift`, `ignored_historical_instruction`, `missing_validation`, and `contradicted_prior_response`.
- `safe_next_actions` - recommended follow-up workflow or clarification.
- `linked_gates` - maps the finding to existing tools: `clarification_gate`, `state_snapshot`, `change_impact_gate`, `release_readiness`, and `workflow_diagnostics`.

Security fields indicate that caller snippets are redacted, secrets are not recorded, and conversation-log persistence is off by default.

## When to use it

Use `interaction_invariant_audit` when the risk is conversational drift:

- before applying a patch after several planning turns,
- before saying a change is ready when required tests or release blockers were mentioned earlier,
- when a newer instruction seems to conflict with an earlier no-mutation/no-secret/no-scope-change constraint,
- when preparing a safe next-step recommendation for a multi-turn agent workflow.

Use `clarification_gate` when a concrete operation is underspecified and the agent needs targeted non-sensitive answers such as target, risk level, or rollback plan.

Use `workflow_diagnostics` after a workflow has already failed or produced audit events and you need critical-step/failure-category analysis.

Use `change_impact_gate` and `release_readiness` for concrete test-impact and release-gate evidence. The invariant audit can recommend those tools, but it does not replace them.

## Example

```json
{
  "task_summary": "Keep this read-only; no mutation and run pytest before readiness.",
  "recent_notes": [
    "I edited source/server.py and skipped tests because the change looked small."
  ],
  "planned_next_step": "Summarize as ready for release"
}
```

Expected result: `ok_to_continue=false`, extracted `mutation_mode` and `validation` invariants, suspected `ignored_historical_instruction` and `missing_validation`, and safe next actions pointing to `clarification_gate`, `state_snapshot`, `change_impact_gate`, and `release_readiness` as appropriate.
