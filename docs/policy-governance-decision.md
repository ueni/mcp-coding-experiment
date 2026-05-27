<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Policy-as-code governance decision adapter

`policy_governance_decision` is a deterministic, read-only runtime adapter for
reviewed repository-local MCP governance policy bundles. It evaluates declared
MCP tool steps before execution and returns schema-versioned `allow`, `deny`, or
`requires_approval` decisions with matched rule IDs, redacted evidence, and safe
next actions.

The adapter complements existing gates. It does not execute planned tools, mutate
files, grant permissions, weaken HTTP auth/scopes, replace repository path-scope
checks, or replace `workflow_policy_plan` / `mutation_step_guard`.

## Bundle location and schema

Bundles must be JSON files under one of these repository-relative directories:

- `.config/codebase-tooling-mcp/policies/`
- `.codebase-tooling-mcp/policies/`

The default checked-in example is:

```text
.config/codebase-tooling-mcp/policies/mcp-governance.example.json
```

Required bundle fields:

- `schema`: currently `mcp_governance_policy_bundle.v1`
- `bundle_id`: stable maintainer-owned identifier
- `version`: maintainer-managed version string
- `trust.source`: must be `repository`
- `trust.reviewed`: must be `true`
- `rules`: non-empty list of reviewed rules

Each rule requires:

- `id`: stable rule ID
- `effect`: `allow`, `deny`, or `requires_approval`
- optional `priority`: lower runs first for evidence ordering
- optional `when`: matcher over normalized MCP step metadata
- optional `rationale`, `evidence`, and `safe_next_actions`

Supported `when` matchers are intentionally small and data-only:

- `tools`, `modes`, `execution_modes`, `data_classifications`
- `categories_any`, `categories_all`
- `mutates`, `network`
- `target_globs`

String lists support simple shell-style globs. The adapter loads JSON data only;
it does not load Python, Rego, Cedar, JavaScript, plugins, remote imports, or any
other executable policy code.

## Decision output

The tool returns `policy_governance_decision.v1` with:

- `decision`: `allow`, `deny`, or `requires_approval`
- `decision_id`: deterministic digest over bundle digest, plan ID, matched rules,
  and decision
- `policy_bundle`: schema, bundle ID/version, repo-relative path, digest, and rule
  count
- `matched_rule_ids` and compact `rule_results`
- redacted `findings` and `safe_next_actions`
- compact embedded `workflow_policy_plan` hard-gate evidence
- `authoritative_hard_gates`, documenting that existing hard gates remain in
  force

Only normalized step metadata is returned. Raw policy inputs, file contents,
secrets, bearer tokens, and host absolute paths are not returned.

## Fail-closed behavior

The adapter returns `deny` with actionable redacted findings when:

- the bundle path escapes the repository or is outside an allowed policy directory;
- the bundle is missing, unreadable, malformed, or not an object;
- the schema version is unknown;
- required `bundle_id`, `version`, or trust metadata is missing;
- trust metadata does not identify a reviewed repository source;
- rules are missing or malformed;
- no rule matches the declared action sequence.

Existing hard gates can also raise the effective decision. For example, a bundle
rule may match `allow`, but a `workflow_policy_plan` scope violation or disabled
`ALLOW_MUTATIONS` for a mutating step makes the adapter return `deny`.

## Non-goals

This slice deliberately does not provide:

- dynamic policy-code loading;
- network imports, remote policy bundles, or SaaS policy evaluation;
- agent-authored runtime policy mutation;
- permission grants or bypasses for `ALLOW_MUTATIONS`, HTTP bearer auth/scopes,
  repository path scope, `mutation_step_guard`, or per-tool security checks;
- full OPA/Cedar/Agent Governance Toolkit execution.

OPA, Cedar, and Agent Governance Toolkit compatibility is limited to stable
schema, import/export, and report-shape alignment for future reviewed work.

## Governance and release metadata

If a caller stores a `policy_governance_decision` result via `result_handle`,
`governance_report` and `release_readiness` surface only compact metadata:
latest result ID, decision, decision ID, bundle ID/version, matched rule IDs, and
aggregate decision counts. They do not include raw planned inputs or full rule
evidence.
