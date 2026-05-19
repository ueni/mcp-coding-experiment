<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# MCP tool contract behavioral fuzzing

`scripts/tool_contract_fuzzer.py` runs deterministic, ToolFuzz-style behavioral
fuzzing against public MCP tool contracts. It executes a bounded corpus of direct
public tool calls, records reproducible findings, and validates structured
outputs against checked-in contracts where available.

## How this differs from `tool_catalog_integrity`

- `tool_catalog_integrity` is a **static catalog drift guard**. It compares public
  tool metadata, annotations, documentation references, and advertised schemas
  against `source/tool_catalog_baseline.json`.
- `tool_contract_fuzzer` is a **dynamic behavior guard**. It actually calls tools
  with deterministic arguments, checks returned structured data and documented
  error envelopes, and verifies redaction invariants for runtime outputs.

Use both checks when changing public tools: the static baseline catches metadata
or schema drift; the behavioral fuzzer catches runtime contract/security drift.

## Safe default corpus

The default corpus is offline-safe and read-only. It currently covers at least
five public surfaces, including schema-backed tools and an error-path-heavy case:

- `repo_info`
- `git_status`
- `find_paths`
- `grep`
- `grep` malformed-regex error path
- `read_snippet`
- `task_router(mode="workflow_select")`
- `quality_router(mode="required_tool_chain")`

The seed controls benign values and execution order. The CLI defaults `--repo-path`
to the current directory:

```bash
python3 scripts/tool_contract_fuzzer.py --seed 106 --pretty
```

To save a report:

```bash
python3 scripts/tool_contract_fuzzer.py \
  --seed 106 \
  --output .codebase-tooling-mcp/reports/tool-contract-fuzz-106.json \
  --pretty
```

## Findings and replay

Each finding includes:

- the tool, case id, seed, and minimized arguments/prompt;
- expected vs actual behavior;
- contract/security category;
- `minimized_replay` data sufficient to rerun the same case.

The runner avoids embedding large output payloads in findings and redacts
secret-looking canaries from diagnostic previews.

## Contract and redaction checks

For schema-backed tools, successful outputs validate against
`source/tool_output_schemas.py`. Expected exception paths are normalized through
the documented shared error envelope before validation.

Every case also checks redaction invariants. By default, outputs must not expose
host absolute repository paths or secret-looking canaries. Tools that explicitly
contract to return the repository path, such as `repo_info`, mark that exception
per case.

## Mutation gating

Write-mode fuzz cases must never run accidentally. The default corpus contains no
mutation cases. Any future mutation corpus must require all of the following:

1. explicit `--include-mutations`;
2. an explicit `--mutation-snapshot-label`;
3. server-side mutation mode enabled;
4. a documented snapshot/restore plan for any write target.

If the snapshot label is omitted, the runner fails before executing mutation
cases.
