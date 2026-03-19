<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# codebase-tooling-mcp

MCP server for repository engineering workflows on a single mounted Git repository.
It exposes safe file, search, analysis, and Git tooling through MCP so assistants can inspect and modify code within `/repo` while honoring mutation controls.

## Naming and Scope

- Product name: `codebase-tooling-mcp`
- Docker service name: `codebase-tooling-mcp`
- Docker image name: `codebase-tooling-mcp:latest`
- Recommended MCP registration alias: `codebase-tooling-mcp`
- Scope: one mounted repository at `REPO_PATH` (default `/repo`)

## Quickstart (60 seconds)

### 1) Build image

```bash
docker build -t codebase-tooling-mcp ./source
```

Expected result (tail):

```text
Successfully tagged codebase-tooling-mcp:latest
```

### 2) Run HTTP server

```bash
docker run --rm \
  -p 8000:8000 \
  -e MCP_TRANSPORT=http \
  -e ALLOW_MUTATIONS=true \
  -e HOST_CA_CERT_FILE=/host-certs/ca-certificates.crt \
  -v /etc/ssl/certs:/host-certs:ro \
  -v "$PWD:/repo" \
  codebase-tooling-mcp
```

### 3) Register MCP server

```bash
claude mcp add --transport http codebase-tooling-mcp http://localhost:8000/mcp
```

Expected result (example):

```text
Added MCP server 'codebase-tooling-mcp'
```

### 4) Verify health endpoint

```bash
curl -sS http://localhost:8000/healthz
```

Expected result:

```text
ok
```

## Use With VS Code Dev Containers

1. Open this repository in VS Code.
2. Run `Dev Containers: Reopen in Container`.
3. Wait for the `codebase-tooling-mcp` container to build and start.
4. Use the MCP endpoint at `http://localhost:8000/mcp`.

The VS Code entry point is [`.devcontainer/devcontainer.json`](./.devcontainer/devcontainer.json). This repository uses a single-file devcontainer setup (no required `docker-compose.yml`).

Inline devcontainer example (non-compose):

```json
{
  "name": "codebase-tooling-mcp",
  "build": {
    "context": "..",
    "dockerfile": "../source/Dockerfile"
  },
  "workspaceFolder": "/repo",
  "containerEnv": {
    "MCP_TRANSPORT": "http",
    "ALLOW_MUTATIONS": "true"
  },
  "forwardPorts": [8000]
}
```

The Dockerfile uses BuildKit cache mounts for `apt` and `pip`, so repeated
devcontainer rebuilds can reuse downloaded package metadata and wheels. Keep
BuildKit enabled when building this image or those cache mounts will be ignored.

Inside the container, the `app` user can run `sudo` without a password.

If you still want compose for local runs outside VS Code, use this inline example:

```yaml
services:
  codebase-tooling-mcp:
    image: codebase-tooling-mcp:latest
    build: ./source
    environment:
      MCP_TRANSPORT: http
      ALLOW_MUTATIONS: "true"
      REPO_PATH: /repo
      HOST: 0.0.0.0
      PORT: "8000"
    ports:
      - "8000:8000"
    volumes:
      - .:/repo
```

Run it with:

```bash
docker compose up --build
```

### VS Code Inline Autocomplete Extension (MCP-backed)

A minimal extension is included at [`vscode/mcp-inline-autocomplete`](./vscode/mcp-inline-autocomplete).

Run it in VS Code:

1. Open [`vscode/mcp-inline-autocomplete/package.json`](./vscode/mcp-inline-autocomplete/package.json).
2. Press `F5` (Run Extension) to start an Extension Development Host.
3. In the dev host, open Command Palette and run `MCP Inline Autocomplete: Show Status`.
4. Start typing in a file; inline suggestions come from MCP tool `autocomplete` at `http://localhost:8000/mcp`.

Key settings (in VS Code Settings):

- `mcpInlineAutocomplete.endpoint` (default `http://localhost:8000/mcp`)
- `mcpInlineAutocomplete.maxTokens`
- `mcpInlineAutocomplete.temperature`
- `mcpInlineAutocomplete.enabledLanguages`

## Bootstrap Another Repository

To add this MCP setup to another repository using the published image
`ueniueni/codebase-tooling-mcp:latest`, run:

