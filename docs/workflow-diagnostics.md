<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow diagnostics

`workflow_diagnostics` is a read-only failure report for MCP workflows. It reads redacted audit events from `MCP_AUDIT_LOG_FILE` and can also accept caller-supplied trajectory snippets. The tool never stores the caller trajectory and applies the same audit redaction rules used by `governance_report`.

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
  "critical_step_candidate": {
    "step_id": "plan-1",
    "tool": "apply_unified_diff",
    "success": false,
    "failure_category": "mutation_disabled",
    "redacted_args": {"path": "src/app.py", "token": "<redacted>"}
  },
  "evidence": [
    {"field": "tool", "value": "apply_unified_diff"},
    {"field": "error", "value": "mutations disabled"}
  ],
  "safe_next_actions": [
    "Keep analysis read-only or restart with ALLOW_MUTATIONS=true only after explicit operator approval.",
    "Prefer planning/diff preview tools before enabling mutation-capable tools."
  ],
  "redactions_applied": ["sensitive_keys_or_values"]
}
```

Recognized failure categories include `auth_policy_denial`, `mutation_disabled`, `path_scope_violation`, `missing_snapshot_rollback`, `failed_readiness_test_gate`, and `malformed_tool_output`. `governance_report` includes a compact `workflow_diagnostics` summary when audit failures are present.
