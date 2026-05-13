<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Clarification Gate

`clarification_gate` is a read-only uncertainty gate for underspecified high-risk workflows. It separates missing-context detection from execution so clients can ask targeted questions before mutation, release, or security-sensitive follow-up work.

## Tool contract

Inputs:

- `intent` - requested outcome in non-sensitive terms.
- `target` - file, directory, diff range, branch, release candidate, or feature area.
- `operation` - workflow being considered, for example `apply_diff`, `workspace_transaction`, `release_readiness`, or `security_triage`.
- `risk_level` - `low`, `medium`, or `high`.
- `rollback_plan` - required for high-impact mutation or release workflows.
- `user_response_action` - optional MCP elicitation result action: `accept`, `decline`, or `cancel`.

Structured output schema is registered in `source/tool_output_schemas.py` as `clarification_gate.v1` and includes:

- `ok_to_continue` - true only when required non-sensitive context is present and the user has not declined/cancelled.
- `status` - `ready`, `needs_clarification`, `declined`, or `cancelled`.
- `missing_fields` - machine-readable field descriptors with `field`, `reason`, `question`, `required`, and `sensitive=false`.
- `questions` - fallback display questions for clients without elicitation support.
- `fallback_checklist` - checklist text that prompts/agents can render directly.
- `elicitation` - adapter payload for MCP `elicitation/create`, using a flat JSON schema and only non-sensitive fields.
- `audit` - redaction/governance notes. User answers are not persisted.

## MCP elicitation adapter

When a client supports MCP elicitation, use `elicitation.request` as the `elicitation/create` prompt and schema. The request intentionally asks only for flat, non-sensitive strings such as target path, risk level, or rollback plan. It must not ask for passwords, bearer tokens, API keys, credentials, or private keys.

Clients must honor response actions:

- `accept` - rerun `clarification_gate` with the newly supplied non-sensitive fields.
- `decline` - stop the risky workflow and surface `status=declined`.
- `cancel` - stop the risky workflow and surface `status=cancelled`.

Clients without elicitation support should render `fallback_checklist` and continue only after the user supplies the missing non-sensitive fields in a normal prompt/tool call.

## Governance and release-readiness integration

`release_readiness` consumes the gate before returning a release recommendation and reports a `checks.clarification_gate` item. Governance reports aggregate redacted clarification decisions from the audit log under `audit.counts.clarification_gate`, including counts for ready/blocked decisions and missing-field types. Audit records store decision metadata only, not user answers.
