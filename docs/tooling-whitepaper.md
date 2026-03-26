<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Tooling White Paper: `codebase-tooling-mcp`

## Abstract

`codebase-tooling-mcp` is a repository-centric Model Context Protocol (MCP) server that exposes engineering workflows as auditable, bounded tools. It is designed for high-leverage LLM-assisted software development where correctness, reproducibility, and governance are first-class concerns.

This white paper documents:

- The system goals and non-goals.
- The security and trust model.
- The tool architecture and execution semantics.
- Operational patterns for quality, compliance, and release control.
- A practical maturity model for teams adopting LLM+MCP workflows.

The platform targets one mounted repository (`REPO_PATH`, default `/repo`) and provides a broad tool surface for read, analysis, test orchestration, controlled mutation, and governance automation.

## 1. Problem Statement

Modern software teams face two simultaneous constraints:

1. They need higher throughput in code review, maintenance, and release workflows.
2. They cannot compromise controls around security, licensing, change risk, and traceability.

General-purpose LLM chat without tool mediation creates operational gaps:

- Weak coupling between advice and executable actions.
- Low reproducibility of multi-step workflows.
- Poor observability of what actually changed and why.
- Limited policy enforcement at execution time.

`codebase-tooling-mcp` addresses these gaps by turning frequent engineering actions into typed, inspectable tools that can be orchestrated by an LLM while constrained by repository boundaries and explicit mutation controls.

## 2. Design Principles

### 2.1 Repository-Bounded Execution

All path-based operations resolve under `REPO_PATH`. Path traversal outside the mounted repository is blocked by design. This reduces accidental blast radius and provides a clear operational boundary.

### 2.2 Default-Safe Mutability

Mutation is disabled by default (`ALLOW_MUTATIONS=false`). Write, delete, move, and Git-mutating operations require explicit opt-in. This supports safe read-only introspection and policy simulation in production-like environments.

### 2.3 Tool-First Workflows

The system emphasizes composable tools over monolithic agents. Capabilities are exposed as narrow interfaces that can be chained:

- Discovery/indexing tools provide context.
- Analysis tools produce diagnostics and risk signals.
- Mutation tools apply targeted changes.
- Governance tools enforce compliance and release gates.

### 2.4 Auditable Artifacts

Many tools write durable outputs to `.codebase-tooling-mcp/` (reports, memory, baselines, snapshots, replay logs). These artifacts create an audit trail for why actions were taken and how outcomes were validated.

### 2.5 Operational Pragmatism

The platform supports both strict governance (quality gates, approval points, policy checks) and fast paths (quick summaries, compact output profiles, selective tests), allowing teams to tune rigor/latency tradeoffs per use case.

## 3. System Architecture

### 3.1 Runtime Layers

1. Transport Layer  
Supports `http` and `stdio` MCP transport modes.

2. Tool Runtime  
Typed Python tool functions implementing read/search/analyze/mutate workflows.

3. Control Plane  
Mutation guardrails, path resolution, output budgeting, and result caching.

4. Repository Substrate  
Single Git repository mounted into container; local files are source of truth.

5. Artifact Plane  
`.codebase-tooling-mcp/` stores reports, memory, caches, snapshots, policy traces, and replay data.

### 3.2 Execution Model

Each tool call is:

- Parameterized with explicit inputs.
- Evaluated under repository/path constraints.
- Optionally recorded in result handles or report files.
- Returned as structured JSON for deterministic downstream handling.

This model allows LLMs to move from conversational ambiguity to explicit, machine-checkable state transitions.

## 4. Security and Trust Model

### 4.1 Threats Addressed

- Path escape from repository root.
- Uncontrolled write/mutation in default mode.
- Silent policy regressions during fast iteration.
- Unbounded outputs that inflate cost or hide relevant data.

### 4.2 Security Controls

- Repository path normalization and traversal checks.
- Mutation gating through `ALLOW_MUTATIONS`.
- Separate read/analyze vs. mutate semantics.
- Policy and risk tools (`security_triage`, `change_impact_gate`, `policy_simulator`).
- Output guards (`token_budget_guard`, `output_size_guard`).

### 4.3 Operational Caveat

The new git-backed snapshot restore flow intentionally performs rollback-style cleanup (`reset --hard` + `clean`) before applying captured state. It is correct for rollback semantics but destructive to unsaved local state and should be used accordingly.

## 5. Tooling Taxonomy

The platform now exposes a compact LLM-first MCP v1 surface. Capabilities remain broad, but the public contract is intentionally single-entrypoint so weaker models spend attention on one stable tool and route internally.

### 5.1 Public MCP v1 Surface

Public tools:

- `task_router`

### 5.2 Router Design Principle

`task_router` owns the public contract and dispatches to internal leaf implementations. Its explicit modes expose status, task inference, embeddings, autocomplete, reranking, and coding sandbox/check/package flows without publishing the full internal helper surface.

### 5.3 Internal Leaf Tools

Internal leaf tools remain implemented in the server for reuse and testing, but they are not part of the public MCP v1 surface. This preserves feature breadth while materially reducing the exposed tool count.

