<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow policy plan preflight

`workflow_policy_plan` is a deterministic, read-only preflight for declared MCP tool sequences. It complements per-tool authorization and mutation gates: it does not execute tools, grant permissions, or replace HTTP/security enforcement.

## Schema

The tool returns `workflow_policy_plan.v1`.

Stable fields:

- `decision`: one of `allow`, `deny`, `needs_approval`, or `needs_clarification`.
- `ok`: true only when `decision == "allow"`.
- `plan_id`: deterministic `workflow-plan-...` digest built from the redacted intent digest, execution mode, allowed targets, data classification, and normalized step metadata.
- `blocking_policies`: sequence-level policies that require denial, such as `scope`, `dataflow`, or `shadow_tool`.
- `required_preconditions`: missing gates such as `snapshot_or_rollback` or `test_or_change_impact_gate`.
- `findings`: scope-creep, dataflow, mutation, network, shadow-tool, missing-gate, and clarification findings.
- `safe_next_actions`: bounded next steps for agents/humans.

The response also includes normalized `steps` with tool name, mode/subcommand, argument shape, risk category, mutation/network flags, expected artifacts, dependency hints, and redacted target summaries.

## Inputs

- `intent`: declared user/workflow intent.
- `execution_mode`: `auto`, `online-cloud-assisted`, `offline-onboard-only`, `read-only`, or `mutation` style profile.
- `allowed_targets`: repository-relative target prefixes that bound the sequence.
- `data_classification`: optional classification such as `public`, `internal`, `sensitive`, or `secret`.
- `planned_steps`: ordered MCP steps. Each step may include `tool`, `mode`/`subcommand`, `args`, `risk_category`, `mutates`, `network`, `expected_artifacts`, and `depends_on`.

## First-slice policies

The current slice checks:

- benign read-only sequences;
- scoped mutation only when an earlier snapshot/rollback gate is declared;
- release-sensitive sequences requiring earlier test/change-impact and snapshot gates;
- repository/config read followed by network-capable steps, especially for sensitive classifications;
- target scope creep outside `allowed_targets`;
- unregistered/shadow tool names not present in the MCP tool security catalog.

Network and mutation findings are sequence-level governance findings. Per-tool authorization still remains with the existing MCP auth, mutation, and security gates.

## Evidence in reports

`governance_report` and `release_readiness` can surface the latest stored `workflow_policy_plan` result from the result-handle store as optional evidence. The preflight itself does not persist artifacts by default.

## Privacy and redaction

Preflight output is redacted by default. It records argument shape rather than raw argument values, redacts host absolute paths, secret-looking values, URLs, and emails, and does not persist raw prompts, repository file contents, or artifacts by default.
