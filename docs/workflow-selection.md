<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow selection cards

`task_router(mode="workflow_select", prompt="...", execution_mode="auto")` is a read-only helper for choosing an existing MCP workflow, prompt, or tool from a natural-language task. It does not execute the selected workflow and does not enable mutations. `execution_mode` layers online/cloud-assisted vs offline/onboard-only routing guidance onto the same selector; it does not create a second workflow selector.

## Card schema

Current card schema: `workflow_card.v1`.

Each card contains:

- `id`: stable workflow-card identifier.
- `schema`: schema version, currently `workflow_card.v1`.
- `title`: short human-readable name.
- `intent`: when to use the workflow.
- `triggers`: common user phrasings or contexts.
- `prerequisites`: information or state required before use.
- `risk`: `low`, `medium`, or `high`.
- `mutation_mode`: whether the workflow is read-only, writes generated state, or requires explicit mutation enablement.
- `outputs`: expected result artifacts or decision records.
- `do_not_use_when`: negative guidance to avoid wrong routing.
- `recommended_entrypoint`: existing MCP tool, router mode, or prompt to call next.
- `routing_terms`: deterministic keyword/phrase hints used by the selector.
- `supported_execution_modes`: `online`, `offline`, or both.
- `mode_routing`: per-mode routing guidance; the selected match also includes `selected_mode_routing`.
- `trust`: `workflow_card_trust.v1` provenance and permission metadata. Built-in cards use an explicit repository-owned trusted default with `source="repository_builtin"`, `trust_tier="trusted_repository"`, `review_status="repository_owned"`, bounded permissions, no network access, sandbox guidance, and a deterministic `provenance_digest`.
- `safety`: compact `workflow_card_safety.v1` lint/trust summary with `lint_status`, finding counts, trust tier, risk, and whether a card was suppressed by default.

The selector returns `workflow_selection.v1` with ranked `matches`, per-match `confidence`, `match_reasons`, trust/safety metadata, global `caveats`, and an `agent_execution_mode.v1` profile. High-risk phrasings surface snapshot, clarification, release, security, or mode-fallback gates where relevant. If an injected/test card is untrusted and high risk, the selector suppresses it by default and reports it in `suppressed_matches` instead of ranking it like a repository-owned card.

## Seeded cards

The built-in card index covers:

- `cloud-assisted-agent-mode`
- `offline-bounded-agent-loop`
- `release-readiness`
- `devcontainer-health`
- `snapshot-before-refactor`
- `security-triage`
- `test-impact`
- `governance-report`
- `workflow-diagnostics`

## Trust linting and import posture

`source/server.py` exposes the deterministic helper `lint_workflow_cards(cards=None)`. With no arguments it checks the repository-owned built-in card index using the trusted default metadata. For proposed generated or external cards, call the helper with those cards before any import path is considered.

Structured findings include:

- `missing_trust_metadata` for absent provenance/trust fields.
- `missing_do_not_use_when` for missing negative routing guidance.
- `overbroad_permissions` for wildcard, privileged, host-filesystem, broad network, or secret permissions.
- `dangerous_shell_obfuscation` for shell pipes, eval, base64 decode-to-shell, chmod, or destructive shell phrases.
- `network_exfiltration_pattern` for upload/post/exfiltration-style network instructions.
- `outside_repo_write` for writes, moves, chmods, or deletes outside `REPO_PATH`.
- `missing_sandbox_guidance` when high-risk cards do not explain sandbox/`REPO_PATH` expectations.

External/generated workflow-card or agent-skill loading remains disabled by default. To accept a future card, reviewers must require complete trust metadata, a passing lint report or documented rejection of each finding, a provenance digest tied to the reviewed card content, least-privilege permissions, explicit `do_not_use_when` guardrails, sandbox guidance for high-risk flows, and confirmation that no card asks agents to bypass MCP mutation, secret, auth, network, or `REPO_PATH` boundaries.

## Usage

When an agent is unsure which workflow to use, call:

```json
{
  "mode": "workflow_select",
  "prompt": "We need to ship this branch; what gates should run?",
  "execution_mode": "online",
  "top_k": 3
}
```

Use `execution_mode="offline"` when cloud models are unavailable or disabled. The offline profile constrains local-small-model decisions to structured JSON, confidence thresholds, and hard iteration limits while preserving the same inspect -> workflow selection -> context retrieval -> patch proposal -> controlled apply -> checks -> summary loop documented in [Agent execution modes](./execution-modes.md).

Then call the `recommended_entrypoint` from the top match only after checking caveats. For high-risk mutation or release work, clarify scope and create/verify a rollback path before executing write-capable tools.

## Regression coverage

The checked-in [context retrieval regression suite](./context-retrieval-regression.md) exercises `task_router(mode="workflow_select")` against deterministic fixtures with gold workflow-card anchors. Run it after changing card text, `routing_terms`, or selector scoring to catch recall, precision, efficiency, or top-card regressions before review.
