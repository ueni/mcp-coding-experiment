<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# AGENTS.md context health report

`agents_context_health()` is a read-only, advisory lint surface for keeping the repository-owned `AGENTS.md` entrypoint compact and effective.

The first slice checks:

- byte and estimated-token budgets for minimal context loading;
- duplicate guidance candidates;
- stale markers such as TODO/deprecated/outdated/version-sensitive lines;
- risky global instruction wording around override, network/upload, secrets, destructive actions, and blanket always/never rules;
- instruction categories such as safety, mutation, routing, documentation, communication, product scope, and generated artifacts;
- candidate guidance that should move from `AGENTS.md` into router/workflow cards or canonical docs.

The report is intentionally bounded. It returns counts, categories, line numbers, stable line hashes, and human-readable finding summaries, but not raw `AGENTS.md` excerpts. It performs no network calls, uploads nothing, writes no artifacts, and enforces that the requested path stays inside the configured repository root.

## Direct tool

```python
agents_context_health(path="AGENTS.md", token_budget=1600, byte_budget=6000)
```

Stable fields:

- `schema`: `agents_context_health.v1`
- `read_only` / `advisory_only`
- `target`: repository-relative path and boundary status
- `budget`: bytes, chars, estimated tokens, configured budgets, and remaining budget
- `summary`: status, finding counts, severity counts, and line/category counts
- `instruction_categories`: compact counts by category
- `duplicate_guidance`, `stale_guidance`, `risky_global_instructions`, `move_candidates`
- `safety`: no-network/no-upload/read-only/content-excerpt controls

Statuses are advisory:

- `clean`: no findings and within budget
- `advisory`: low/info findings only
- `warnings`: medium/high findings present
- `over-budget`: token or byte budget exceeded
- `missing`: requested AGENTS file was not found

## CLI

For local checks without starting MCP:

```bash
python3 scripts/agents_context_health.py --repo . --compact
```

The CLI exits non-zero when the report summary is not OK, making it suitable for focused regression checks without changing repository state.
