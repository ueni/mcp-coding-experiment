<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow diagnostics

`workflow_diagnostics` is a read-only failure report for MCP workflows. It reads redacted audit events from `MCP_AUDIT_LOG_FILE` and can also accept caller-supplied trajectory snippets. The tool never stores the caller trajectory and applies the same audit redaction rules used by `governance_report`.

The report complements, rather than replaces, `workflow_lineage`, `interaction_invariant_audit`, and `mutation_step_guard`:

- `workflow_lineage` preserves replay inputs, artifacts, and deterministic provenance for a workflow.
- `interaction_invariant_audit` extracts user/task invariants before risky continuation.
- `mutation_step_guard` decides whether a planned mutation has the required preconditions.
- `workflow_diagnostics` replays the ordered audit/trajectory evidence after a failed or suspicious run and localizes the first high-confidence failed constraint as `critical_failure_step`.

The first slice is deterministic and offline. LLM judging is not used by default; the output includes `llm_judging.enabled=false` so callers can distinguish deterministic replay constraints from any future optional model-assisted review.

## Failure-localization taxonomy

`workflow_diagnostics.v1` keeps the older `critical_step_candidate` and `safe_next_actions` fields for compatibility, and adds AGENTRX-style failure-localization fields:

- `critical_failure_step` - the first explicit failed step or deterministic constraint violation that plausibly caused the run outcome;
- `constraint_violations` - ordered replay constraints that failed, with redacted evidence;
- `failure_category` - the selected category for the critical failure;
- `failure_taxonomy` - category descriptions;
- `confidence` - deterministic confidence score for the localization;
- `recommended_followup` - concrete gate/tool/doc improvement to run next.

The localization taxonomy includes:

- `context` - stale, missing, or contradicted context;
- `clarification` - missing operator/user clarification;
- `policy` - policy, authorization, path-scope, or required-gate blockers;
- `mutation-snapshot` - mutation without prior snapshot/rollback-point evidence;
- `test` - missing or failed validation/readiness evidence;
- `tool-output-security` - prompt-injection or untrusted tool-output findings;
- `rollback` - missing or failed rollback/recovery evidence.

Legacy categories such as `auth_policy_denial`, `mutation_disabled`, `mutating_decisive_deviation`, `path_scope_violation`, `missing_snapshot_rollback`, `failed_readiness_test_gate`, and `malformed_tool_output` may still appear for older audit events.

## Example failed workflow

A workflow attempts to edit a file while mutations are disabled, then tries to continue to release readiness:

```json
[
  {
    "step_id": "plan-1",
    "tool": "apply_unified_diff",
    "success": false,
    "error": "mutations disabled",
    "args": {"path": "src/app.py", "token": "secret-value"}
  },
  {
    "step_id": "gate-1",
    "tool": "release_readiness",
    "success": false,
    "error": "readiness failed: tests failed"
  }
]
```

Diagnostic output shape:

```json
{
  "schema": "workflow_diagnostics.v1",
  "ok": false,
  "failure_category": "mutation_disabled",
  "critical_failure_step": {
    "step_id": "plan-1",
    "tool": "apply_unified_diff",
    "failure_category": "mutation_disabled",
    "constraint_id": "explicit_step_failure",
    "confidence": 0.85,
    "recommended_followup": ["Keep analysis read-only or restart with ALLOW_MUTATIONS=true only after explicit operator approval."]
  },
  "constraint_violations": [],
  "evidence": [
    {"field": "tool", "value": "apply_unified_diff"},
    {"field": "error", "value": "mutations disabled"}
  ],
  "recommended_followup": [
    "Keep analysis read-only or restart with ALLOW_MUTATIONS=true only after explicit operator approval."
  ],
  "redactions_applied": ["sensitive_keys_or_values"],
  "llm_judging": {"enabled": false, "default": "off", "reason": "deterministic replay constraints only"}
}
```

## Deterministic replay constraints

The replay pass walks ordered audit events and trajectory snippets and checks constraints without sending transcripts to a model:

- context freshness evidence is present before context-sensitive continuation;
- required clarification is satisfied before ambiguous or `needs_clarification` steps;
- policy/gate blockers are surfaced as the likely critical step;
- snapshot or rollback-point evidence exists before successful mutation-capable steps;
- validation/readiness evidence is fresh before release/test gates advance;
- tool-output security findings are not treated as trusted instructions;
- rollback/recovery evidence exists after unsafe or failed mutation recovery paths.

This makes diagnostics useful for governance/self-optimization loops: lineage says what was replayed and which artifacts are reproducible; diagnostics says which step and constraint most likely made the run fail.
