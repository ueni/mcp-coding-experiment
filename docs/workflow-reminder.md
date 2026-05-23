<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow reminder packets

`workflow_reminder` is a deterministic, read-only risk-reminder helper for instruction fade-out risk. It emits compact `workflow_reminder.v1` packets before high-risk planned actions, but it does not execute tools, grant permission, or replace authoritative gates.

## Contract

Stable fields:

- `schema="workflow_reminder.v1"`.
- `read_only=true` and `advisory_only=true`.
- `emitted` - true when a reminder should be shown before the intended next action.
- `trigger` - one of the bounded reminder triggers, or `none` when evidence suppresses all candidates.
- `remembered_constraints` - redacted constraints inferred from the task summary and caller-supplied invariants.
- `required_next_gate` - the existing gate to run next, not a new permission system.
- `safe_next_actions` - bounded follow-up guidance.
- `suppress_if_already_satisfied` - explicit existing-gate evidence that suppresses the reminder.
- `evidence` - compact emission and suppression evidence, candidate trigger names, recent-step counts, the reused gate vocabulary, and redaction notes.

The gate vocabulary is intentionally reused from existing controls: `workflow_policy_plan`, `interaction_invariant_audit`, `mutation_step_guard`, `change_impact_gate`, `state_snapshot`, `release_readiness`, `secret_exposure_report`, and `workflow_diagnostics`.

## Inputs

- `task_summary` - concise non-sensitive task and constraints.
- `intended_next_action` - planned next tool/action as a short string or structured tool step.
- `recent_steps` - optional recent tool/action summaries, including failures when available.
- `known_invariants` - optional caller-supplied constraints.
- `last_gate_results` - optional compact results from existing gates.

Do not pass credentials, raw private transcripts, full file contents, or unbounded logs. Inputs are redacted and not persisted by default.

## Triggers

The first slice emits reminders for:

- `missing_rollback_before_mutation` -> run `state_snapshot`, then `mutation_step_guard`.
- `stale_or_missing_tests_before_readiness` -> run `change_impact_gate`, then `release_readiness`.
- `secret_sensitive_action` -> run `secret_exposure_report`.
- `scope_expansion` -> run `workflow_policy_plan` or reconcile constraints with `interaction_invariant_audit` / `clarification_gate`.
- `repeated_failed_mutation_attempts` -> run `workflow_diagnostics`, then retry only through `mutation_step_guard` with fresh evidence.

When prior gate evidence already satisfies a candidate trigger, the packet suppresses that reminder and records bounded suppression evidence. Suppression is advisory; if the authoritative gate is stale or incomplete, rerun the gate.

## Governance note

Reminders reduce instruction fade-out by surfacing remembered constraints at risk boundaries. They are not approvals, do not widen scope, do not bypass mutation/security policy, and do not replace MCP auth, `workflow_policy_plan`, `mutation_step_guard`, `change_impact_gate`, `state_snapshot`, `release_readiness`, or `secret_exposure_report`.
