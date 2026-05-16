<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Agent execution modes

`codebase-tooling-mcp` supports two agent execution profiles. Both reuse the existing `task_router(mode="workflow_select")` workflow-card selector; there is no second workflow selector.

Use `execution_mode="online"` for cloud-assisted work and `execution_mode="offline"` for onboard-only work. `execution_mode="auto"` keeps the configured default unless the prompt clearly says online/cloud or offline/onboard-only.

## Configuration profiles

| Profile | `MCP_AGENT_EXECUTION_MODE` | `MCP_AGENT_PROFILE` | Use when |
| --- | --- | --- | --- |
| `online-cloud-assisted` | `online` | `online-cloud-assisted` | A cloud model is available for primary reasoning and MCP should reduce context, add auditability, and run deterministic checks. |
| `offline-onboard-only` | `offline` | `offline-onboard-only` | Cloud models are unavailable, disabled, or disallowed and all model-dependent behavior must stay local/onboard. |

Default server behavior is `MCP_AGENT_EXECUTION_MODE=online`. The VS Code/devcontainer path can still run local autocomplete and local checks in that mode; set `MCP_AGENT_EXECUTION_MODE=offline` when privacy/availability requires no cloud model dependency.

## Online/cloud-assisted mode

Model responsibilities:

- The cloud model owns primary reasoning, planning, and high-uncertainty coding decisions.
- Small onboard models are limited to routing, compression, autocomplete, simple classification, prechecks, and token reduction.

MCP/tool responsibilities:

- Provide compact repository context, indexed/search summaries, deterministic checks, policy gates, project/repo memory, and reusable workflow-card knowledge.
- Avoid spending cloud context on raw file dumps when a targeted summary, grep result, diff summary, or workflow card is enough.
- Record traceable local audit data for sensitive tool calls, plans, mutations, test runs, policy gates, memory use, and rationale summaries where practical.
- Keep local/offline autocomplete available even while the main reasoning model is cloud-backed.

Data-flow boundaries:

- Send compact, task-relevant repository context to the cloud model by default.
- Do not send bearer tokens, private keys, local absolute host paths, or secret-bearing artifacts to prompts or audit summaries.
- Use MCP handles/provenance and redacted summaries instead of unbounded transcript capture.

Fallback/escalation:

- If cloud access fails, rerun workflow selection with `execution_mode="offline"` and follow the offline bounded loop.
- If deterministic prechecks fail, fix/clarify locally before spending more cloud context.

## Offline/onboard-only mode

Model responsibilities:

- All model-dependent behavior runs on onboard/local models.
- Small coding/intent models are used only for bounded tasks: narrow decisions, patch suggestions, summarization, autocomplete, and tool/result classification.
- Model calls must use structured JSON contracts, not long free-form agent loops.

MCP/tool responsibilities:

- Move agent behavior into deterministic orchestration:

  ```text
  inspect -> workflow selection -> context retrieval -> patch proposal -> controlled apply -> checks -> summary
  ```

- Use workflow cards, repository indexes, tests, static checks, lint, grep/ripgrep, AST parsers, and policy gates to compensate for weaker local reasoning.
- Keep runtime model pulls disabled by default (`OLLAMA_ALLOW_PULL=false`) unless explicitly opted in.

Small-model JSON contract:

- Required fields: `intent`, `confidence`, `next_action`, `rationale_summary`.
- `confidence` is a number from `0.0` to `1.0`.
- Allowed `next_action` values are `select_workflow`, `retrieve_context`, `propose_patch`, `run_check`, `ask_clarification`, `escalate_online`, and `stop`.
- Free-text rationale is capped and must not include secrets.

Confidence and limits:

- Accept local-small decisions at `confidence >= 0.72`.
- Retry deterministic analysis for intermediate confidence (`0.55 <= confidence < 0.72`).
- Ask for clarification below `0.55`.
- Escalate/mark the task as requiring online or large-model mode when low confidence remains after two model-decision retries, required local context cannot be retrieved, high-uncertainty judgment is needed, or hard iteration limits are reached.
- Hard limits: maximum 6 tool iterations, 2 model-decision retries, 2 patch-apply attempts, and 1 check retry.

## Workflow-card routing

`task_router(mode="workflow_select", execution_mode=...)` returns the normal `workflow_selection.v1` payload plus:

- `execution_mode_schema`: `agent_execution_mode.v1`
- `execution_mode`: `online` or `offline`
- `execution_mode_profile`: the selected profile contract
- per-card `supported_execution_modes`, `mode_routing`, and `selected_mode_routing`

The #63/#66 workflow-selection implementation is the sequencing foundation for this work. If PR #66 has not landed yet, stack mode-aware changes on its branch/head and keep the dependency explicit instead of reimplementing workflow selection.
