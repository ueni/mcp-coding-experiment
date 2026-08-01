<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Trajectory trust guard

`trajectory_trust_guard` is a deterministic, read-only, advisory report for sensitive final actions that may be over-trusting an untrusted or inconsistent tool trajectory.

It accepts only caller-supplied redacted trajectory summaries, optional response-scanner/policy evidence summaries, redacted evidence references, and proposed final-action metadata. It does not execute tools, call the network, mutate the repository, or persist raw prompts/tool outputs.

Outputs include:

- `decision` / `ok`: `pass`, `warn`, or `block` advisory outcome.
- `risk_score` / `risk_level`: deterministic local score from trajectory features and final-action sensitivity.
- `final_action_sensitivity`: `low`, `medium`, `high`, or `critical`, inferred from action metadata unless supplied explicitly.
- `trajectory_features`: source diversity, single-tool dependency, scanner-warning accumulation, consistency/confidence swings, untrusted high-confidence dependency, provenance gaps, and final-action risk.
- `redacted_evidence_refs`: bounded IDs/digests/report refs only; raw prompt text, raw tool output, secrets, and host absolute paths are redacted.
- `privacy_metadata`: explicit no-raw-prompt/no-raw-tool-output/no-network/no-mutation guarantees.

Example call shape:

```json
{
  "trajectory_summaries": [
    {
      "tool": "external_advisor",
      "trust": "untrusted",
      "confidence": 0.95,
      "dependency_weight": 0.9,
      "supports_final_action": true,
      "scanner_decision": "warn",
      "evidence_ref": {"kind": "scanner", "digest": "sha256:..."}
    }
  ],
  "proposed_final_action": {
    "operation": "write",
    "planned_tool": "workspace_transaction",
    "source_tools": ["external_advisor"]
  }
}
```

The guard is intentionally not a default hard gate. Recommended optional integrations:

- Run before `mutation_step_guard` when a planned mutating step depends heavily on one untrusted or warning-heavy tool trajectory, then pass the compact decision/evidence refs into the mutation guard's context summary.
- Include as advisory evidence next to `release_readiness` for release/deploy decisions that depend on tool-provided test/security/readiness summaries.
- Include the compact result in `governance_report` for configured high-risk workflows so reviewers can see trajectory-level trust formation without exposing raw trajectory content.

A `block` decision means the trajectory is too weak for the proposed final action without independent evidence, fresh scanner results, or human review. It does not close issues, revoke permissions, or override existing hard gates by itself.
