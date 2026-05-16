<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# AGENTS.md - Coding-agent entrypoint

This is the concise, repository-owned entrypoint for coding agents working on
`codebase-tooling-mcp`. Treat it as a map to canonical docs, not a duplicate
manual.

## Product scope

- Product: `codebase-tooling-mcp`, an MCP server for repository engineering on
  one mounted Git repository (`REPO_PATH`, usually `/repo`).
- Service/image/MCP alias: `codebase-tooling-mcp`.
- Main implementation: `source/server.py`; output schema helpers:
  `source/tool_output_schemas.py`; version metadata: `source/version_metadata.py`.
- Canonical overview and setup: [`README.md`](./README.md).
- Documentation index: [`docs/index.md`](./docs/index.md).

## Default MCP entrypoint and important routers

- Start high-level requests with public tool `task_router(mode="task", prompt=...)`.
  It classifies the request and dispatches to the right specialist flow. If unsure
  which existing workflow/prompt/tool fits a task, first call the read-only
  selector `task_router(mode="workflow_select", prompt=...)`; see
  [`docs/workflow-selection.md`](./docs/workflow-selection.md).
- Use `quality_router` for test/quality workflows, including `self_test`,
  `self_check`, `change_impact`, `release_readiness`, flaky-test, required-tool,
  spec-to-test, and smart-fix modes.
- Use `release_readiness` for release gate summaries; see
  [`docs/mcp-apps-release-readiness.md`](./docs/mcp-apps-release-readiness.md).
- Use `workspace_transaction(mode="snapshot")` / `mode="restore"` for the public
  snapshot/restore path. The underlying result schemas are `state_snapshot.v1`
  and `state_restore.v1`.
- Relevant prompt workflows are listed in `README.md` under **MCP prompts in VS
  Code and Copilot**.

## Mutation, auth, and secret guardrails

- Default to read-only operations. Mutations require explicit mutation mode
  (`ALLOW_MUTATIONS=true`) and should go through transactional/snapshot-aware
  flows when possible.
- HTTP MCP access is bearer-token protected. Use environment variables or client
  secret inputs for tokens; never commit token values, authorization headers with
  literal credentials, API keys, private keys, or local secret files.
- Discovery (`/.well-known/mcp-server.json`) is public metadata only. Do not add
  repository contents, absolute host paths, environment values, user data, or
  secrets to it.
- Before risky edits/refactors, create a rollback point or document why a normal
  Git branch/diff is sufficient. After failed mutation workflows, restore from
  the recorded snapshot or leave clear recovery instructions.
- Do not widen host access casually. Review
  [`docs/sandbox-profiles.md`](./docs/sandbox-profiles.md) before autonomous
  mutation access, Docker socket exposure, privileged containers, or broad
  network/secret access.

## Generated artifacts

- `.codebase-tooling-mcp/` is generated runtime/tooling state, including reports
  such as `.codebase-tooling-mcp/reports/TEST_IMPACT_MAP.json`.
- Generated reports may be useful verification evidence but should not be treated
  as source documentation unless a task explicitly says to update or preserve
  them.
- Keep generated artifacts out of commits unless the specific workflow requires a
  checked-in fixture or documented sample.

## Coding workflow

1. Inspect current Git state and avoid overwriting unrelated work.
2. Read the nearest canonical docs before changing behavior:
   - [`README.md`](./README.md) for setup/product scope.
   - [`docs/index.md`](./docs/index.md) for doc ownership/status.
   - [`docs/mcp-output-schemas.md`](./docs/mcp-output-schemas.md) for schema
     contracts.
   - [`docs/release-notes-policy.md`](./docs/release-notes-policy.md) for release
     note expectations.
3. Make minimal, reviewable changes.
4. Run the smallest meaningful gate (`pytest` target, docs/static check, or direct
   inspection) and record the command/result in the PR.
5. If a change affects public tools, schemas, auth, mutation behavior, release
   readiness, or generated artifacts, update docs/tests in the same branch.

## Issue and PR communication

- Link implementation PRs to their source issue with a closing keyword such as
  `Closes #<issue>`.
- Include changed files, verification commands/results, and any rollback or
  generated-artifact notes in the PR body.
- Use GitHub issue/PR discussion for blockers, clarifications, and handoff notes;
  do not rely only on local notes or chat summaries.