```bash
curl -fsSL https://raw.githubusercontent.com/ueni/mcp-coding-experiment/main/setup-repository.sh | sh
```

The script finds the repository root by locating `.git` and creates only:

- `.devcontainer/devcontainer.json`

When the devcontainer starts, the image applies default repository files if they
are missing:

- `.continue/models/*.yaml` (router + specialist model defaults, repo-owned)
- `.continue/model-routing.yaml` (routing map for router/specialists)
- `.continue/mcpServers/codebase-tooling-mcp.yaml`
- `.config/labs/*.json`
- `/.build/`, `/.continue/`, `/.config/`, `/.devcontainer/`, `/.gitignore_codebase_tooling_mcp.touched` entries in `.gitignore` (one-time bootstrap)

The image also ensures a default Codex MCP client entry exists at:

- `~/.codex/config.toml`

That generated Codex entry uses the server key `codebase-tooling-mcp`:

```toml
[mcp_servers."codebase-tooling-mcp"]
url = "http://localhost:8000/mcp"
```

The `.gitignore` bootstrap is intentionally one-time. A marker file
`.gitignore_codebase_tooling_mcp.touched` is created on first apply; after that,
removed generated entries are not re-added automatically.

For home-config portability, the generated devcontainer mounts host paths under
`/host` for `~/.continue` and `~/.gitconfig`, and mounts `~/.codex` directly to
`/home/app/.codex`. Startup bootstrap copies from `/host` mounts only when the
`$HOME` targets are missing or empty.

The inline autocomplete extension and the Marketplace extensions declared in the devcontainer are preloaded into the image during `docker build` for the common VS Code server extension directories, so the target
repository does not need a local `vscode/mcp-inline-autocomplete/` copy and VS Code should not need to fetch those extensions again on container start.

## Endpoints (HTTP mode)

- MCP endpoint: `http://localhost:8000/mcp`
- Health endpoint: `http://localhost:8000/healthz`

## Example Claude Code registration

### HTTP server

```bash
claude mcp add --transport http codebase-tooling-mcp http://localhost:8000/mcp
```

### Local stdio server via Docker

```json
{
  "mcpServers": {
    "codebase-tooling-mcp": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "-e",
        "MCP_TRANSPORT=stdio",
        "-e",
        "ALLOW_MUTATIONS=true",
        "-v",
        "/absolute/path/to/repo:/repo",
        "codebase-tooling-mcp"
      ]
    }
  }
}
```

## Configuration Reference

| Variable | Default | Required | Allowed Values | Effect |
|---|---|---|---|---|
| `MCP_TRANSPORT` | `http` | No | `http`, `stdio`, `direct`, `streamable-http`, `streamable_http` | Selects server transport mode. |
| `REPO_PATH` | `/repo` | No | Absolute path | Root path tools may operate on. |
| `ALLOW_MUTATIONS` | `false` (recommended default) | No | `true`, `false` | Enables/disables write and git-mutating operations. |
| `HOST` | `0.0.0.0` | No | Host/IP string | Bind address for HTTP mode. |
| `PORT` | `8000` | No | Integer port | HTTP listen port. |
| `MAX_READ_BYTES` | `262144` | No | Positive integer | Max bytes read by file tools per request. |
| `MAX_OUTPUT_CHARS` | `200000` | No | Positive integer | Output truncation limit for tool responses. |
| `CONTINUE_OLLAMA_MODELS` | `qwen2.5-coder:7b,granite3.2:2b,phi4-mini:3.8b,phi4-mini-reasoning:3.8b,deepseek-r1:1.5b,deepscaler:1.5b,granite3.2-vision:2b,llama3.2:3b` | No | Comma-separated model IDs (or empty) | Models ensured via `ollama pull` at startup; set to empty to skip pre-pull. |
| `OLLAMA_ENABLED` | `true` | No | `true`, `false` | Enables/disables Ollama startup in `entrypoint.sh`. |
| `OLLAMA_STARTUP_TIMEOUT` | `30` | No | Integer seconds | Max wait time for Ollama readiness before fallback/failure logic. |
| `OLLAMA_HOST` | `127.0.0.1:11434` | No | `host:port` | Primary bind target for `ollama serve`. The devcontainer overrides this to `0.0.0.0:2345` so the bundled Ollama service is reachable from the host on port `2345`. |
| `OLLAMA_FALLBACK_HOST` | `0.0.0.0:11434` | No | `host:port` | Secondary bind target used if primary Ollama host fails. The devcontainer keeps this aligned to `0.0.0.0:2345`. |
| `ALLOW_ORIGINS` | `*` | No | CORS origin list | Controls browser/client origins for HTTP mode. |
| `SSL_CERT_FILE` | `/etc/ssl/certs/ca-certificates.crt` | No | Path | CA bundle for outbound HTTPS. |
| `HOST_CA_CERT_FILE` | empty | No | Path | Optional mounted host CA bundle path. |

