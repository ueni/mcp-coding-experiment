<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Documentation Index

- [Governance report workflow](./governance-report.md) - read-only audit/export reporting plus first-slice `workflow_lineage.v1` manifests for MCP policy and tool-gate decisions.
- [Dependency security report](./dependency-security.md) - read-only dependency inventory, SBOM export, and vulnerability-gate evidence with offline/caller-provided advisory sources.
- [Tool catalog integrity baseline](./tool-catalog-integrity.md) - checked-in public MCP tool-catalog digests, drift diffs, and advisory metadata lint for rug-pull detection.
- [MCP tool contract behavioral fuzzing](./tool-contract-fuzzing.md) - deterministic read-only ToolFuzz-style runtime checks for public tool contracts, error paths, and redaction invariants.
- [Workflow lineage manifests](./workflow-lineage.md) - deterministic redacted plan identity, lineage graph, artifact digests, and read-only drift verification for replayable MCP executions.
- [Workflow diagnostics](./workflow-diagnostics.md) - read-only critical-step diagnostics for failed MCP workflows.
- [Self-optimization efficiency report](./self-optimization-report.md) - offline repo-local MCP usage, token/time savings, throughput, bottleneck, and duplicate recommendation reporting.
- [Interaction invariant audit](./interaction-invariant-audit.md) - read-only invariant-smell guardrail for multi-turn agent workflows.
- [Workflow selection cards](./workflow-selection.md) - read-only workflow-card retrieval for choosing existing MCP workflows/prompts/tools.
- [Context retrieval regression suite](./context-retrieval-regression.md) - offline ContextBench-style fixtures and metrics for task-router workflow-card retrieval.
- [E2E MCP workflow benchmarks](./e2e-mcp-workflow-benchmarks.md) - offline Terminal-Bench-style task fixtures, direct baseline runner, agent hooks, and trajectory/safety metrics for complete MCP workflows.
- [Agent execution modes](./execution-modes.md) - online/cloud-assisted and offline/onboard-only routing contracts layered onto workflow selection.
- [Explicit Agent API Proxy](./agent-api-proxy.md) - opt-in OpenAI-compatible `/v1/chat/completions` proxy with routing, durable privacy evidence packets, redaction/anonymization, streaming, and offline/no-network controls.
- [Async workflow tasks](./workflow-tasks.md) - prototype MCP Tasks-style async handles for long-running repo workflows.
- [Adaptive observation compression](./observation-compression.md) - opt-in deterministic summaries for verbose tool/report outputs.
- [MCP roots diagnostics](./roots-diagnostics.md) - read-only advisory diagnostics for client roots vs `REPO_PATH`.
- [MCP sampling safety adapter](./sampling-safety.md) - disabled-by-default, client-mediated sampling policy for bounded advisory summaries/classifications.
- [Opt-in OpenTelemetry tracing](./opentelemetry-tracing.md) - disabled-by-default, redacted local span records for MCP tool/workflow execution.
- [Hash-pinned dependency locks](./dependency-locks.md) - checked-in pip hash locks, refresh/check tooling, and opt-in Docker locked installs.
- [Build download cache verification](./build-download-cache.md) - stable BuildKit cache IDs, offline/no-network gates, and cache audit tooling for Docker builds.
- [Untrusted content prompt-injection signals](./untrusted-content-signals.md) - deterministic advisory metadata for instruction-like text in tool outputs with aggregate governance/risk counts.
- [Review signal/noise evaluator](./review-signal-noise-evaluator.md) - offline CR-Bench-style fixture scoring for review true positives, misses, and spurious findings.

This index lists documentation ownership and status.

