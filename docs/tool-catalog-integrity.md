<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Tool catalog integrity baseline

`tool_catalog_integrity` is a read-only rug-pull drift guard for the public MCP
v1 tool catalog. It compares the live FastMCP `mcp.list_tools()` metadata with
the checked-in [`source/tool_catalog_baseline.json`](../source/tool_catalog_baseline.json)
baseline.

The baseline contains only public metadata:

- tool name, title, description, input schema, advertised output schema, MCP
  annotations/meta/icons/execution fields from `mcp.list_tools()`;
- `tool_annotations()` safety categories, required scopes, MCP annotation hints,
  and covered mode categories;
- `tool_output_contracts()` output contracts where present;
- public documentation path/anchor references.

It must never contain repository file contents, bearer tokens, runtime secrets,
host absolute paths, prompts, traces, or user data.

## Digest stability

Each tool entry has a `sha256:<hex>` digest over its canonical metadata object.
The whole-catalog digest is a second `sha256:<hex>` over the sorted list of tool
entries plus the baseline schema/source metadata.

Canonicalization rules are intentionally simple and reviewer-friendly:

- JSON is serialized with sorted object keys, compact separators, and
  `ensure_ascii=true`;
- tools are sorted by name;
- semantically equivalent JSON object key ordering does not change any digest;
- array order remains significant for public schema fields where clients observe
  the order;
- generated timestamps, repository contents, and runtime environment data are not
  included.

## Reviewer workflow

Use the helper before approving public tool-surface changes:

```python
from source import server

report = server.tool_catalog_integrity()
assert report["ok"], report["drift"]
```

A drift report separates:

- `added`: new public tools present in live `mcp.list_tools()` but absent from the
  baseline;
- `removed`: baseline tools no longer present live;
- `changed`: per-tool digest changes with bounded metadata path diffs, including
  description/schema/category/annotation/documentation changes.

When a public metadata change is intentional, refresh the baseline and review the
JSON diff as part of the PR:

```bash
python3 scripts/tool_catalog_integrity.py --write
python3 scripts/tool_catalog_integrity.py --check
```

The first command rewrites `source/tool_catalog_baseline.json`; the second exits
non-zero if the live catalog differs from the checked-in baseline.

## Advisory metadata lint

The helper also runs advisory metadata lint checks. These do not fail the drift
gate by themselves, but reviewers should inspect them before merging. Fixtures
cover:

- hidden-instruction or system/developer prompt override wording;
- cross-tool manipulation language that tells the model to call another tool;
- exfiltration wording around secrets, credentials, repository contents, or
  source code;
- category/annotation mismatches such as `write` tools advertising
  `readOnlyHint=true`, destructive tools missing `destructiveHint=true`, or
  network/shell tools missing `openWorldHint=true`.

`governance_report` includes a compact `tool_catalog_integrity` summary so audit
and release flows can cite the current baseline digest and drift counts without
embedding the full catalog.
