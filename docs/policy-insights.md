<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Policy insight regression bank

`source/policy_insights.json` is the maintainer-owned, repository-local regression bank for high-risk MCP policy and tool/router gate decisions.

## Schema

Current schema: `mcp_policy_insights.v1`.

Each insight must include:

- `id`: stable, descriptive identifier.
- `tool_router`: public tool or internal router/gate family being protected.
- `trigger`: deterministic replay fixture with a `kind`, human `summary`, and fixture inputs.
- `expected_decision`: expected replay result such as `allow`, `deny`, or `redact`.
- `rationale`: why the decision matters.
- `source`: policy, code, issue, or audit source for the insight.
- `remediation`: maintainer action if the replay drifts or an audit ambiguity recurs.

The bank is versioned with `version`, has `owner: "maintainers"`, and explicitly keeps `runtime_learning: false`. Agents must not mutate policy from runtime observations or self-authored learning.

## Seeded coverage

The initial bank covers:

- High-risk mutation denial when `workspace_transaction` write mode is requested while mutations are disabled.
- Release-readiness reporting remaining read-only when mutation mode is disabled.
- Secret/sensitive audit values being redacted before reporting.

## Replay and reporting

Tests replay the checked-in fixtures through the same local policy primitives used by the server (`_require_tool_security_gate` and audit redaction helpers). Drift fails the regression test.

The public `policy_insights()` tool is read-only and returns only a summary: schema/version, stable IDs, tool/router names, trigger summaries, expected decisions, rationale, source, and remediation. It does not expose raw trigger arguments or sensitive fixture values.

## Promoting audit failures into insights

When an audit failure, verifier finding, or ambiguous gate decision is policy-relevant:

1. Add a reviewed fixture to `source/policy_insights.json` with all required fields.
2. Use sanitized fixture inputs only; do not copy real tokens, private paths, or raw customer/user data.
3. Set the narrowest deterministic `expected_decision`.
4. Document the source and remediation so future maintainers know whether to fix code, docs, annotations, or the fixture.
5. Run the policy insight replay tests before merge.