## Continue + Ollama Contract

- The checked-in Continue model configs use `provider: ollama` with `apiBase: http://127.0.0.1:2345`.
- This repo treats the native Ollama base as the contract for Continue's Ollama provider. Do not append `/v1` when configuring those model YAMLs.
- `source/entrypoint.sh` is responsible for pre-pulling the models listed in `CONTINUE_OLLAMA_MODELS`, but it now does that in the background after Ollama is reachable so the MCP server can start immediately; `source/server.py` only reports endpoint and model state.
- Setting `CONTINUE_OLLAMA_MODELS` to an empty value is an explicit opt-out of model pre-pull. In that mode, Continue may report `model not found` until models are installed manually.
- A `404` on `http://127.0.0.1:2345/v1/` does not invalidate the native Ollama integration in this repo; the native base and `/api/tags` are the relevant health checks.

## Safety and Mutation Controls

- Path traversal outside the mounted repository is blocked.
- Read-only usage is the safest default: keep `ALLOW_MUTATIONS=false` unless changes are required.
- Mutating operations (for example `write_file`, `delete_path`, `move_path`, Git writes) require `ALLOW_MUTATIONS=true`.
- Git commits still require Git user identity in repo config or environment.
- In stdio mode, avoid writing logs to stdout to preserve protocol framing.

## Tool Catalog by Category

### Public MCP v1 Surface

- `autocomplete`
- `repo_info`
- `runtime_state`
- `repo_router` (tree, find, read, read_document, read_snippet, read_batch, query_json)
- `workspace_transaction` (begin, apply, validate, rollback, commit, snapshot, restore, write, replace, move, delete, apply_diff)
- `git_router` (init, status, diff, log, show, add, restore, commit, checkout, create_branch, fetch, pull, push, summarize_diff, risk, security)
- `code_index_router` (refresh, read, query, symbols, deps, calls, search, grep, tree, ast, impact_tests, doc_sync, api_surface)
- `model_router`
- `memory_router` (upsert, summary_upsert, decision_record, get, validate, auto_compact, failure_memory, root_cause, artifact_index)
- `docker_router`
- `vscode_router`
- `command_runner` (safe allowlisted command execution; non-allowlisted commands return a pending approval request instead of a failed execution)
- `tool_router` (route, record, inspect)
- `quality_router` (self_test, self_check, release_readiness, flaky, change_impact, required_tool_chain, spec_to_tests, smart_fix)
- `governance_router` (policy, license, runtime_contract, human_approval, commit_lint)
- `workflow_router` (fast_path, compile, multi_agent, constraint_check, confidence, artifact_index, failure_memory, root_cause, execution_replay, auto_shard)
- `runtime_guard_router` (benchmark, output_size, golden_output, token_budget, cost_budget, cache, result_handle, workspace_facts)
- `math_router` (parse, solve, verify)
- `document_router` (ocr, image, presentation, translate)
- `diagram_router` (from_code, lint_mermaid, drawio, sync_check)
- `sql_expert`
- `browse_web`

Leaf implementations remain in `source/server.py` as internal helpers and compatibility-free call targets for the routers above. Only the tools listed here are exposed over MCP v1.

## Labs and Reports

Prototype automations for advanced workflows live under `source/labs`.
See [MCP Fun Labs](./docs/labs.md) for command examples and expected outputs.

## Documentation

- [Documentation Index](./docs/index.md)
- [Tooling White Paper](./docs/tooling-whitepaper.md)
- [JSON Settings Files](./docs/json-settings.md)
- [MCP Fun Labs](./docs/labs.md)
- [Troubleshooting](./docs/troubleshooting.md)
- [Release Notes and Documentation Policy](./docs/release-notes-policy.md)
