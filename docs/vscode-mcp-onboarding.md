<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# VS Code MCP Onboarding

This path starts from a fresh clone or downstream repository using the devcontainer bootstrap and ends with a verified MCP endpoint ready for a VS Code MCP client.

## Fresh clone path

1. Clone the repository and open it in VS Code.
2. Generate a local-only HTTP token before the container starts:

   ```bash
   export MCP_HTTP_BEARER_TOKEN="$(openssl rand -hex 32)"
   ```

3. Run **Dev Containers: Reopen in Container**.
4. Wait for the `codebase-tooling-mcp` container to finish startup. VS Code should forward ports `8000` (MCP) and `2345` (bundled Ollama).
5. Run **Tasks: Run Task → MCP: Workspace Health Check**.
6. Copy `.vscode/mcp.example.json` to your user/workspace MCP config if your VS Code build expects active MCP registrations outside the repository sample, then keep the token out of git. The sample uses a password input rather than a committed secret.
7. Make a test tool call from your MCP client against `http://localhost:8000/mcp` using `Authorization: Bearer <token>`.

## What the health check verifies

`./scripts/vscode_mcp_healthcheck.py` checks:

- `GET /healthz` returns JSON and reports HTTP transport.
- The MCP server port `8000` and Ollama port `2345` are reachable through VS Code forwarding.
- The health payload reports the expected mutation mode (`ALLOW_MUTATIONS=true` by default for editing workflows).
- Ollama is running and `GET http://localhost:2345/api/tags` responds.
- HTTP authorization matches the behavior from the token-mode server: unauthenticated MCP requests are rejected, and a request with `MCP_HTTP_BEARER_TOKEN` reaches the MCP endpoint.

Useful overrides:

```bash
MCP_HEALTHCHECK_BASE_URL=http://localhost:8000 \
MCP_HEALTHCHECK_OLLAMA_URL=http://localhost:2345 \
MCP_HEALTHCHECK_EXPECT_ALLOW_MUTATIONS=true \
python3 scripts/vscode_mcp_healthcheck.py
```

The script prints remediation text for common failures: container not started, missing forwarded ports, missing token, wrong mutation mode, or Ollama not listening.


## Clarification fallback checklist and elicitation

VS Code/Copilot clients should display `clarification_gate` results before risky mutation, release, or security follow-up workflows. If `ok_to_continue=false`, render `fallback_checklist` as a blocking checklist and do not recommend mutation or release action until the missing non-sensitive fields are supplied.

Clients that support MCP elicitation can translate `elicitation.request` into an `elicitation/create` request. Only ask for the flat non-sensitive fields listed in the schema, and honor `accept`, `decline`, and `cancel` actions. Never request passwords, bearer tokens, API keys, credentials, private keys, or other sensitive values through this gate.

## Downstream repository bootstrap

Downstream repositories can opt into the same VS Code MCP setup with:

```bash
curl -fsSL https://raw.githubusercontent.com/ueni/mcp-coding-experiment/main/setup-repository.sh | sh
```

Before reopening the generated devcontainer, set `MCP_HTTP_BEARER_TOKEN` in the VS Code parent shell. The generated devcontainer passes it through with `${localEnv:MCP_HTTP_BEARER_TOKEN}` and does not store the secret in git.

If you also want the repository-local sample MCP config and health task, copy these files from this repository or vendor them in your own template:

- `.vscode/mcp.example.json`
- `.vscode/tasks.json` task `MCP: Workspace Health Check`
- `scripts/vscode_mcp_healthcheck.py`

Keep committed samples secret-free. Prefer VS Code password inputs (`${input:...}`) or environment variables (`MCP_HTTP_BEARER_TOKEN`) for bearer tokens.
