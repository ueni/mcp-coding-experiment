<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Troubleshooting

## No tests discovered

Symptom:

```text
Ran 0 tests in 0.000s
NO TESTS RAN
```

Checks:

- Confirm tests are under `tests/` or configured for your runner.
- Confirm filenames match your framework conventions.
- Run collection-only mode:

```bash
pytest --collect-only tests
```

## Mount path issues (`/repo`)

Symptom:

- Tools cannot find expected files.
- Writes fail unexpectedly.

Checks:

- Confirm Docker run uses `-v "$PWD:/repo"`.
- Confirm `REPO_PATH=/repo` (or your chosen mount) matches container mount.

## Port `8000` already in use

Symptom:

```text
bind: address already in use
```

Fix options:

- Stop the process using port `8000`.
- Or map a different host port:

```bash
docker run --rm -p 8001:8000 ... codebase-tooling-mcp
```

Then use `http://localhost:8001/mcp`.

## Git commit identity missing

Symptom:

```text
Please tell me who you are
```

Fix:

```bash
git config user.name "Your Name"
git config user.email "you@example.com"
```

## Permission, authorization, or mutation denied

Symptom:

- HTTP `/mcp` or `/sse` returns 401/403.
- Write, git mutation, command, package, or network-backed tools return permission/mutation errors.

Checks:

- For HTTP mode, set `MCP_HTTP_BEARER_TOKEN` and send `Authorization: Bearer <token>`.
- If unauthenticated HTTP is intentional for a throwaway local test, set `MCP_HTTP_AUTH_MODE=insecure-local` and bind `HOST=127.0.0.1`; do not forward that port from VS Code, a devcontainer, SSH, or a tunnel.
- Confirm `ALLOW_MUTATIONS=true` when mutation tools are required. Mutating tool categories require both this flag and an authorized HTTP session.
- Keep `ALLOW_MUTATIONS=false` for read-only sessions.
- Confirm paths are inside the mounted repository root.
- Check `.codebase-tooling-mcp/audit/security_events.jsonl` (or `MCP_AUDIT_LOG_FILE`) for denied auth attempts and sensitive tool call audit events.

## HTTP rate limits or timeouts

Symptom:

- HTTP returns 429 with `Retry-After`.
- HTTP returns 504 timeout.

Checks:

- Lower client concurrency or raise `MCP_HTTP_RATE_LIMIT_REQUESTS` / `MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS` for trusted local automation.
- Narrow long-running tool requests or raise `MCP_HTTP_REQUEST_TIMEOUT_SECONDS`.
- SSE streams are exempt from the request timeout but still require authorization and count against rate limits.

## Default bootstrap files not created

Symptom:

- `.continue/models/` does not contain the default specialist model files.
- `.continue/model-routing.yaml` is missing.
- `.continue/mcpServers/codebase-tooling-mcp.yaml` is missing.
- `.config/labs/` is missing.
- `/.codebase-tooling-mcp/` was not added to `.gitignore`.
- `~/.codex/config.toml` does not contain the MCP server entry.

Checks:

- Confirm the repository was opened with the generated `.devcontainer/devcontainer.json`.
- Confirm the container environment includes `MCP_APPLY_REPO_DEFAULTS=true`.
- Rebuild or reopen the devcontainer so the image entrypoint runs again.

## Ollama stays on CPU in the devcontainer

Symptom:

```text
ollama ps
NAME                ID              SIZE      PROCESSOR
qwen3.6-35b-a3b:iq1 ...             22 GB     100% CPU
```

Checks:

- Confirm the built image uses Ollama `0.12.11` or newer; older releases do not ship Vulkan support for Intel/AMD iGPU paths.
- Confirm `.devcontainer/devcontainer.json` includes `--device=/dev/dri`.
- On AMD hosts, also expose `/dev/kfd` when it exists.
- Confirm `.devcontainer/devcontainer.json` sets `OLLAMA_VULKAN=1`.
- Inspect `/tmp/ollama.log`; if it still says `no compatible GPUs were discovered`, run `vulkaninfo --summary` inside the container and verify a real Intel or AMD GPU is visible instead of only llvmpipe.
- Rebuild or reopen the devcontainer after changing the config so Ollama restarts with the new environment.