## 6. Advanced Workflow Layer

The workflow, governance, and runtime layers remain available as internal leaf functions and higher-level helpers, but they are no longer published as separate MCP routers. This keeps the public contract truthful: one entrypoint, many internal capabilities.

## 7. State Management and Rollback Strategy

### 7.1 Git-Backed Snapshots

Git-backed snapshots use stash commits and refs:

- Preserve tracked/untracked change capture via Git primitives.
- Reuse existing repository object storage.
- Improve restore fidelity for code-centric workflows.

### 7.2 Snapshot Lifecycle (Current)

1. Capture current `HEAD` as baseline.
2. Stash working changes (optionally excluding `.codebase-tooling-mcp`).
3. Persist a stable ref for the stash commit (`refs/mcp-snapshots/<id>`).
4. Drop transient stash entry and reapply state so user workspace remains unchanged.
5. Save metadata to snapshot index in `.codebase-tooling-mcp/snapshots/git_snapshots.json`.

Restore:

1. Load snapshot metadata by `snapshot_id`.
2. Reset repository to baseline `HEAD`.
3. Clean untracked files.
4. Reapply captured stash commit with index state.

## 8. Governance and Compliance Model

### 8.1 License Governance

`license_monitor` and associated hooks provide:

- SPDX/REUSE posture checks.
- Missing header detection.
- Optional remediation and report generation.

### 8.2 Release Governance

`release_readiness`, `required_tool_chain`, and `change_impact_gate` allow policy-gated release decisions based on:

- Testing outcomes.
- Documentation sync.
- Security findings.
- Risk score thresholds.
- Required artifact/report presence.

### 8.3 Policy Simulation

`policy_simulator` allows "dry-run governance" against a diff without immediately applying broader workflow changes, reducing policy surprises late in the cycle.

## 9. Reliability, Observability, and Reproducibility

### 9.1 Result Handles and Persistent Reports

- `result_handle` enables referential linking of prior tool outputs.
- `.codebase-tooling-mcp/reports` stores generated artifacts for later review/comparison.

### 9.2 Replay and Memory

- `execution_replay` supports deterministic replay-like debugging.
- `root_cause_memory` and generic memory tools preserve institutional knowledge from prior incidents.

### 9.3 Output Controls

- `output_size_guard` and `token_budget_guard` provide objective limits.
- Compact output profiles reduce token drift in large multi-step runs.

## 10. Productivity Patterns

### 10.1 Fast Path for Daily Iteration

Use `fast_path_dev` with targeted checks and compact output for short feedback loops.

### 10.2 Safe Automation Path

For high-stakes changes:

1. `workflow_compiler`
2. `state_snapshot`
3. analysis + policy tools
4. gated mutation tools
5. `state_restore` on failure
6. `execution_replay` and reports for post-mortem

### 10.3 Lossless Prompt Transport

Use `encode_lossless`/`decode_lossless` with `roundtrip_verify` for compression pipelines where exact reconstruction is mandatory.

## 11. Performance and Cost Considerations

Primary cost vectors:

- Tool fan-out breadth (number of calls).
- Context size of file reads and summaries.
- Model inference paths (local vs remote).
- Redundant scans absent index reuse.

Recommended controls:

- Reuse the persisted per-file repo index (`repo_index_daemon`) instead of rebuilding full analysis state.
- Use `read_snippet`/focused queries over full-file reads when possible.
- Gate output size early (`token_budget_guard`).
- Use explicit benchmark and guard checks when you need them; do not rely on hidden router layers to optimize automatically.

## 12. Adoption Maturity Model

### Level 1: Assisted Inspection

Read/search/index tools only, no mutations. Goal: safe visibility.

### Level 2: Controlled Mutation

Enable mutations for narrow tasks with explicit review and git discipline.

### Level 3: Policy-Gated Pipelines

Mandate checks (`release_readiness`, `required_tool_chain`, `license_monitor`) before merge.

### Level 4: Autonomous Orchestration

Use workflow tools, replay, route learning, and approval points to automate large segments of engineering operations with governance in-loop.

## 13. Known Limitations

- Single-repo scope by design; multi-repo transactions are out of scope.
- Quality of semantic/ranking tools depends on index freshness and query quality.
- Local model quality depends on environment/runtime availability.
- Governance coverage is only as strong as configured policy thresholds and required artifact definitions.

## 14. Roadmap Recommendations

- First-class multi-repo orchestration with explicit trust boundaries.
- Stronger policy-as-code DSL for richer gate expressions.
- Provenance signing for generated artifacts/reports.
- Time-series telemetry dashboards over `.codebase-tooling-mcp` artifacts.
- Deterministic plan IDs and replay signatures for compliance-grade traceability.

## 15. Conclusion

`codebase-tooling-mcp` positions MCP as an engineering control plane, not just a tool adapter. Its architecture allows teams to scale LLM-assisted development while preserving boundaries, policy enforcement, and operational traceability.

The strategic value is not any single tool; it is the composition of bounded tools, persistent artifacts, and governance-aware workflows into a repeatable software production system.
