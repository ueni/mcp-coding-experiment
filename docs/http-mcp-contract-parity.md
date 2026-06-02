<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# HTTP/MCP contract parity report

`http_mcp_contract_parity_report.v1` is an offline, read-only diagnostic that compares repository-local HTTP/HarnessAPI-style endpoint expectations with the MCP `tools/list` input/output schema contracts.

The checked-in expectation file is:

- `.config/codebase-tooling-mcp/contracts/http-mcp-contract-parity.json`

Expectation files use schema `http_mcp_contract_expectations.v1` and list endpoints with:

- `method` and `path` for the HTTP/HarnessAPI-style surface;
- `mcp_tool` for the corresponding MCP tool name;
- either inline `request_schema` / `response_schema` values or compact `request_schema_digest` / `response_schema_digest` values.

They may also list `repo_doc_surfaces` for repository-owned docs/examples that must stay aligned with the HTTP/MCP surface, such as `.vscode/mcp.example.json` and `docs/vscode-mcp-onboarding.md`. Each surface is checked offline with repo-relative paths only, using expected JSON pointer values, required text digests, or documented argument defaults compared against the MCP `tools/list` input schema.

The report canonicalizes schemas and documented values with sorted JSON and compares SHA-256 digests. Findings include endpoint ids or doc surface ids, HTTP method/path, MCP tool/argument names, repository-relative paths, JSON pointers, and expected/actual digests only. It does not embed raw schemas, endpoint payloads, bearer tokens, host absolute paths, or repository contents.

Run the local report directly with:

```bash
python3 scripts/http_mcp_contract_parity_report.py --include-passes
```

`governance_report` includes a compact `http_mcp_contract_parity` section so release and audit flows can detect HTTP/MCP contract drift without replacing existing MCP schema validation, tool-catalog integrity, or ToolFuzz checks.