- [Repository Overview](../README.md) (`README.md`) - Status: `canonical`
- [Coding-agent entrypoint](../AGENTS.md) (`AGENTS.md`) - Status: `canonical/agent guidance`
- [JSON Settings Files](./json-settings.md) (`docs/json-settings.md`) - Status: `canonical`
- [MCP Fun Labs](./labs.md) (`docs/labs.md`) - Status: `lab/experimental`
- [Tooling White Paper](./tooling-whitepaper.md) (`docs/tooling-whitepaper.md`) - Status: `canonical/strategy`
- [Troubleshooting](./troubleshooting.md) (`docs/troubleshooting.md`) - Status: `operational runbook`
- [Sandbox Profiles for Autonomous Coding Agents](./sandbox-profiles.md) (`docs/sandbox-profiles.md`) - Status: `operational guidance`
- [Release Notes and Documentation Policy](./release-notes-policy.md) (`docs/release-notes-policy.md`) - Status: `canonical`
- [Docker resource monitoring](./resource-monitoring.md) (`docs/resource-monitoring.md`) - Status: `operational verification`
- [MCP Output Schemas](./mcp-output-schemas.md) (`docs/mcp-output-schemas.md`) - Status: `canonical`
- [Dependency security report](./dependency-security.md) (`docs/dependency-security.md`) - Status: `prototype`
- [Tool catalog integrity baseline](./tool-catalog-integrity.md) (`docs/tool-catalog-integrity.md`) - Status: `prototype`
- [MCP tool contract behavioral fuzzing](./tool-contract-fuzzing.md) (`docs/tool-contract-fuzzing.md`) - Status: `prototype`
- [Workflow lineage manifests](./workflow-lineage.md) (`docs/workflow-lineage.md`) - Status: `prototype`
- [Policy insight regression bank](./policy-insights.md) (`docs/policy-insights.md`) - Status: `canonical`
- [Adaptive observation compression](./observation-compression.md) (`docs/observation-compression.md`) - Status: `prototype`
- [MCP Apps release readiness dashboard](./mcp-apps-release-readiness.md) (`docs/mcp-apps-release-readiness.md`) - Status: `prototype`
- [MCP roots diagnostics](./roots-diagnostics.md) (`docs/roots-diagnostics.md`) - Status: `operational guidance`
- [MCP sampling safety adapter](./sampling-safety.md) (`docs/sampling-safety.md`) - Status: `prototype`
- [Opt-in OpenTelemetry tracing](./opentelemetry-tracing.md) (`docs/opentelemetry-tracing.md`) - Status: `prototype`
- [Hash-pinned dependency locks](./dependency-locks.md) (`docs/dependency-locks.md`) - Status: `operational guidance`
- [Self-optimization efficiency report](./self-optimization-report.md) (`docs/self-optimization-report.md`) - Status: `prototype`
- [Build download cache verification](./build-download-cache.md) (`docs/build-download-cache.md`) - Status: `operational verification`
- [Untrusted content prompt-injection signals](./untrusted-content-signals.md) (`docs/untrusted-content-signals.md`) - Status: `prototype`
- [Review signal/noise evaluator](./review-signal-noise-evaluator.md) (`docs/review-signal-noise-evaluator.md`) - Status: `prototype`
- [Workflow selection cards](./workflow-selection.md) (`docs/workflow-selection.md`) - Status: `operational guidance`
- [Context retrieval regression suite](./context-retrieval-regression.md) (`docs/context-retrieval-regression.md`) - Status: `prototype`
- [E2E MCP workflow benchmarks](./e2e-mcp-workflow-benchmarks.md) (`docs/e2e-mcp-workflow-benchmarks.md`) - Status: `prototype`
- [Agent execution modes](./execution-modes.md) (`docs/execution-modes.md`) - Status: `operational guidance`
- [Explicit Agent API Proxy](./agent-api-proxy.md) (`docs/agent-api-proxy.md`) - Status: `prototype`
- Bootstrap instructions for external repositories live in `README.md` under `Bootstrap Another Repository`.
- Generated reports under `.codebase-tooling-mcp/reports/`, including `TEST_IMPACT_MAP.json` for the static test-impact workflow - Status: `generated`
