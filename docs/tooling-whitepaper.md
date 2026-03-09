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

Many tools write durable outputs to `.build/` (reports, memory, baselines, snapshots, replay logs). These artifacts create an audit trail for why actions were taken and how outcomes were validated.

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
`.build/` stores reports, memory, caches, snapshots, policy traces, and replay data.

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

The platform currently exposes a broad catalog (documented in `README.md`). The categories below describe architecture-level roles.

### 5.1 Repository and File I/O

Core primitives for file enumeration, focused reads, batched reads, structured config queries, and controlled writes/moves/deletes.

Representative tools:

- `list_files`, `read_file`, `read_snippet`, `read_batch`, `find_paths`
- `write_file`, `move_path`, `delete_path`, `replace_in_files`, `json_query`

### 5.2 Git and Change Management

Version-control interaction, diff processing, and mutation workflows.

Representative tools:

- `git_status`, `git_diff`, `git_show`, `git_add`, `git_commit`
- `apply_unified_diff`, `edit_transaction`, `summarize_diff`
- `risk_scoring`, `security_triage`

### 5.3 Search, Indexing, and Structure

Fast lexical/semantic discovery and structural program analysis.

Representative tools:

- `grep`, `semantic_find`, `repo_index_daemon`
- `symbol_index`, `dependency_map`, `call_graph`, `ast_search`, `tree_sitter_core`
- `doc_sync_check`, `api_surface_snapshot`, `impact_tests`

### 5.4 Quality, Governance, and Productivity

Orchestration tools that shift teams from ad-hoc prompts to repeatable pipelines.

Representative tools:

- `self_test`, `self_check_pipeline`, `release_readiness`
- `license_monitor`, `install_git_hooks`, `commit_lint_tag`
- `required_tool_chain`, `change_impact_gate`, `smart_fix_batch`
- `tool_router_learned`, `policy_simulator`, `confidence_scoring`
- `runtime_contract_checker`, `cost_budget_enforcer`, `human_approval_points`
- `root_cause_memory`, `execution_replay`

### 5.5 Lossless Prompt Compression and Transport Efficiency

For token economy without semantic drift:

- `encode_lossless`, `decode_lossless`, `roundtrip_verify`
- `delta_encode`, `delta_apply`
- `prompt_optimize`, `token_budget_guard`

### 5.6 Local Model and Retrieval Adapters

Offline/nearby model integration and vector-style reranking support:

- `local_model_status`, `local_infer`, `local_embed`, `local_rerank`

### 5.7 Domain Utilities

Math, SQL, OCR, presentation parsing, and constrained web fetch:

- `math_parser`, `math_solver`, `math_verify`, `sql_expert`
- `vision_ocr_parser`, `image_interpret`, `interpret_presentation`, `browse_web`

## 6. Advanced Workflow Layer (LLM+MCP Power Tools)

Recent additions establish a higher-level orchestration layer:

- `workflow_compiler`: Converts goal statements into executable tool plans with optional rollback policy.
- `state_snapshot` / `state_restore`: Git-backed workspace rollback mechanism using stash commits + stable refs.
- `tool_router_learned`: Lightweight learned routing from historical latency/success outcomes.
- `artifact_memory_index`: Persistent artifact-level indexing for retrieval over generated outputs.
- `constraint_solver_for_tasks`: Constraint feasibility and gap detection for action planning.
- `spec_to_tests`: Derive test skeletons from natural-language spec fragments.
- `auto_sharding_for_analysis`: Partition analysis jobs for scale-out parallelism.
- `runtime_contract_checker`: Validate runtime/tooling contracts against expected interfaces.
- `cost_budget_enforcer`: Apply runtime cost/size budgets to contain token and compute drift.
- `multi_agent_lane`: Explicit lane semantics for concurrent agent roles.
- `human_approval_points`: Codify checkpoints where automation must pause for human sign-off.
- `root_cause_memory`: Durable defect/remediation memory for repeated failure classes.
- `execution_replay`: Reconstruct and replay tool chains for incident/debug analysis.

These tools shift usage from "single command execution" to "policy-aware autonomous workflow management."

## 7. State Management and Rollback Strategy

### 7.1 Git-Backed Snapshots

Git-backed snapshots use stash commits and refs:

- Preserve tracked/untracked change capture via Git primitives.
- Reuse existing repository object storage.
- Improve restore fidelity for code-centric workflows.

### 7.2 Snapshot Lifecycle (Current)

1. Capture current `HEAD` as baseline.
2. Stash working changes (optionally excluding `.build`).
3. Persist a stable ref for the stash commit (`refs/mcp-snapshots/<id>`).
4. Drop transient stash entry and reapply state so user workspace remains unchanged.
5. Save metadata to snapshot index in `.build/snapshots/git_snapshots.json`.

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
- `.build/reports` stores generated artifacts for later review/comparison.

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

- Keep index warm (`repo_index_daemon`).
- Use `read_snippet`/focused queries over full-file reads when possible.
- Gate output size early (`token_budget_guard`).
- Benchmark critical chains (`tool_benchmark`) and route with learned stats (`tool_router_learned`).

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
- Time-series telemetry dashboards over `.build` artifacts.
- Deterministic plan IDs and replay signatures for compliance-grade traceability.

## 15. Conclusion

`codebase-tooling-mcp` positions MCP as an engineering control plane, not just a tool adapter. Its architecture allows teams to scale LLM-assisted development while preserving boundaries, policy enforcement, and operational traceability.

The strategic value is not any single tool; it is the composition of bounded tools, persistent artifacts, and governance-aware workflows into a repeatable software production system.
