<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# MCP threat-model regression report

`mcp_threat_model_report` is an offline, read-only STRIDE/DREAD-style report for MCP-specific threat modeling. It complements existing checks instead of replacing them:

- `tool_catalog_integrity` detects public MCP metadata drift and advisory poisoned-tool wording.
- `untrusted_content_signals` detects prompt-injection-like text in tool outputs and returns aggregate governance evidence.
- HTTP scopes, `workflow_policy_plan`, and `mutation_step_guard` cover authorization and mutation-intent boundaries.
- `governance_report`, `workflow_diagnostics`, and `artifact_provenance` provide audit and evidence continuity.

The report schema is `mcp_threat_model_report.v1`. It includes:

- MCP components and trust boundaries for host/client, client/LLM, client/server, server/repository, and server/external integrations;
- STRIDE categories mapped to deterministic DREAD-like fields (`damage`, `reproducibility`, `exploitability`, `affected_users`, `discoverability`);
- required controls, covered controls, and uncovered controls per modeled threat;
- optional local poisoned-tool fixture analysis;
- optional baseline comparison that fails only for newly introduced high-severity uncovered fixture findings;
- JSON and Markdown exports under `.codebase-tooling-mcp/reports/` when `export=true`.

## Fixture and baseline usage

Use secret-free local JSON fixtures for poisoned-tool regressions:

```python
mcp_threat_model_report(
    fixture_path="tests/fixtures/mcp_poisoned_tools.json",
    baseline_path="tests/fixtures/mcp_threat_model_baseline.json",
    export=True,
)
```

Fixtures model tool metadata only. They must not contain real credentials, private URLs, production prompts, or raw logs. The checked-in fixture set covers hidden instruction poisoning, cross-tool manipulation/exfiltration wording, ambiguous client parameter visibility, and read-only annotation mismatch.

The baseline uses `mcp_threat_model_baseline.v1` and records known high-severity uncovered findings by stable fixture finding ID:

```json
{
  "schema": "mcp_threat_model_baseline.v1",
  "allowed_high_uncovered_finding_count": 1,
  "allowed_high_uncovered_finding_ids": [
    "fixture:ambiguous_parameter_visibility:ambiguous-parameter-visibility"
  ],
  "required_fixture_ids": ["hidden_instruction_poison"],
  "required_fixture_rule_ids": {
    "hidden_instruction_poison": ["poisoned-tool-metadata"]
  }
}
```

A CI job should prefer `allowed_high_uncovered_finding_ids`: existing known gaps remain visible, while any fixture that introduces a new high-severity uncovered finding fails as a deterministic regression.

## Limitations

This is deterministic advisory threat modeling. It does not prove every MCP client displays the same metadata, parameters, or approval context. Server-side controls can expose schemas and annotations, but client transparency still needs client-specific review.
