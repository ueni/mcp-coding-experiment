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
- OAuth-capable MCP clients should read `/.well-known/oauth-protected-resource`; 401 responses include a `WWW-Authenticate` `resource_metadata` parameter pointing at that document plus `scope="mcp:read"` for least-privilege discovery/read access.
- If `MCP_HTTP_BEARER_TOKEN_SCOPES` is set, confirm it contains only `mcp:read` and/or `mcp:mutate`. Empty preserves the old single-token behavior and grants both scopes; `mcp:read` alone can run read-only tools but mutation/sensitive categories (`write`, `git mutation`, `shell/process`, `network`, `secret-sensitive`) require `mcp:mutate` and otherwise fail with `insufficient_scope`.
- If unauthenticated HTTP is intentional for a throwaway local test, set `MCP_HTTP_AUTH_MODE=insecure-local` and bind `HOST=127.0.0.1`; do not forward that port from VS Code, a devcontainer, SSH, or a tunnel.
- Confirm `ALLOW_MUTATIONS=true` when mutation tools are required. Mutating tool categories require both this flag and an authorized HTTP session with `mcp:mutate`.
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
- If Continue defaults are expected, confirm `MCP_APPLY_CONTINUE_DEFAULTS` is unset or `true`; setup with `--continue-model-profile none` intentionally writes `MCP_APPLY_CONTINUE_DEFAULTS=false` so startup will not create `.continue` model or MCP profile files.
- Rebuild or reopen the devcontainer so the image entrypoint runs again.

## Continue suggests adding `/v1` to `apiBase`

Symptom:

```text
This may mean that you forgot to add '/v1' to the end of your 'apiBase' in config.json.
```

Checks:

- Treat this as a generic Continue connection hint for this repository's
  checked-in `provider: ollama` model configs. Keep
  `apiBase: http://127.0.0.1:2345` without `/v1`.
- Confirm Continue is loading the repository `.continue` YAML config, not a
  stale legacy `config.json` or OpenAI-compatible model entry.
- From the same side where Continue is running, confirm the native Ollama API
  responds:

  ```bash
  curl http://127.0.0.1:2345/api/tags
  ```

- If `/api/tags` works but Continue still cannot chat, check that the configured
  model is installed and that the native `/api/chat` path is healthy. A `404` on
  `http://127.0.0.1:2345/v1/` is expected for this native Ollama route.

## Devcontainer build fails while pulling an Ollama model

Symptom:

```text
timeout 7200 ollama pull qwen2.5-coder:1.5b
ERROR: failed to build: failed to receive status: rpc error: code = Unavailable desc = ... error reading from server: EOF
```

Fix:

- The checked-in devcontainer preloads `qwen2.5-coder:1.5b` by default. Run the
  build on a stable network with a persistent BuildKit cache so retries can
  reuse completed blobs.
- If you need to validate startup without a baked model, override the build arg
  to an empty value and keep runtime downloads opt-in with
  `OLLAMA_ALLOW_PULL=false`.
- After a no-model validation container starts, install the model manually with
  `ollama pull qwen2.5-coder:1.5b` only when you want local inference available.

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

- Rebuild/reopen after increasing Docker/VM memory. The default local model is
  the compact `qwen2.5-coder:1.5b` profile with an `8192` context to keep
  laptop-class startup and Agent/MCP diagnostics predictable. Increase the
  context to 16384/32768 only after confirming enough memory, or configure a
  verified tool-capable Agent model locally when Agent tool calling requires it.
- Close other memory-heavy workloads before rebuild/reopen.
- Keep `OLLAMA_ALLOW_PULL=false` unless runtime downloads are intentionally
  enabled; build-time or published-image preloads avoid extra memory/network
  pressure during attach when you have intentionally created such an image.
- Temporarily set `OLLAMA_ENABLED=false` to distinguish VS Code Server attach
  memory pressure from Ollama startup/model checks, then restore Ollama for the
  final verification path.
- If the attach succeeds, confirm the container remains alive after VS Code
  attaches by checking the MCP server process, for example:

```bash
docker inspect --format 'running={{.State.Running}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}}' <devcontainer-name-or-id>
docker exec <devcontainer-name-or-id> pgrep -af 'python /app/server.py|server.py'
```

## Vulkan crashes the Ollama runner in the devcontainer

Symptom:

```text
model runner has unexpectedly stopped
radv/amdgpu: The CS has been cancelled because the context is lost.
terminate called after throwing an instance of 'vk::DeviceLostError'
what():  vk::Queue::submit: ErrorDeviceLost
```

Fix:

- Keep the default `.devcontainer/devcontainer.json` setting `OLLAMA_VULKAN=0`
  and rebuild/reopen the devcontainer so Ollama restarts on the CPU backend.
- Remove any local `--device=/dev/dri` / `--device=/dev/kfd` additions unless
  you are explicitly validating Vulkan acceleration.
- If you opt into Vulkan with `setup-repository.sh --enable-vulkan-gpu`, inspect
  `/tmp/ollama.log` after a real `/api/chat` Agent/tool request. A
  `vk::DeviceLostError` means the GPU path is not stable for this host/driver
  combination; return to `OLLAMA_VULKAN=0`.
