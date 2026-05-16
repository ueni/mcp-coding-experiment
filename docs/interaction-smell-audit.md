<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Interaction smell audit

`interaction_smell_audit` is a read-only invariant audit for multi-turn agent workflows. It inspects caller-supplied trajectory snippets in memory and reports when a later turn appears to violate an extracted constraint from earlier turns.

Use it when a workflow has enough context to continue, but you want a safety pass over remembered invariants before handoff, validation, or mutation. Use `clarification_gate` instead when required user intent, target, risk, or rollback details are missing. Use `workflow_diagnostics` instead after a tool/workflow failure, where the question is which failed step and recovery action matter most.

## Schema

The output contract is `interaction_smell_audit.v1`:

```json
{
  "schema": "interaction_smell_audit.v1",
  "ok": false,
  "smells": [
    {
      "category": "ignored_no_mutation_constraint",
      "confidence": 0.86,
      "evidence": [{"step_id": "trajectory-1", "snippet": "please keep this read-only"}],
      "safe_next_action": "Stop mutation-capable steps; use change_impact_gate and a rollback/snapshot plan before any approved write.",
      "recommended_path": "change_impact_gate"
    }
  ],
  "extracted_constraints": [
    {
      "category": "ignored_no_mutation_constraint",
      "confidence": 0.86,
      "evidence": [{"step_id": "trajectory-1", "snippet": "please keep this read-only"}]
    }
  ],
  "safe_next_actions": ["Stop mutation-capable steps; use change_impact_gate and a rollback/snapshot plan before any approved write."],
  "redactions_applied": ["sensitive_keys_or_values"],
  "recommendations": [{"path": "change_impact_gate", "reason": "ignored_no_mutation_constraint"}],
  "read_only": true,
  "storage": {"trajectory_persisted": false},
  "security": {"redacts_sensitive_snippets": true, "records_secrets": false}
}
```

## Smell categories

- `intent_drift` - a later turn appears to switch goals without routing the new intent through `task_router` or `clarification_gate`.
- `ignored_historical_constraint` - a constraint carried from earlier discussion is not reflected in the later plan; re-check with a `state_snapshot`/constraint summary.
- `ignored_no_mutation_constraint` - a read-only/no-edit/no-mutation invariant conflicts with a write-like action; route through `change_impact_gate` and snapshot/rollback planning before writes.
- `ignored_no_secret_constraint` - a no-secret/redaction invariant conflicts with secret-like output; redact and use `workflow_diagnostics` or security review.
- `missing_validation` - a handoff/release/review step appears to skip required validation; run `release_readiness` or the smallest focused test gate.
- `contradictory_prior_plan` - a later action contradicts an earlier ordered plan; restate the active plan or use `clarification_gate` if the operator must choose.

## Security and persistence

Caller-supplied trajectory snippets are analyzed in memory only. The tool does not persist trajectories, even when `persist_trajectory_requested` is true in the response metadata. Evidence snippets pass through the shared audit redaction helpers and are truncated, so sensitive tokens, credentials, and private-key-like values should not be echoed back.
