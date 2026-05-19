<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# E2E MCP workflow benchmarks

The E2E MCP workflow benchmark pack is an offline-safe, Terminal-Bench-style smoke layer for complete repository workflows. It complements static MCP output contracts, context-retrieval regression, and ToolFuzz checks by exercising task setup, tool trajectory, safety gates, mutation policy, rollback behavior, verification, and compact reporting from start to finish.

The default suite uses disposable temporary repositories created from checked-in fixtures under [`evaluation/e2e_mcp_workflows/tasks`](../evaluation/e2e_mcp_workflows/tasks). It does not mutate the real checkout, call network services, persist raw transcripts, or retain command stdout/stderr.

## Running the direct baseline

From the repository root:

```bash
python3 scripts/e2e_mcp_workflow_benchmarks.py --fail-on-benchmark-failure
```

To write sibling reports for local inspection:

```bash
python3 scripts/e2e_mcp_workflow_benchmarks.py \
  --fail-on-benchmark-failure \
  --report-dir .codebase-tooling-mcp/reports
```

The direct baseline runner is deterministic and executes only declared fixture actions. Reports use schema `mcp_e2e_workflow_benchmark_report.v1` and include JSON plus optional Markdown summaries. Generated reports under `.codebase-tooling-mcp/reports/` remain local/generated evidence and should not be committed unless a task explicitly asks for a sample artifact.

## Starter task coverage

The checked-in starter pack covers six offline-safe workflows:

- read-only API/documentation triage;
- safe one-file edit with a focused test gate;
- snapshot and rollback recovery after a broken config edit;
- release/governance readiness summary using local evidence;
- dependency/security policy interpretation without network access;
- noisy search/context retrieval with a targeted safe edit.

Each task runs in a disposable repository copy assembled from the fixture `setup.files`. Future container-backed runners can mount that disposable directory into an isolated container, but the smoke path does not require Docker.

## Fixture format

Fixture files use schema `mcp_e2e_workflow_benchmark_task.v1`.

Required top-level fields:

- `id`, `title`, `prompt`: stable task identity and natural-language task prompt.
- `setup`: initial disposable repository state.
  - `files`: map of repository-relative paths to file contents.
  - `git_init`: optional boolean for initializing a git repository in the disposable copy.
- `allowed`: task policy.
  - `tools`: declared tool/action names the runner may use.
  - `network`: boolean; the checked-in smoke fixtures keep this `false`.
  - `mutations`: `{ "repo": bool, "artifact": bool, "paths": [...] }` where repo writes are restricted to allowed path globs and artifact writes stay outside the disposable repository.
- `verification`: final checks.
  - `commands`: safe local verification commands. The direct runner currently allows only `python`/`python3` commands and rejects environment-mutating modules such as `pip` and `venv`.
  - `expected_artifacts`: expected files with `scope` of `artifact` or `repo`, path, `must_contain`, and optional `must_not_contain` checks.
- `baseline`: deterministic direct-runner actions.
  - `runner`: currently `direct`.
  - `actions`: ordered declared steps such as `safety_gate`, `read_file`, `search`, `snapshot`, `restore_snapshot`, `write_file`, `write_artifact`, `run_command`, and `retry_marker`.
- `invariants`: safety and trajectory expectations.
  - Safety examples: `required_gate`, `required_tool`, `forbidden_tool`, `forbidden_path_mutation`, `no_repo_mutation`, and `no_network`.
  - Trajectory examples: `tool_order`, `snapshot_before_mutation`, and `test_after_mutation`.

## Reported metrics

The harness reports, per task and for the suite:

- pass/fail status and elapsed time;
- tool-call count by tool;
- approximate input/output byte volume and estimated token volume;
- retries and rework count, including rewritten paths;
- required/satisfied safety-gate coverage;
- snapshot and rollback usage;
- test-gate status;
- trajectory-order findings.

The report also includes a `self_optimization_inputs` block with aggregate metrics suitable for a future `self_optimization_report` ingestion path or a sibling optimization report. This block is intentionally compact and redacted: no raw transcripts, command output, secrets, repository-external paths, host-private data, or host absolute paths are persisted.

## Online/cloud and offline/onboard runner hooks

The default smoke suite should stay deterministic. Agent-driven profiles are opt-in hooks:

```bash
python3 scripts/e2e_mcp_workflow_benchmarks.py \
  --runner offline-onboard-only \
  --fail-on-benchmark-failure \
  --agent-command path/to/sanitized-agent-runner
```

Supported runner profile names are:

- `direct`;
- `online-cloud-assisted`;
- `offline-onboard-only`.

For non-direct profiles the harness creates a disposable repository and artifact directory, then invokes the hook with environment variables:

- `MCP_E2E_FIXTURE_JSON`: path to the fixture JSON copy;
- `MCP_E2E_WORKDIR`: disposable repository path;
- `MCP_E2E_ARTIFACT_DIR`: disposable artifact directory;
- `MCP_E2E_RESULT_JSON`: file the hook must write;
- `MCP_AGENT_EXECUTION_MODE`: selected profile.

The hook result must be a sanitized JSON object compatible with one task result from the direct runner. It may include aggregate tool-call, token/byte, retry, safety, snapshot, test, and trajectory metrics, but must not store raw prompts beyond the fixture prompt, raw transcripts, bearer tokens, command output, absolute host paths, or paths outside the disposable workspace.

## Interpreting results

Use this benchmark pack to guide router, workflow-card, safety-gate, rollback, and reporting improvements. Failures are product-level evidence that an end-to-end path became slower, noisier, less safe, or less deterministic.

Do not treat these scores as the sole release gate. Pair them with normal unit/integration tests, code review, release readiness checks, security review, and task-specific judgment.
