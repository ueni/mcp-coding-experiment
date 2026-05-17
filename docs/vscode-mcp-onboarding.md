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
4. Wait for the `codebase-tooling-mcp` container to finish startup. The devcontainer publishes loopback ports `8000` (MCP) and `2345` (bundled Ollama), and VS Code should also show them as forwarded ports.
5. Run **Tasks: Run Task → MCP: Workspace Health Check**.
6. After building the local image (`codebase-tooling-mcp:test`), run **Tasks: Run Task → Devcontainer: CI Smoke Test** to start the same image, run the MCP health check, and exercise a bounded model prompt when a local model is already present.
7. Copy `.vscode/mcp.example.json` to your user/workspace MCP config if your VS Code build expects active MCP registrations outside the repository sample, then keep the token out of git. The sample uses a password input rather than a committed secret.
8. Make a test tool call from your MCP client against `http://localhost:8000/mcp` using `Authorization: Bearer <token>`.

HTTP transport hardening is enabled on `/mcp` and `/sse`:

- Missing `Origin` is accepted for non-browser MCP clients. Present browser origins must be allowed; the default allows loopback/devcontainer origins on `localhost`, `127.0.0.0/8`, and `[::1]` with HTTP or HTTPS on any port.
- If a browser client or tunnel needs another origin, set `MCP_HTTP_ALLOWED_ORIGINS` to comma-separated exact origins such as `https://mcp.example.test` and keep bearer tokens out of config, logs, and screenshots. Use `http://localhost:*` only for local port-forwarding diagnostics; avoid `*` except for a short local test.
- Clients that send `MCP-Protocol-Version` must send a supported version. The server accepts absent headers for legacy/fallback clients and rejects malformed or unsupported present values with `400` before tool execution. `MCP-Session-Id` preserves session continuity only and never replaces the bearer token.

The checked-in devcontainer preloads `qwen2.5-coder:1.5b` during
`docker build` with `OLLAMA_PRELOAD_MODELS=qwen2.5-coder:1.5b`. Use a
persistent BuildKit cache so repeated rebuilds can reuse the downloaded model
blob. Validation jobs that need a no-model image can override the build arg to
an empty value.

## What the health check verifies

`./scripts/vscode_mcp_healthcheck.py` checks:

- `GET /healthz` returns JSON and reports HTTP transport.
- The MCP server port `8000` and Ollama port `2345` are reachable from localhost.
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

## Execution-mode choice in VS Code/devcontainers

The devcontainer supports both agent execution profiles on the same MCP endpoint:

- `online-cloud-assisted` / `MCP_AGENT_EXECUTION_MODE=online`: use this when VS Code, Copilot, or another cloud-backed client owns primary reasoning. MCP still provides compact repository context, audit/memory traces, deterministic prechecks, token-saving summaries/compression, and local/offline autocomplete through the bundled Ollama service.
- `offline-onboard-only` / `MCP_AGENT_EXECUTION_MODE=offline`: use this when cloud models are unavailable, disabled, or disallowed. Local models stay bounded by structured JSON decisions while MCP runs the scripted loop: inspect -> workflow selection -> context retrieval -> patch proposal -> controlled apply -> checks -> summary.

Cloud mode optimizes quality/speed/audit/token savings. Offline mode optimizes privacy/availability and must respect confidence thresholds, clarification/escalation behavior, and hard iteration limits from [Agent execution modes](./execution-modes.md).

## Devcontainer CI smoke test

`./scripts/devcontainer_smoke_test.py` is the CI/local smoke test for the VS Code devcontainer path. It validates:

- `.devcontainer/devcontainer.json` points at `../source/Dockerfile` and `../source`.
- The devcontainer exposes HTTP MCP/Ollama environment and publishes/forwards ports `8000` and `2345`.
- `.vscode/tasks.json` contains the MCP health check task.
- A container started from the built image becomes healthy and passes `scripts/vscode_mcp_healthcheck.py`.
- A bounded streaming native Ollama `/api/chat` request with a tool schema returns a tool call when a model is already installed, preferring `CODING_AGENT_MODEL` when it is present locally; otherwise the test prints an explicit skip unless required.

The smoke test never pulls model assets by default; it uses a model already
present in the image/container or skips the prompt check:

```bash
TEST_IMAGE=codebase-tooling-mcp:test \
MCP_SMOKE_REQUIRE_MODEL_PROMPT=false \
OLLAMA_ALLOW_PULL=false \
python3 scripts/devcontainer_smoke_test.py
```

To require a real Agent-mode prompt against a preinstalled model, opt in explicitly. The script checks `/api/tags`, sends a streaming native `/api/chat` tool-call request like Continue's Ollama provider, bounds generation with `num_predict`, and uses `ollama pull` only when `OLLAMA_ALLOW_PULL=true` is set for an explicit local run:

```bash
TEST_IMAGE=codebase-tooling-mcp:test \
MCP_SMOKE_MODEL_NAME=qwen2.5-coder:1.5b \
MCP_SMOKE_REQUIRE_MODEL_PROMPT=true \
OLLAMA_ALLOW_PULL=false \
python3 scripts/devcontainer_smoke_test.py
```

GitHub Actions runs the smoke test after building the devcontainer image with these controls:

| Input/env var | Default | Behavior |
| --- | --- | --- |
| `smoke_model_name` / `MCP_SMOKE_MODEL_NAME` | empty | Use this installed model tag for the bounded prompt; empty uses the first installed model or skips when none exist. |
| `require_smoke_model_prompt` / `MCP_SMOKE_REQUIRE_MODEL_PROMPT` | `false` | Fail instead of skip if no real local model prompt can run. |
| `allow_smoke_ollama_pull` / `OLLAMA_ALLOW_PULL` | `false` | Permit runtime model pulls only for explicit opt-in runs; pull requests keep this off. |
| `MCP_SMOKE_SERVER_STARTUP_TIMEOUT_SECONDS` | `90` | Maximum time to wait for the devcontainer server health endpoint. |
| `MCP_SMOKE_MODEL_TIMEOUT_SECONDS` | `30` | Timeout for the bounded prompt request. |
| `preload_ollama_models` | `false` | Workflow input for validation-image model preloading; pull-request validation keeps this off and passes an empty build arg to exercise the no-model path. Published images still use the Dockerfile default preload. |


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
