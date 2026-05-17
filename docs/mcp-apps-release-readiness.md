<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# MCP Apps release readiness dashboard

`release_readiness` can optionally include a read-only MCP Apps dashboard payload for VS Code MCP Apps-capable clients.

Enable it explicitly before starting the MCP server:

```bash
export MCP_APPS_DASHBOARD_ENABLED=true
```

When the flag is unset or false, `release_readiness` keeps its existing text/JSON/`structuredContent` contract and does not add an Apps payload.

With the flag enabled, the tool result still includes the normal `schema`, `base_ref`, `head_ref`, `ok`, and `checks` fields. It also adds `mcp_apps`, which references the dashboard UI resource:

```text
ui://codebase-tooling-mcp/release-readiness-dashboard
```

The dashboard is display-only. It renders real `release_readiness` result data: go/no-go status, blocking failures, selected impacted tests, docs/security/dependency-security/license/risk status, governance report status, and a rollback/snapshot reference when one is present. It may show copyable next-step tool-chain text, but it exposes no executable UI actions.

## Try in VS Code

1. Install a VS Code build with MCP Apps support.
2. Register/start this MCP server as usual.
3. Set `MCP_APPS_DASHBOARD_ENABLED=true` in the server environment.
4. Run `release_readiness(summary_mode='quick')` or `release_readiness(summary_mode='full')` from chat.

Non-Apps clients ignore the extra `mcp_apps` field and continue to display the normal structured response.
