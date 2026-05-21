<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Mutation step guard

`mutation_step_guard` is a deterministic, read-only final checkpoint before a planned workspace mutation. It is intended to run immediately before write/delete/git/workspace-transaction operations and never executes the planned mutation.

Inputs summarize the planned mutating tool/mode, normalized argument summary, declared intent, target files, expected diff shape, rollback or snapshot id, selected tests or impact-gate status, recent `interaction_invariant_audit` summary, optional `secret_exposure_report` evidence, and freshness/context metadata.

Outputs include:

- `decision` / `ok_to_mutate`: `allow`, `needs_clarification`, `needs_snapshot`, `needs_fresh_context`, `needs_tests`, `needs_human_approval`, or `deny`.
- `decisive_deviation_risk`: bounded risk score/level plus reasons such as stale context, scope drift, broad targets, high churn, missing tests, or unsafe paths.
- `missing_preconditions`: the concrete evidence that must be supplied before retrying.
- `targeted_reflection_checklist`: concise checks the agent should perform before mutating.
- `safe_next_actions`: deterministic next steps for the selected decision.

The guard is conservative: unsafe repository escapes, secret-like paths, or in-scope high-confidence newly introduced secrets from `secret_exposure_report` are denied; stale context, missing snapshots, missing/stale tests, and unclear intent produce a blocking precondition decision; scoped low-risk edits with fresh context, clear invariant-audit status, expected diff shape, clean secret-exposure evidence, and test evidence can return `allow`.

Example low-risk call shape:

```json
{
  "planned_tool": "workspace_transaction",
  "mode": "write",
  "argument_summary": {"path": "src/app.py"},
  "declared_intent": "Apply the focused fix described in issue #123.",
  "target_files": ["src/app.py"],
  "expected_diff_shape": {"file_count": 1, "line_additions": 6, "line_deletions": 2},
  "selected_tests": ["tests/test_app.py"],
  "invariant_audit_summary": {"ok_to_continue": true, "suspected_smells": []},
  "context_metadata": {"fresh": true, "tests_fresh": true}
}
```

`workflow_diagnostics` recognizes failures reported as `mutation_step_guard`, `mutating_decisive_deviation`, or `ok_to_mutate=false` under the `mutating_decisive_deviation` category so workflow reports can identify skipped final checkpoints or blocked mutation attempts.
