<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# AGENTS.md context-health lint

`agents_context_lint` is a read-only, offline check for repository-owned always-on coding-agent context, starting with [`AGENTS.md`](../AGENTS.md). It helps keep the entrypoint minimal, task-relevant, and empirically useful while larger workflow detail stays in `task_router`, workflow cards, MCP prompts, or canonical docs.

Run the advisory lint from the repository root:

```bash
python3 scripts/agents_context_lint.py
```

To include the first routing-effectiveness regression fixtures:

```bash
python3 scripts/agents_context_lint.py --include-regression --fail-on-regression
```

The report schema is `agents_context_health_report.v1`. It includes:

- byte and approximate token budgets for the checked context files;
- duplicated guidance and repeated-concept signals;
- stale relative links and stale-looking public tool references;
- broad `MUST` / `ALWAYS` / `NEVER`-style global instructions;
- instruction-class counts separated into safety-critical, workflow-routing, coding-style, and optional-background buckets;
- candidate lines that may be better moved behind `task_router(mode="workflow_select")`, workflow cards, MCP prompts, README/docs, or other canonical references.

Findings are advisory by default. Automatic AGENTS.md rewrites are intentionally out of scope: humans should decide whether a safety guardrail belongs in always-on context or whether optional background/routing detail should move behind a more targeted workflow.

## Governance summary

`governance_report` embeds an `agents_context_health` section in its JSON and Markdown output. The embedded summary is redacted and repository-relative: it records counts, line numbers, relative paths, and identifiers, but not raw AGENTS.md text, raw host paths, prompts, bearer tokens, or file contents.

## Effectiveness regression fixtures

Fixtures live in [`tests/fixtures/agents_context_minimal_routing.json`](../tests/fixtures/agents_context_minimal_routing.json) with schema `agents_context_effectiveness_fixture_set.v1`. The first slice covers three representative tasks:

- security review routes to `security-triage`;
- release readiness routes to `release-readiness`;
- Python test-impact selection routes to `test-impact`.

The evaluator compares `task_router(mode="workflow_select")` on the task prompt alone with the same task prefixed by a compact always-on context summary. Thresholds focus on token budget, top workflow-card accuracy, and routing preservation rather than model-dependent task success.

## Safety boundaries

- No network or model calls are used by the lint itself.
- Context file paths must be repository-relative and stay inside `REPO_PATH` / the supplied repo root.
- Reports do not include raw AGENTS.md contents or absolute host paths.
- Secret-like literals are counted as findings, not copied into output.
- Optional `.codex` / `.continue` entrypoints can be checked explicitly with repeated `--context-file` arguments when a repository wants to expand coverage.
