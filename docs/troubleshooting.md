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

- For HTTP mode, set `MCP_HTTP_BEARER_TOKEN` before starting the server and send
  `Authorization: Bearer <token>` from the client.
- For Continue IDE MCP, keep the server token out of tracked YAML and make the
  same value resolvable as `${{ secrets.MCP_HTTP_BEARER_TOKEN }}`. Use Continue
  Settings > Secrets for personal IDE use, or a local secret source such as
  `.env`, `.continue/.env`, or `~/.continue/.env` with
  `MCP_HTTP_BEARER_TOKEN=<token>`.
- If Continue reports `unresolved secrets: MCP_HTTP_BEARER_TOKEN`, the IDE could
  not resolve the client-side secret. If `/mcp` also reports
  `MCP_HTTP_BEARER_TOKEN is not configured`, restart or rebuild the server after
  exporting the same token into its environment.
- In devcontainers, an empty `${localEnv:MCP_HTTP_BEARER_TOKEN}` usually means
  VS Code was launched without that environment variable. Rebuild/reopen the
  container after setting it, or let the entrypoint generate `.continue/.env` on
  the next startup and use that local file as the Continue secret source.
- If `MCP_HTTP_AUTH_MODE=oauth-resource`, also set `MCP_HTTP_AUTHORIZATION_SERVERS` to a JSON list or comma-separated list of issuer URLs, for example `MCP_HTTP_AUTHORIZATION_SERVERS='["https://auth.example.test"]'`. Without it, protected MCP endpoints fail closed with 403 and `/healthz` reports `auth.configuration_error`.
- OAuth-capable MCP clients should read `/.well-known/oauth-protected-resource`; 401 responses include a `WWW-Authenticate` `resource_metadata` parameter pointing at that document.
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


## VS Code Server attach fails with exit code 137

Symptom:

```text
Installing VS Code Server ...
Exit code: 137
```

Exit code `137` means the VS Code Server install process was killed with
`SIGKILL`. For Dev Containers this is most often an out-of-memory kill during
server extraction, extension installation, or model/bootstrap startup.

Collect deterministic evidence before changing the container shape:

```bash
# From the host, replace the name/id with the affected devcontainer.
scripts/devcontainer_exit137_diagnostics.sh <devcontainer-name-or-id> \
  > devcontainer-exit137-diagnostics.txt

# If already attached to a shell inside the container and the Docker socket is mounted:
scripts/devcontainer_exit137_diagnostics.sh "$HOSTNAME" \
  > /tmp/devcontainer-exit137-diagnostics.txt
```

The diagnostic report includes:

- `docker inspect` OOM and exit state (`State.OOMKilled`, `State.ExitCode`,
  `State.Error`, start/finish time, PID, memory and swap limits).
- Cgroup memory current/peak/limit/event counters, including `memory.current`,
  `memory.peak`, `memory.events`, `memory.events.local`, and swap counters on
  cgroup v2 hosts, with cgroup v1 fallbacks.
- Process list sorted by RSS so large VS Code Server, Node, Python, Ollama, or
  package-install processes are visible.
- Memory and swap state from `free -h`, `swapon --show`, and `/proc/meminfo`.
- Relevant kernel and Docker OOM messages from `dmesg`, `journalctl -k`,
  `docker logs`, and `docker events`.

Diagnosis hints:

- `State.OOMKilled=true`, `State.ExitCode=137`, cgroup `oom`/`oom_kill` event
  counters, or kernel lines like `Memory cgroup out of memory` confirm an OOM
  kill rather than a VS Code Server installer bug.
- A cgroup `memory.max` lower than host RAM means Docker Desktop, Colima,
  systemd slices, or the runtime imposed a tighter limit than expected.
- High RSS for `node`, `vscode-server`, `ollama`, `python`, or package manager
  processes identifies the competing memory user.

Remediation options:

- Rebuild/reopen after increasing Docker/VM memory. For the Qwen3.6 IQ1_M
  devcontainer path, use a 32GB T14-class host or equivalent Docker memory
  allocation when collecting final attach evidence. A ThinkPad T14 Gen 1 AMD
  with 16GB RAM may be marginal for VS Code + devcontainer + Ollama, especially
  with `llama3.1:8b` Agent mode and a 32768 context; reduce the context to
  8192/16384 or use a smaller verified tool-capable Agent model if one is
  configured locally.
- Close other memory-heavy workloads before rebuild/reopen.
- Keep `OLLAMA_ALLOW_PULL=false` unless runtime downloads are intentionally
  enabled; preloaded models avoid extra memory/network pressure during attach.
- Temporarily set `OLLAMA_ENABLED=false` to distinguish VS Code Server attach
  memory pressure from Ollama startup/model checks, then restore Ollama for the
  final verification path.
- If the attach succeeds, confirm the container remains alive after VS Code
  attaches by checking the MCP server process, for example:

```bash
docker inspect --format 'running={{.State.Running}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}}' <devcontainer-name-or-id>
docker exec <devcontainer-name-or-id> pgrep -af 'python /app/server.py|server.py'
```

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
