<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Tool catalog integrity baseline

`tool_catalog_integrity` is a read-only rug-pull drift guard for the public MCP
surface. It preserves the original public tool-catalog contract while extending
the checked-in [`source/tool_catalog_baseline.json`](../source/tool_catalog_baseline.json)
baseline to prompts, resources, and allowlisted public discovery metadata.

The baseline contains only public metadata:

- tool name, title, description, input schema, advertised output schema, MCP
  annotations/meta/icons/execution fields from `mcp.list_tools()`;
- `tool_annotations()` safety categories, required scopes, MCP annotation hints,
  and covered mode categories;
- `tool_output_contracts()` output contracts where present;
- prompt names/titles/descriptions/arguments from `mcp.list_prompts()` plus a
  deterministic digest of `mcp.get_prompt()` template text with synthetic prompt
  arguments redacted to `<argument:name>` placeholders;
- resource and resource-template names, titles/descriptions, URI templates/URIs,
  MIME/type hints, and read/list capability flags from `mcp.list_resources()` and
  `mcp.list_resource_templates()` without reading resource payloads;
- public `/.well-known/mcp-server.json` prompt/resource capability entries and
  README/docs mention checks;
- public documentation path/anchor references.

Public prompts currently covered:

- `review_changed_files`
- `release_readiness_check`
- `security_triage`
- `devcontainer_health_check`
- `snapshot_before_refactor`

Public resources currently covered:

- `repo://summary`
- `repo://file/{path}`
- `repo://tree/{path}`
- `ui://codebase-tooling-mcp/release-readiness-dashboard`

The baseline must never contain repository file contents, bearer tokens, runtime
secrets, host absolute paths, raw caller-supplied prompt argument values, traces,
or user data.

## Digest stability

Each tool, prompt, resource, and public-discovery entry has a `sha256:<hex>`
digest over its canonical metadata object. The whole-catalog digest is a second
`sha256:<hex>` over the sorted public surface plus the baseline schema/source
metadata.

Canonicalization rules are intentionally simple and reviewer-friendly:

- JSON is serialized with sorted object keys, compact separators, and
  `ensure_ascii=true`;
- tools are sorted by name, prompts by name, resources by URI/template identity,
  and public-discovery entries by stable identity;
- semantically equivalent JSON object key ordering does not change any digest;
- array order remains significant for public schema fields where clients observe
  the order;
- generated timestamps, repository contents, resource read contents, runtime
  environment data, and raw dynamic prompt arguments are not included.

## Reviewer workflow

Use the helper before approving public MCP-surface changes:

```python
from source import server

report = server.tool_catalog_integrity()
assert report["ok"], report["drift"]
```

A drift report separates:

- `drift.tools`: new, removed, or changed public tools from `mcp.list_tools()`;
- `drift.prompts`: new, removed, or changed public prompts or prompt template
  digests;
- `drift.resources`: new, removed, or changed public resources/templates;
- `drift.public_discovery`: changes to allowlisted public discovery/docs entries;
- top-level `added`, `removed`, `changed`, and `summary` fields for backward
  compatible compact consumers.

When a public metadata change is intentional, refresh the baseline and review the
JSON diff as part of the PR:

```bash
python3 scripts/tool_catalog_integrity.py --write
python3 scripts/tool_catalog_integrity.py --check
```

The first command rewrites `source/tool_catalog_baseline.json`; the second exits
non-zero if the live public MCP surface differs from the checked-in baseline.

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
  network/shell tools missing `openWorldHint=true`;
- missing prompt/resource descriptions;
- public prompt templates missing explicit `Safety guardrails:` text;
- host-path-like resource URI templates or path-parameter templates that are not
  clearly `repo://` scoped;
- discovery/docs mismatches where README/docs or the provisional public manifest
  advertise stale prompt/resource names.

## Draft MCP tool metadata lint

`tool_catalog_integrity` also includes a read-only `draft_tool_metadata_lint`
section for the MCP draft server/tools metadata described at
<https://modelcontextprotocol.io/specification/draft/server/tools>. The draft
currently calls out deterministic `tools/list` ordering for prompt-cache
efficiency, optional `ttlMs`/`cacheScope` cache hints, and `x-mcp-header` JSON
Schema annotations for mirroring primitive tool parameters into Streamable HTTP
headers.

The report is advisory while the referenced MCP text remains draft. Its finding
severities (`info`, `warn`, `block`) are compatibility severities, not a release
gate; revisit the severity mapping if the draft becomes final or changes the
server/client obligations.

The draft lint reports:

- deterministic local catalog ordering across repeated generation;
- optional cache-hint presence/shape when this server elects to expose
  `ttlMs`/`cacheScope`;
- `x-mcp-header` safety: non-empty ASCII header names, no spaces or colons,
  case-insensitive uniqueness, primitive-only mirrored fields, and no
  secret/token/password/credential/authorization-like mirrored fields.

Catalogs with no `x-mcp-header` annotations remain passing and return
`x_mcp_header.status="not_present"` with a not-applicable note.

`governance_report` includes a compact `tool_catalog_integrity` summary so audit
and release flows can cite the current public-MCP-surface digest, counts, drift
counts, and advisory lint counts without embedding the full baseline.
