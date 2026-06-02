# Agent lifespan drift report

`agent_lifespan_drift_report()` is a read-only, offline fixture suite for longitudinal memory and workflow-card knowledge drift. It replays deterministic synthetic repo revisions, memory/workflow updates, and task probes to show whether agent-facing knowledge still answers current repository tasks correctly after repeated state changes.

The report is additive to existing checks: `memory_governance_report()` still inventories current stores, and `agents_context_health()` still evaluates `AGENTS.md` context independently. `governance_report()` embeds only the compact `agent_lifespan_drift_summary.v1` profile.

## Coverage

Built-in fixtures include:

- a passing current workflow-card revision;
- stale revision drift where an older memory fact remains agent-facing;
- interference drift where a similar legacy workflow task outranks the current fact;
- compression/write drift where summarization drops a path-scoped constraint;
- utilization drift where retrieved current knowledge is misapplied.

Findings are attributed to `write`, `retrieval`, `revision`, or `utilization` stages and to AgingBench-style mechanisms (`compression_aging`, `interference_aging`, `revision_aging`, `maintenance_aging`).

## Redaction and exports

Findings include fixture IDs, hashes of expected/observed facts, severity, stage, mechanism, recommendations, and repository-relative paths. Raw memory values, host absolute paths, secrets, prompts, transcript snippets, and external content are not included. Optional JSON and Markdown exports are written under `.codebase-tooling-mcp/reports/agent-lifespan-drift-report/` with resource links.
