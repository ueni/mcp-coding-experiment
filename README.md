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

## Coding agents

Start with [`AGENTS.md`](./AGENTS.md) for the concise repository-owned coding-agent entrypoint. It maps agent workflow, guardrails, public MCP routers, generated artifacts, and PR expectations back to the canonical docs.

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

HTTP mode requires bearer-token authorization by default. Generate a local token before starting the server:

```bash
export MCP_HTTP_BEARER_TOKEN="$(openssl rand -hex 32)"

docker run --rm \
  -p 127.0.0.1:8000:8000 \
  -p 127.0.0.1:2345:2345 \
  -e MCP_TRANSPORT=http \
  -e MCP_HTTP_BEARER_TOKEN="$MCP_HTTP_BEARER_TOKEN" \
  -e ALLOW_MUTATIONS=true \
  -e HOST_CA_CERT_FILE=/host-certs/ca-certificates.crt \
  -v /etc/ssl/certs:/host-certs:ro \
  -v "$PWD:/repo" \
  codebase-tooling-mcp
```

### 3) Register MCP server

Send the same token to MCP clients as an `Authorization: Bearer ...` header:

```bash
claude mcp add --transport http codebase-tooling-mcp http://localhost:8000/mcp \
  --header "Authorization: Bearer $MCP_HTTP_BEARER_TOKEN"
```

Expected result (example):

```text
Added MCP server 'codebase-tooling-mcp'
```

### HTTP transport hardening

Protected HTTP MCP endpoints (`/mcp` and `/sse`) validate browser `Origin` headers in addition to bearer auth. Non-browser clients that omit `Origin` continue to work. By default, local/devcontainer origins on loopback hosts are allowed (`localhost`, `127.0.0.0/8`, and `[::1]`, with HTTP or HTTPS and any port) so VS Code port forwarding and local clients keep working.

Use `MCP_HTTP_ALLOWED_ORIGINS` only when a browser-based client needs a non-loopback origin. Set it to comma-separated exact origins or explicit port wildcards, for example:

```bash
-e MCP_HTTP_ALLOWED_ORIGINS="https://mcp.example.test,http://localhost:*"
```

Prefer exact HTTPS origins for tunnels. Avoid `*` except for a short local diagnostic, and never put bearer-token values in origin settings or logs.

When a client sends `MCP-Protocol-Version`, the server accepts only configured supported versions and rejects malformed or unsupported values with `400` before tool execution. The default supported versions are `2024-11-05`, `2025-03-26`, `2025-06-18`, and `2025-11-25`; override with `MCP_HTTP_SUPPORTED_PROTOCOL_VERSIONS` only when intentionally narrowing or extending client compatibility. Missing protocol-version headers are accepted for legacy/fallback clients. `MCP-Session-Id` is session continuity only; it never replaces `Authorization: Bearer ...`.

### 4) Verify health endpoint

```bash
curl -sS http://localhost:8000/healthz
```

Expected result (example):

```json
{
  "ok": true,
  "repo_path": "/repo",
  "is_git_repo": true,
  "allow_mutations": true,
  "transport": "http",
  "runtime_image_version": "0.0.0-local-build",
  "mcp_coding_experiment_version": "0.0.0-local-build",
  "server": {
    "http_mode": true,
    "port": 8000,
    "port_listening": true
  },
  "ollama": {
    "running": true,
    "serve_processes": 1,
    "configured_port": 11434,
    "configured_port_listening": true,
    "port_11434_listening": true
  }
}
```

### Provisional MCP discovery manifest

HTTP mode also serves a provisional discovery manifest at:

```bash
curl -sS http://localhost:8000/.well-known/mcp-server.json
```

The endpoint is intentionally unauthenticated so clients can run a preflight before opening an MCP session. It advertises only public, allowlisted metadata: server names, relative transport and health URLs, auth scheme requirements, public MCP tool/resource/prompt names, schema/contract identifiers, and tool risk/category annotations where available.

The manifest is non-final SEP discovery work (`mcp-server-manifest.provisional.v1`), so clients should treat field names as provisional and prefer defensive parsing. It must not contain repository contents, bearer tokens, local absolute paths, environment values, host user data, or secrets. The protected MCP endpoint remains `/mcp`; the discovery manifest does not weaken bearer-token enforcement for MCP calls.

### MCP Registry server.json readiness

This repository also checks in `server.json` for official MCP Registry release metadata. It is distinct from the runtime discovery manifest above: `server.json` describes the publishable package identity and install surface, not one running server instance.

Initial registry identity is `io.github.ueni/codebase-tooling-mcp` with an OCI/GHCR package channel at `ghcr.io/ueni/codebase-tooling-mcp:<version>`. Publication is not automatic. Use the local dry-run gate before changing release metadata or publishing:

```bash
python3 scripts/registry_readiness.py validate --compact
```

See [MCP Registry server.json readiness](./docs/mcp-registry-readiness.md) for the selected package channel, Docker ownership marker, version expectations, and maintainer-gated `mcp-publisher` OIDC publishing path.

## VS Code MCP Onboarding

For a complete VS Code MCP path from fresh clone/devcontainer to a verified tool call, see [VS Code MCP Onboarding](./docs/vscode-mcp-onboarding.md). The workspace task **MCP: Workspace Health Check** validates `/healthz`, `/mcp`, forwarded ports `8000`/`2345`, Ollama status, mutation mode, and HTTP bearer-token state without committing secrets.

## MCP prompts in VS Code and Copilot

This server exposes a curated prompt pack for clients that support MCP prompts, including VS Code and Copilot Chat slash-command workflows. After registering the MCP endpoint, use the client's MCP prompt picker or slash-command UI to discover:

- `review_changed_files` - read-only branch diff review with impact and validation guidance.
- `release_readiness_check` - release gate summary backed by existing readiness workflows.
- `security_triage` - security-focused triage that avoids secret exposure and policy bypasses.
- `devcontainer_health_check` - VS Code/devcontainer MCP endpoint, auth, port, and Ollama diagnostics.
- `snapshot_before_refactor` - pre-refactor snapshot and rollback planning before mutation work.

The prompts are workflow starters, not bypasses: they route users toward existing tools such as `task_router`, `quality_router`, `release_readiness`, `change_impact_gate`, and `state_snapshot`, while preserving mutation, authentication, and rollback guardrails. When unsure which workflow to use, call `task_router(mode="workflow_select", prompt="...", execution_mode="auto")` first; it returns read-only, ranked workflow cards with confidence, caveats, online/offline execution-mode guidance, and compact trust/safety metadata. External workflow-card loading remains disabled by default; proposed generated or external cards must pass the documented trust-lint review before import. See [Workflow selection cards](./docs/workflow-selection.md).

## Agent execution modes

`codebase-tooling-mcp` defines two profiles on the same workflow-card/router path:

- `online-cloud-assisted` (`MCP_AGENT_EXECUTION_MODE=online`): a cloud model handles primary reasoning for quality and speed while MCP supplies compact repository context, audit traces, project/repo memory, deterministic prechecks, token-saving summaries/compression, and local/offline autocomplete.
- `offline-onboard-only` (`MCP_AGENT_EXECUTION_MODE=offline`): no cloud model is required; local models make bounded structured decisions while MCP orchestrates inspect -> workflow selection -> context retrieval -> patch proposal -> controlled apply -> checks -> summary with confidence thresholds, clarification/escalation paths, and hard iteration limits.

Use cloud mode for quality/speed/audit/token savings. Use offline mode for privacy/availability with scripted agent emulation. See [Agent execution modes](./docs/execution-modes.md).

### Explicit OpenAI-compatible agent API proxy

An opt-in proxy can serve `POST /v1/chat/completions` for clients that deliberately configure this server as their OpenAI-compatible `base_url`. It is disabled by default and is not hidden MITM/TLS interception or credential capture. The first slice supports non-streaming responses, `stream: true` SSE chunks, local/offline routing, explicit online provider controls, request-local anonymization, irreversible secret redaction, fail-closed disclosure audit, durable buyer/auditor evidence packets, disclosure summaries, and gated compact memory capture. See [Explicit Agent API Proxy](./docs/agent-api-proxy.md).

### Static test impact map workflow

Use `test_impact_map` when you need a repeatable, TDAD-style view of which Python tests should cover a source change. In normal read mode it loads the repository-local artifact at `.codebase-tooling-mcp/reports/TEST_IMPACT_MAP.json`, checks that it is still fresh, and returns `selected_tests`, `test_details`, `confidence`, `impacted_sources`, `coverage_gaps`, and `unmapped_changed_files` for explicit `changed_files`.

Call `test_impact_map(refresh=true)` to rebuild and write the artifact. Refresh is a write-mode operation guarded by mutation settings (`ALLOW_MUTATIONS`); read/query calls do not write. The artifact is considered fresh only when it has the expected schema, is not older than `max_age_hours` (24 hours by default), and its Python source fingerprint still matches the workspace. Absent, invalid, or stale artifacts are reported through `artifact_status` instead of being silently trusted.

`impact_tests` now prefers a fresh impact-map artifact. If the artifact is absent, invalid, stale, or cannot map a changed Python source, it falls back to dependency/naming heuristics and reports the fallback through `impact_map.fallback_used` plus `impact_map.artifact_status`. Both `impact_tests` and `change_impact_gate` expose `unmapped_changed_files`; treat those paths as coverage gaps that need manual review or new tests before relying on automated selection. `quality_router(mode="change_impact")` wraps the same `change_impact_gate` result, including selected tests and unmapped files.

For dependency supply-chain review, `dependency_security_report` builds a read-only dependency inventory, matches repository-local or caller-provided advisory data, distinguishes clean/vulnerable/skipped/stale/network-disabled/scanner-unavailable states, and exports JSON plus an optional CycloneDX-compatible SBOM with local provenance sidecars under `.codebase-tooling-mcp/reports/`. It never edits dependency files or performs live network lookup unless a future caller explicitly enables that path; the current offline-safe slice consumes advisory fixtures or externally generated `pip-audit` JSON. See [Dependency security report](./docs/dependency-security.md).

For enterprise audit/release review, `workflow_policy_plan` can preflight an intent-declared MCP tool sequence before execution, and `governance_report` reads redacted events from `MCP_AUDIT_LOG_FILE`, summarizes local policy/readiness/tool-chain/snapshot/dependency-security/tool-catalog-integrity/workflow-policy evidence, and exports JSON plus Markdown plus local provenance sidecars under `.codebase-tooling-mcp/reports/`. It also writes a redacted deterministic `workflow_lineage.v1` manifest for the governance-report workflow; `workflow_lineage(mode="verify")` checks the manifest read-only and reports `matched`, `input_changed`, `artifact_changed`, and `non_deterministic_node` conditions without persisting prompts, transcript snippets, secrets, file contents, or host absolute paths. `artifact_provenance` verifies report sidecars and snapshot-index sidecars read-only, reports existing unsigned sidecars as `unsigned` / `local-only`, validates deterministic offline `local-dsse-fixture` attestations without network calls, and can opt into `github-artifact-attestations` verification when the sidecar/config provides a local bundle, trusted root, expected repository/workflow/ref-or-commit policy, and predicate type. GitHub attestation verification is offline by default; online `gh attestation verify` is unavailable unless explicitly enabled and reports `network_access=true` only when it actually uses network access. Other future backends such as Sigstore/cosign remain `unsupported`. `workflow_task` can start the governance report asynchronously and persist a redacted MCP Tasks-style status handle under `.codebase-tooling-mcp/tasks/`; poll it with `task_status`. `workflow_diagnostics` turns failed audit events and optional caller-supplied trajectory snippets into a redacted critical-step/failure-category report with safe recovery actions. `tool_catalog_integrity` compares live public MCP tool metadata against the checked-in digest baseline and returns added/removed/changed metadata diffs plus advisory metadata-lint findings without exposing repository contents or secrets. `self_optimization_report` builds an offline-by-default, repo-local efficiency report for recent MCP usage, token/time savings, cache/compression effects, issue/PR/workflow/task throughput, state transitions, test gates, retries/rework, blocked/waiting time, bottlenecks, confidence/caveats for unknown inputs, and duplicate-suppressed optimization candidates with explicit high-confidence GitHub issue update gating. #88 proxy/anonymizer/disclosure/router data is treated only as non-blocking redacted enrichment for routing/disclosure metrics when available. `interaction_invariant_audit` checks extracted task constraints and interaction-smell risks before mutation or readiness summaries without persisting conversation snippets by default. `grep` and `governance_report` can also opt into deterministic `compressed_observation` summaries without replacing raw results or artifacts. See [Dependency security report](./docs/dependency-security.md), [Governance report workflow](./docs/governance-report.md), [Tool catalog integrity baseline](./docs/tool-catalog-integrity.md), [Self-optimization efficiency report](./docs/self-optimization-report.md), [Workflow lineage manifests](./docs/workflow-lineage.md), [Workflow policy plan preflight](./docs/workflow-policy-plan.md), [Async workflow tasks](./docs/workflow-tasks.md), [Workflow diagnostics](./docs/workflow-diagnostics.md), [Interaction invariant audit](./docs/interaction-invariant-audit.md), and [Adaptive observation compression](./docs/observation-compression.md).

For opt-in observability, `MCP_OTEL_TRACING_ENABLED=true` with `MCP_OTEL_EXPORTER=jsonl` writes redacted local span records for public tool execution, workflow selection, async task lifecycle events, and policy-gate denials under `.codebase-tooling-mcp/traces/`. Tracing is disabled by default, uses the audit redaction path before recording attributes, and does not capture raw prompts, file contents, command output, bearer tokens, or host absolute paths. See [Opt-in OpenTelemetry tracing](./docs/opentelemetry-tracing.md).

For underspecified high-risk workflows, `clarification_gate` returns structured missing-field decisions, fallback checklist questions, and a non-sensitive MCP elicitation adapter before mutation or release recommendations. See [Clarification Gate](./docs/clarification-gate.md).

For VS Code MCP Apps-capable clients, `release_readiness` can include a read-only dashboard when `MCP_APPS_DASHBOARD_ENABLED=true`. The default is disabled so existing clients keep the same response contract. See [MCP Apps release readiness dashboard](./docs/mcp-apps-release-readiness.md).

For MCP client workspace boundary checks, `roots_diagnostics` compares request-scoped MCP Roots with the configured `REPO_PATH` and reports advisory, redacted relationship states without changing authorization. See [MCP roots diagnostics](./docs/roots-diagnostics.md).

For MCP Sampling-capable clients, `model_assisted_summary` provides a disabled-by-default safety adapter for bounded summary/classification/workflow-selection use cases. It requires `MCP_SAMPLING_ENABLED=true`, client-declared sampling capability, repository-relative redacted context, hard path/byte/token caps, and human/client-mediated review metadata. Generated text is advisory only and cannot be the sole basis for mutation, release, or security decisions. See [MCP sampling safety adapter](./docs/sampling-safety.md).

## Sandbox profiles for autonomous agents

Before giving an autonomous coding agent mutation access, review [Sandbox Profiles for Autonomous Coding Agents](./docs/sandbox-profiles.md). It includes copy-pasteable VS Code/devcontainer and disposable container/microVM-oriented profiles, warnings for Docker socket and privileged-container escape paths, host secret handling, network egress, and rollback checks.

## Use With VS Code Dev Containers

1. Open this repository in VS Code.
2. Export a local HTTP token before opening/rebuilding the container: `export MCP_HTTP_BEARER_TOKEN="$(openssl rand -hex 32)"`.
3. Run `Dev Containers: Reopen in Container`.
4. Wait for the `codebase-tooling-mcp` container to build and start.
5. Use the MCP endpoint at `http://localhost:8000/mcp` with header `Authorization: Bearer $MCP_HTTP_BEARER_TOKEN`.

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
  "runArgs": [
    "-p", "127.0.0.1:8000:8000",
    "-p", "127.0.0.1:2345:2345"
  ],
  "containerEnv": {
    "MCP_TRANSPORT": "http",
    "MCP_HTTP_BEARER_TOKEN": "${localEnv:MCP_HTTP_BEARER_TOKEN}",
    "ALLOW_MUTATIONS": "true",
    "OLLAMA_VULKAN": "0"
  },
  "forwardPorts": [8000, 2345]
}
```

The Dockerfile uses BuildKit cache mounts for `apt`, `pip`, VSIX downloads, and
Ollama build artifacts, so repeated devcontainer rebuilds can reuse downloaded
package metadata, wheels, VSIX archives, the Ollama binary archive, and
preloaded model blobs. Download cache mounts use stable explicit IDs, including
`codebase-tooling-apt-cache` for `/var/cache/apt`,
`codebase-tooling-apt-lists` for `/var/lib/apt/lists`,
`codebase-tooling-build-downloads` for standalone build artifacts,
`codebase-tooling-pip` for `/var/cache/buildkit/pip`,
`codebase-tooling-pip-wheelhouse` for requirements-digest keyed wheelhouses, and
`codebase-tooling-vscode-vsix` for `/var/cache/buildkit/vscode-vsix`, so the
cache namespace does not depend on the exact Dockerfile instruction text.
Marketplace VS Code extensions are preloaded before repository defaults are
copied, so edits under `source/defaults/` do not invalidate the network download
layer. The build removes Debian slim's `/etc/apt/apt.conf.d/docker-clean` hook
before installing packages so downloaded `.deb` archives can remain in the cache
mount. Pip installs go through cached wheelhouses and then run with
`--no-index --find-links` so a warmed cache can be reused without contacting an
index.
Keep BuildKit enabled and use the same persistent builder/cache store when
building this image or those cache mounts will be ignored or lost between builds.
With `docker buildx` on ephemeral builders, persist the cache explicitly with
matching import/export options, for example
`--cache-to=type=local,dest=.buildx-cache,mode=max` and
`--cache-from=type=local,src=.buildx-cache`. After a warm build, use
`--build-arg MCP_BUILD_OFFLINE=true` to fail closed instead of downloading a
missing resource; use `--build-arg MCP_REFRESH_BUILD_DOWNLOAD_CACHE=true` during
an online build to refresh cached downloads intentionally. Run
`python3 scripts/build_download_cache_check.py --compact` for the local/CI static
cache-contract gate. See [Build download cache verification](./docs/build-download-cache.md).

The default image keeps `LOCAL_EMBED_BACKEND=hash` and does not install the optional
`sentence-transformers`/PyTorch stack, which is large and is not needed for the
offline hash embedding path. Build with `--build-arg INSTALL_SENTENCE_TRANSFORMERS=true`
and set `LOCAL_EMBED_BACKEND=sentence-transformers` plus `LOCAL_EMBED_MODEL` only
when that optional backend is required; for local non-Docker installs, add
`source/requirements-embedding.txt` to the normal requirements install.

### Hash-pinned dependency locks

The default Docker build still installs from the normal requirements files. For
reproducible MCP runtime builds, opt into the checked-in hash locks:

```bash
docker build \
  --build-arg MCP_USE_LOCKED_DEPS=true \
  -t codebase-tooling-mcp \
  ./source
```

Locked builds validate `source/dependency-locks.json` and install with
`pip install --require-hashes --only-binary=:all:`, failing closed when a
requirements input, lock file, wheel hash, or binary wheel is stale or missing.
Maintainers refresh/check locks with `scripts/dependency_lock.py`; see
[Hash-pinned dependency locks](./docs/dependency-locks.md)
for refresh, CI, offline wheelhouse, and optional embedding guidance.

## Docker image size and RAM monitoring

Use [`scripts/monitor_runtime_resources.py`](./scripts/monitor_runtime_resources.py) to record a repeatable local baseline for the Docker image size and startup RAM usage after `/healthz` succeeds. Verifiers can opt in to `--continuous` monitoring to sample RAM/VRAM until the container exits or a configured timeout is reached, with peak RAM and explicit VRAM availability in the output. The CI devcontainer-image workflow also uploads `docker-resource-baseline.json` for verifier comparisons. See [Docker resource monitoring](./docs/resource-monitoring.md) for commands, output fields, and offline-bootstrap constraints.

The checked-in devcontainer keeps `OLLAMA_VULKAN=0` by default because the
Vulkan backend can be unstable on some integrated GPU/driver combinations. The
image still bundles Vulkan-capable Ollama userspace, and `source/entrypoint.sh`
maps matching device groups onto the `app` user when GPU devices are passed
through. Use `setup-repository.sh --enable-vulkan-gpu` only after validating the
host GPU path.

Inside the container, the `app` user can run `sudo` without a password.

If you still want compose for local runs outside VS Code, use this inline example:

```yaml
services:
  codebase-tooling-mcp:
    image: codebase-tooling-mcp:latest
    build: ./source
    environment:
      MCP_TRANSPORT: http
      MCP_HTTP_BEARER_TOKEN: ${MCP_HTTP_BEARER_TOKEN:?set MCP_HTTP_BEARER_TOKEN before docker compose up}
      ALLOW_MUTATIONS: "true"
      REPO_PATH: /repo
      HOST: 0.0.0.0
      PORT: "8000"
    ports:
      - "127.0.0.1:8000:8000"
      - "127.0.0.1:2345:2345"
    volumes:
      - .:/repo
```

Run it with:

```bash
export MCP_HTTP_BEARER_TOKEN="$(openssl rand -hex 32)"
docker compose up --build
```

### VS Code Inline Autocomplete Extension (MCP-backed)

A minimal extension is included at [`vscode/mcp-inline-autocomplete`](./vscode/mcp-inline-autocomplete).

Run it in VS Code:

1. Open [`vscode/mcp-inline-autocomplete/package.json`](./vscode/mcp-inline-autocomplete/package.json).
2. Press `F5` (Run Extension) to start an Extension Development Host.
3. In the dev host, open Command Palette and run `MCP Inline Autocomplete: Show Status`.
4. Start typing in a file; inline suggestions come from MCP tool `autocomplete` at `http://localhost:8000/mcp`. By default, the extension sends `Authorization: Bearer $MCP_HTTP_BEARER_TOKEN` from the configured environment variable.

Key settings (in VS Code Settings):

- `mcpInlineAutocomplete.endpoint` (default `http://localhost:8000/mcp`)
- `mcpInlineAutocomplete.bearerTokenEnv` (default `MCP_HTTP_BEARER_TOKEN`; set empty only for explicit `insecure-local` tests)
- `mcpInlineAutocomplete.maxTokens`
- `mcpInlineAutocomplete.temperature`
- `mcpInlineAutocomplete.enabledLanguages`

## Bootstrap Another Repository

To add this MCP setup to another repository using the published image
`ueniueni/codebase-tooling-mcp:latest`, run:

```bash
curl -fsSL https://raw.githubusercontent.com/ueni/mcp-coding-experiment/main/setup-repository.sh | sh
```

The setup script keeps Vulkan GPU passthrough disabled by default for stable
local Agent mode. Pass `--enable-vulkan-gpu` to add `/dev/dri`, set
`OLLAMA_VULKAN=1`, and also add `/dev/kfd` when it is present on AMD hosts:

```bash
curl -fsSL https://raw.githubusercontent.com/ueni/mcp-coding-experiment/main/setup-repository.sh | sh -s -- --enable-vulkan-gpu
```

The script is standalone-safe when piped through `sh`: it embeds the default Continue profiles and falls back to those embedded defaults when `source/defaults/continue` is not present locally. It finds the repository root by locating `.git` and creates:

- `.devcontainer/devcontainer.json`
- `.continue/models/*.yaml`, `.continue/model-routing.yaml`, and `.continue/mcpServers/codebase-tooling-mcp.yaml` for the selected local or OpenAI-compatible Continue profile
- `/.codebase-tooling-mcp/`, `/.continue/`, `/.config/`, `/.devcontainer/`, `/.gitignore_codebase_tooling_mcp.touched` entries in `.gitignore` (one-time bootstrap)

By default, non-interactive setup uses the bundled local Ollama profile. On an
interactive first run, the setup script writes the selected Continue profile:
bundled local Ollama, OpenAI-compatible endpoint, or no Continue model setup.
For scripted OpenAI-compatible setup, pass `--continue-model-profile
openai-compatible` and optional environment overrides:

```bash
CONTINUE_MODEL_ID=local-proxy-model \
CONTINUE_MODEL_API_BASE=http://127.0.0.1:8787/v1 \
curl -fsSL https://raw.githubusercontent.com/ueni/mcp-coding-experiment/main/setup-repository.sh | sh -s -- --continue-model-profile openai-compatible
```

To skip repository Continue model configuration durably, pass
`--continue-model-profile none`. That path does not write `.continue` model or
MCP profile files and the generated devcontainer sets
`MCP_APPLY_CONTINUE_DEFAULTS=false`, so container startup will not recreate the
bundled Continue defaults while `MCP_APPLY_REPO_DEFAULTS=true` can still apply
non-Continue repository defaults.

When the devcontainer starts, the image applies default repository files if they
are missing and not disabled by profile selection:

- `.continue/models/*.yaml` (router + specialist model defaults, repo-owned, skipped when `MCP_APPLY_CONTINUE_DEFAULTS=false`)
- `.continue/model-routing.yaml` (routing map for router/specialists, skipped when `MCP_APPLY_CONTINUE_DEFAULTS=false`)
- `.continue/mcpServers/codebase-tooling-mcp.yaml` (skipped when `MCP_APPLY_CONTINUE_DEFAULTS=false`)
- `.config/labs/*.json`

The image also ensures a default Codex MCP client entry exists at:

- `~/.codex/config.toml`

That generated Codex entry uses the server key `codebase-tooling-mcp`:

```toml
[mcp_servers."codebase-tooling-mcp"]
url = "http://localhost:8000/mcp"
bearer_token_env_var = "MCP_HTTP_BEARER_TOKEN"
```

The checked-in Continue MCP server config sends
`Authorization: Bearer ${{ secrets.MCP_HTTP_BEARER_TOKEN }}`. The same token
must be configured in two places: as `MCP_HTTP_BEARER_TOKEN` when the MCP server
starts, and as a Continue secret named `MCP_HTTP_BEARER_TOKEN` so the IDE can
build the Authorization header. For personal IDE use, set the Continue secret in
Continue Settings > Secrets or store it in a local secret source such as
`.env`, `.continue/.env`, or `~/.continue/.env`:

```dotenv
MCP_HTTP_BEARER_TOKEN=<same token used to start the server>
```

Do not commit `.env` files or paste token values into tracked MCP config.
When the devcontainer starts in HTTP token mode with an empty
`MCP_HTTP_BEARER_TOKEN`, the entrypoint reuses one of those local secret files or
generates `.continue/.env` automatically so the server does not start in a
permanently unauthorized state.

The `.gitignore` bootstrap is intentionally one-time. A marker file
`.gitignore_codebase_tooling_mcp.touched` is created on first apply; after that,
removed generated entries are not re-added automatically.

For home-config portability, the generated devcontainer mounts host paths under
`/host` for `~/.continue` and `~/.gitconfig`, and mounts `~/.codex` directly to
`/home/app/.codex`. Startup bootstrap copies from `/host` mounts only when the
`$HOME` targets are missing or empty.

The inline autocomplete extension and the Marketplace extensions declared in the devcontainer are preloaded into one shared image directory during `docker build` and linked from the common VS Code server extension directories, so the target
repository does not need a local `vscode/mcp-inline-autocomplete/` copy and VS Code should not need to fetch those extensions again on container start.

## Endpoints (HTTP mode)

- MCP endpoint: `http://localhost:8000/mcp`
- Health endpoint: `http://localhost:8000/healthz`
- Authorization metadata: `http://localhost:8000/.well-known/oauth-protected-resource`

HTTP mode requires bearer-token authorization by default. Start with a token and pass it in the standard header:

```bash
export MCP_HTTP_BEARER_TOKEN="$(openssl rand -hex 32)"
python source/server.py

curl -H "Authorization: Bearer $MCP_HTTP_BEARER_TOKEN" http://localhost:8000/mcp
```

The default `token`/`bearer` modes are simple local bearer-token protection. Their `/.well-known/oauth-protected-resource` response is public and intentionally returns an empty `authorization_servers` list; those modes do not claim full OAuth authorization-server discovery. Protected-resource metadata and the public MCP server manifest advertise the local scope vocabulary: `mcp:read` for discovery/read-only tools, and `mcp:mutate` for HTTP tools or modes whose categories intersect mutation or sensitive categories (`write`, `git mutation`, `shell/process`, `network`, or `secret-sensitive`). Unauthorized protected MCP responses include a least-privilege `WWW-Authenticate` challenge with `scope="mcp:read"` by default.

For early deployments that need a narrower local token, set `MCP_HTTP_BEARER_TOKEN_SCOPES` to a comma- or space-separated subset such as `mcp:read`. When unset, the single configured `MCP_HTTP_BEARER_TOKEN` keeps the historical behavior and grants both `mcp:read` and `mcp:mutate`. A read-only-scoped HTTP request can call read-only tools, but mutation/sensitive tools return `insufficient_scope` with a challenge naming the required scope; bearer token values are never written to audit events.

For MCP/OAuth clients that need RFC 9728 protected resource metadata, use `oauth-resource` mode and configure at least one authorization server issuer URL:

```bash
export MCP_HTTP_AUTH_MODE=oauth-resource
export MCP_HTTP_BEARER_TOKEN="$(openssl rand -hex 32)"
export MCP_HTTP_AUTHORIZATION_SERVERS='["https://auth.example.test"]'
python source/server.py

curl -sS http://localhost:8000/.well-known/oauth-protected-resource
```

In `oauth-resource` mode, missing `MCP_HTTP_AUTHORIZATION_SERVERS` fails closed for protected MCP endpoints and is reported under `/healthz` `auth.configuration_error`. Unauthorized MCP requests include a `WWW-Authenticate: Bearer ... resource_metadata="..." scope="mcp:read"` challenge so clients can discover the protected-resource metadata document and request the least privilege needed for initial read/discovery access.

For throwaway local-only experiments, unauthenticated HTTP is still available only by explicit opt-in:

```bash
MCP_HTTP_AUTH_MODE=insecure-local HOST=127.0.0.1 python source/server.py
```

Do not use insecure-local mode with public interfaces, tunnels, shared devcontainers, or VS Code port forwarding.

## Example Claude Code registration

### HTTP server

```bash
claude mcp add --transport http codebase-tooling-mcp http://localhost:8000/mcp \
  --header "Authorization: Bearer $MCP_HTTP_BEARER_TOKEN"
```

If you intentionally started the server with `MCP_HTTP_AUTH_MODE=insecure-local` for a throwaway loopback-only test, omit the header. Do not use that mode through forwarded ports, devcontainers, SSH tunnels, or shared networks.

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
| `MCP_HTTP_AUTH_MODE` | `token` | No | `token`, `bearer`, `oauth-resource`, `insecure-local`, `disabled`, `off`, `none` | HTTP auth mode. Token/bearer modes require `Authorization: Bearer ...` and are local/simple bearer modes; `oauth-resource` also publishes RFC 9728 protected-resource metadata. Insecure modes are explicit local-only escapes. Stdio is unaffected. |
| `MCP_HTTP_BEARER_TOKEN` | empty | Required for HTTP token modes | Secret string | Bearer token accepted by HTTP MCP/SSE requests. Missing token in token mode returns 403. |
| `MCP_HTTP_BEARER_TOKEN_SCOPES` | empty | No | Comma- or space-separated subset of `mcp:read`, `mcp:mutate` | Static scopes granted to the configured local bearer token. Empty preserves existing single-token behavior by granting both scopes; set `mcp:read` for read-only HTTP access. |
| `MCP_HTTP_AUTHORIZATION_SERVERS` | empty | Required for `oauth-resource` mode | JSON string list or comma-separated issuer URLs | Authorization server issuer URLs returned as `authorization_servers` by `/.well-known/oauth-protected-resource`. Missing values in `oauth-resource` mode fail closed and appear in `/healthz` diagnostics. |
| `MCP_HTTP_RESOURCE` | `http://localhost:$PORT/mcp` | No | Absolute resource URI | Resource identifier advertised by the protected-resource metadata document. |
| `MCP_HTTP_PROTECTED_RESOURCE_METADATA_URL` | derived from `MCP_HTTP_RESOURCE` | No | Absolute URL | URL placed in 401 `WWW-Authenticate` challenges as `resource_metadata`. |
| `MCP_HTTP_RATE_LIMIT_REQUESTS` | `120` | No | Positive integer | Per-client HTTP request budget per window. Exceeded requests return 429 with `Retry-After`. |
| `MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS` | `60` | No | Positive integer seconds | Rate-limit window size. |
| `LOCAL_EMBED_BACKEND` | `hash` | No | `hash`, `auto`, `sentence-transformers` | Offline local embedding backend. The default Docker image supports `hash`; the optional `sentence-transformers` backend requires building with `INSTALL_SENTENCE_TRANSFORMERS=true` or installing `source/requirements-embedding.txt`. |
| `LOCAL_EMBED_MODEL` | empty | Required only for `sentence-transformers` | Model path/name | Sentence-transformers model reference. Keep empty for the default hash backend. |
| `LOCAL_EMBED_DIM` | `256` | No | Positive integer >= 8 | Hash embedding dimension. |
| `MCP_AGENT_EXECUTION_MODE` | `online` | No | `online`, `offline` | Default agent execution mode used by workflow selection. `online` is cloud-assisted with MCP context/audit/token savings; `offline` is onboard-only with bounded local JSON decisions and deterministic orchestration. |
| `MCP_AGENT_PROFILE` | derived from mode | No | `online-cloud-assisted`, `offline-onboard-only` | Optional profile-name alias for the execution-mode contract. |
| `MCP_HTTP_REQUEST_TIMEOUT_SECONDS` | `120` | No | Positive seconds | Non-SSE HTTP request timeout; exceeded requests return 504. |
| `MCP_AUDIT_LOG_FILE` | `.codebase-tooling-mcp/audit/security_events.jsonl` | No | Path | Append-only JSONL audit events for sensitive tool calls and denied HTTP auth attempts. Arguments are redacted/truncated. |
| `MCP_WORKFLOW_TASK_EXPIRY_HOURS` | `24` | No | Positive integer hours | Marks non-terminal async workflow task statuses as `expired` after this interval. |
| `MCP_WORKFLOW_TASK_RETENTION_DAYS` | `7` | No | Positive integer days | Retention window recorded on persisted `.codebase-tooling-mcp/tasks/` status files. |
| `MCP_APPS_DASHBOARD_ENABLED` | `false` | No | `true`, `false` | Adds the prototype read-only MCP Apps dashboard payload to `release_readiness` results when enabled. |
| `MCP_DEPENDENCY_SECURITY_BLOCKING` | `false` | No | `true`, `false` | Makes `dependency_security_report` and its `release_readiness` check block on known vulnerabilities, stale advisory data, disabled advisory lookup, scanner-unavailable status, or skipped dependency evidence. Default false keeps the first slice informational. |
| `RUNTIME_IMAGE_VERSION_COMPATIBILITY` / `RUNTIME_IMAGE_VERSION_FEATURE` / `RUNTIME_IMAGE_VERSION_BUGFIX` / `RUNTIME_IMAGE_VERSION_SUFFIX` | `0` / `0` / `0` / `-local-build` | No | Version counter/suffix strings | Independent Docker runtime image version metadata surfaced by `/healthz` as `runtime_image_version`. Build/release automation can override these Docker build args or runtime env vars. |
| `MCP_CODING_EXPERIMENT_VERSION_COMPATIBILITY` / `MCP_CODING_EXPERIMENT_VERSION_FEATURE` / `MCP_CODING_EXPERIMENT_VERSION_BUGFIX` / `MCP_CODING_EXPERIMENT_VERSION_SUFFIX` | `0` / `0` / `0` / `-local-build` | No | Version counter/suffix strings | Independent Python MCP server version metadata surfaced by `/healthz` as `mcp_coding_experiment_version`. Build/release automation can override these Docker build args or runtime env vars. |
| `HOST` | `0.0.0.0` | No | Host/IP string | Bind address for HTTP mode. Prefer `127.0.0.1` for local development. |
| `PORT` | `8000` | No | Integer port | HTTP listen port. |
| `MAX_READ_BYTES` | `262144` | No | Positive integer | Max bytes read by file tools per request. |
| `MAX_OUTPUT_CHARS` | `200000` | No | Positive integer | Output truncation limit for tool responses. |
| `CODING_DEFAULT_MODEL` | `qwen2.5-coder:1.5b` | No | Ollama model ID | Primary local coding model used by `coding_infer`, specialist task routes, and the default quality route. |
| `CODING_AGENT_MODEL` | `qwen2.5-coder:1.5b` | No | Ollama model ID | Local model route used for Continue Agent-mode requests when no custom Agent-capable profile is configured. |
| `CODING_MICRO_MODEL` | `qwen2.5-coder:1.5b` | No | Ollama model ID | Small fast-path coding model used for explicit `micro_coding` requests and short auto-routed autocomplete-like coding prompts. |
| `CODING_MICRO_MAX_PROMPT_CHARS` | `600` | No | Positive integer | Maximum normalized prompt size for automatic micro-coding selection. |
| `CONTINUE_OLLAMA_MODELS` | `qwen2.5-coder:1.5b` | No | Comma-separated model IDs (or empty) | Default steady-state Ollama model set expected to be present locally and seeded into the runtime model directory. Set to empty to declare no default bundled model set. |
| `OLLAMA_ALLOW_PULL` | `false` | No | `true`, `false` | Explicit opt-in for runtime `ollama pull` of missing models. Keep `false` for offline-only startup. |
| `OLLAMA_ENABLED` | `true` | No | `true`, `false` | Enables/disables Ollama startup in `entrypoint.sh`. |
| `OLLAMA_CONTEXT_LENGTH` | `8192` | No | Positive integer | Ollama server default context length. Also used as the local text alias `num_ctx` when `OLLAMA_TEXT_ALIAS_NUM_CTX` is unset. Increase only on hosts with enough memory for larger Agent-mode prompts. |
| `OLLAMA_TEXT_ALIAS_SOURCE_MODEL` | empty | No | Ollama model ID | Optional source model used to create a local text-only alias when `OLLAMA_TEXT_ALIAS_MODEL` is set. |
| `OLLAMA_TEXT_ALIAS_MODEL` | empty | No | Ollama model ID | Optional local text-only alias created from `OLLAMA_TEXT_ALIAS_SOURCE_MODEL`. |
| `OLLAMA_TEXT_ALIAS_NUM_CTX` | uses `OLLAMA_CONTEXT_LENGTH` | No | Positive integer | Optional per-alias `num_ctx` override written into the generated Modelfile. |
| `OLLAMA_STARTUP_TIMEOUT` | `30` | No | Integer seconds | Max wait time for Ollama readiness before fallback/failure logic. |
| `OLLAMA_HOST` | `127.0.0.1:11434` | No | `host:port` | Primary bind target for `ollama serve`. The devcontainer overrides this to `0.0.0.0:2345` so the bundled Ollama service is reachable from the host on port `2345`. |
| `OLLAMA_FALLBACK_HOST` | `0.0.0.0:11434` | No | `host:port` | Secondary bind target used if primary Ollama host fails. The devcontainer keeps this aligned to `0.0.0.0:2345`. |
| `ALLOW_ORIGINS` | `*` | No | CORS origin list | Controls browser/client origins for HTTP mode. |
| `SSL_CERT_FILE` | `/etc/ssl/certs/ca-certificates.crt` | No | Path | CA bundle for outbound HTTPS. |
| `HOST_CA_CERT_FILE` | empty | No | Path | Optional mounted host CA bundle path. |

## Continue + Ollama Contract

- The checked-in default Continue Ollama config uses `provider: ollama` with `apiBase: http://127.0.0.1:2345`. The repo also ships `.continue/models/coding-openai-compatible.yaml` as an OpenAI-compatible endpoint template for local proxies, hosted endpoints, or MITM inspection setups, plus `.continue/models/model-fallback.yaml` pointing at `http://localhost:8000/v1/model-fallback` for configuration assistance when no real model is configured yet.
- The devcontainer publishes `127.0.0.1:2345:2345` so Continue can reach the bundled Ollama service even when its extension host runs outside the container. If Continue reports `ECONNREFUSED 127.0.0.1:2345`, rebuild/reopen the devcontainer and confirm `curl http://127.0.0.1:2345/api/tags` works from the same side where Continue is running.
- This repo treats the native Ollama base as the contract for Continue's Ollama provider. Do not append `/v1` when configuring those model YAMLs.
- `source/Dockerfile` installs Vulkan userspace (`libvulkan1`, `mesa-vulkan-drivers`, `vulkan-tools`), but the checked-in devcontainer keeps `OLLAMA_VULKAN=0` for stability. `source/entrypoint.sh` maps `/dev/dri` device groups onto `app` so Ollama can use Vulkan-capable Linux GPUs only when `/dev/dri` is explicitly passed through and `OLLAMA_VULKAN=1` is set.
- The steady-state local development route is `qwen2.5-coder:1.5b` for `coding_infer`, Continue routing, and specialist task routes. The checked-in VS Code devcontainer preloads that model with `OLLAMA_PRELOAD_MODELS=qwen2.5-coder:1.5b`, so fresh containers have the Continue model available without runtime pulls.
- Agent-mode tool calling may require a custom local model profile with verified tool support. The repository no longer ships a separate bundled Agent model profile.
- Hardware note: the repository default context is `8192` to reduce runner pressure on laptop-class hosts. Raise the context to `16384` or `32768` only after confirming enough memory for the selected local model and client workload.
- Existing host or repository `.continue` config may stay stale until refreshed. After pulling this change, rebuild/reopen the devcontainer and either keep `MCP_APPLY_REPO_DEFAULTS=true` with `MCP_APPLY_CONTINUE_DEFAULTS=true` or rerun `setup-repository.sh` / manually copy the `source/defaults/continue` files so the host-visible `.continue` config includes the compact default model profile and routing. On an interactive first run, `setup-repository.sh` asks whether Continue should adopt the bundled local Ollama profile, configure an OpenAI-compatible endpoint, or skip Continue model setup. For scripted setup, use `--continue-model-profile openai-compatible` plus `CONTINUE_MODEL_ID`, `CONTINUE_MODEL_API_BASE`, and optional `CONTINUE_MODEL_PROXY` / `CONTINUE_MODEL_CA_BUNDLE`; use `--continue-model-profile none` when the repository should not receive `.continue` model/MCP profile files, and the generated devcontainer keeps that choice durable with `MCP_APPLY_CONTINUE_DEFAULTS=false`. The fallback endpoint can also write `.continue/models/coding-openai-compatible.yaml` and `.continue/model-routing.yaml` through `POST /v1/model-fallback/configure` when `ALLOW_MUTATIONS=true` and the HTTP bearer token has `mcp:mutate`; when mutations are disabled, it returns a dry-run config payload to mutate-scoped callers.
- `source/Dockerfile` preloads the model set declared by `OLLAMA_PRELOAD_MODELS` into the image; the default is `qwen2.5-coder:1.5b`. The build-time preload step stores models in the stable BuildKit cache mount `id=codebase-tooling-ollama-models,target=/var/cache/buildkit/ollama-models` and runs `ollama show` before `ollama pull`, so a persistent builder can skip already-cached models on later builds. On throwaway/remote builders, use `docker buildx` cache import/export (`--cache-to=...` and `--cache-from=...`) or the preload cache will still be empty on the next build. CI validation jobs may override the build arg to empty when they need a no-model image path.
- Runtime `ollama pull` is disabled by default. Missing models are only downloaded when `OLLAMA_ALLOW_PULL=true` is explicitly set.
- `task_router(mode="task")` and `task_router(mode="coding_infer")` accept `task="micro_coding"` to force the compact coder, and short coding prompts can auto-select it when no explicit model override is provided.
- Endpoint inference strips common chat sentinel and reasoning marker tokens before returning tool output.
- Setting `CONTINUE_OLLAMA_MODELS` to an empty value declares that no default bundled model set is required. In that mode, Continue may report `model not found` until models are installed manually or `OLLAMA_ALLOW_PULL=true` is used.
- A `404` on `http://127.0.0.1:2345/v1/` does not invalidate the native Ollama integration in this repo; the native base and `/api/tags` are the relevant health checks.

## Safety and Mutation Controls

- Path traversal outside the mounted repository is blocked.
- Read-only usage is the safest default: keep `ALLOW_MUTATIONS=false` unless changes are required.
- HTTP mode requires authorization by default (`MCP_HTTP_AUTH_MODE=token` plus `MCP_HTTP_BEARER_TOKEN`). Stdio-only use is not affected.
- Mutating categories (`write`, `git mutation`) require both `ALLOW_MUTATIONS=true` and an authorized HTTP session with `mcp:mutate` scope when called over HTTP.
- Sensitive categories (`shell/process`, `network`, `secret-sensitive`) require an authorized HTTP session and are audited.
- `task_router` carries per-mode security categories: read/status modes are read-only; inference/autocomplete modes include `network`; coding check/package/sandbox modes include `shell/process`, and package/sandbox/coding-infer modes include `write` where applicable.
- Git commits still require Git user identity in repo config or environment.
- In stdio mode, avoid writing logs to stdout to preserve protocol framing.

### Inline Python Convenience

- Internal execution helpers can allow narrowly scoped inline Python via `python -c ...` or `python3 -c ...`.
- The allowlisted path is intended for small transforms and calculations, such as `python3 -c "import json; print(json.dumps({'ok': True}, sort_keys=True))"`.
- Inline Python remains constrained: only `-c` form is allowlisted, code length is capped, and imports/names/attributes tied to filesystem, process, or network access are blocked.
- Examples that remain outside the allowlist and fall back to manual approval include `python script.py`, `python3 -c "import os; print(os.getcwd())"`, and code that calls `open(...)`, `subprocess.run(...)`, or path write helpers.

## Tool Catalog by Category

### Public MCP v1 Surface

- `task_router`
- `tool_annotations`
- `tool_output_contracts`
- `tool_catalog_integrity` for checked-in public tool-catalog digest drift checks
- `scripts/tool_contract_fuzzer.py` for deterministic read-only behavioral fuzzing of public tool contracts
- `policy_insights` for read-only maintainer policy/tool-gate regression summaries
- `workflow_task` and `task_status` for the prototype persisted async task wrapper
- Schema-backed core tools: `repo_info`, `roots_diagnostics`, `model_assisted_summary`, `runtime_state`, `git_status`, `grep`, `find_paths`, `read_snippet`, `summarize_diff`, `risk_scoring`, `workspace_transaction`, `policy_simulator`, `release_readiness`, `tool_catalog_integrity`, `dependency_security_report`, `governance_report`, `self_optimization_report`, `artifact_provenance`, `workflow_diagnostics`, `workflow_lineage`, `interaction_invariant_audit`

`task_router()` remains the default public entrypoint and now defaults to `mode="task"`. It classifies the request, encodes the routing packet, reads and writes compact task/session memory automatically, and dispatches to the selected specialist flow. Use `mode="workflow_select"` plus `execution_mode="online" | "offline" | "auto"` as a read-only preflight when an agent is unsure which workflow/prompt/tool to choose. Use `memory_session` when you want related requests to share that compact context or to isolate a separate task thread.

`workflow_task()` starts the prototype async wrapper for long-running workflows. Initial supported workflows are `governance_report` and `vscode_task_run`; status is persisted under `.codebase-tooling-mcp/tasks/`, can be read with `task_status()`, and returns repository-relative artifact resource links. VS Code task logs are kept in redacted result artifacts referenced by the compact task status. See [Workflow task prototype](./docs/workflow-tasks.md).

`workflow_lineage()` is a read-only verifier for deterministic lineage manifests emitted by `governance_report(export=true)`. It recomputes the redacted plan inputs for the recorded governance-report execution and compares artifact digests without duplicating tracing/attestation backends. See [Workflow lineage manifests](./docs/workflow-lineage.md).

`self_optimization_report()` is the direct software-team efficiency report for this repository. Run `self_optimization_report(window_hours=168, export=true)` after issue/PR batches or noisy MCP runs to summarize local usage, savings, task/state/test-gate/retry/blocked-time metrics, throughput, bottlenecks, confidence/caveats, and duplicate-suppressed optimization candidates without exposing raw traces or secrets. GitHub issue create/update remains off unless explicitly gated with high-confidence candidates. See [Self-optimization efficiency report](./docs/self-optimization-report.md). The offline E2E MCP workflow benchmark runner writes a sibling summary report that can feed the same optimization loop; see [E2E MCP workflow benchmarks](./docs/e2e-mcp-workflow-benchmarks.md).

`roots_diagnostics()` is a read-only advisory setup diagnostic that feature-detects MCP client roots support and compares available `file://` roots with `REPO_PATH`. It returns redacted relationship metadata (`exact_match`, overlaps, multiple roots, no overlap, unsupported, unavailable, or error) without exposing absolute client paths outside the repository and without changing `_resolve_repo_path` enforcement. See [MCP roots diagnostics](./docs/roots-diagnostics.md).

`model_assisted_summary()` is a disabled-by-default MCP Sampling adapter for bounded advisory summaries/classifications. It returns explicit `disabled`/`unsupported`/`denied` statuses unless server config, client-declared sampling capability, allowed purpose, and redacted budgeted context all permit a client-mediated request.

`tool_annotations()` returns machine-checkable read-only/destructive/idempotent/open-world hints for the public tools and covered public modes such as `task_router`, `test_impact_map(refresh=true)`, `workflow_task(start)`, and `workspace_transaction`. The schema-backed core tools publish checked-in output contracts for clients that validate `structuredContent`; `tool_output_contracts()` returns those contracts and the shared error envelope. `tool_catalog_integrity()` hashes the public `mcp.list_tools()` metadata plus annotations, output contracts, categories, and documentation references against `source/tool_catalog_baseline.json`; see [Tool catalog integrity baseline](./docs/tool-catalog-integrity.md). `scripts/tool_contract_fuzzer.py` complements that static baseline with dynamic, deterministic read-only calls that validate runtime outputs, error envelopes, and redaction invariants; see [MCP tool contract behavioral fuzzing](./docs/tool-contract-fuzzing.md). Leaf implementations remain in `source/server.py` as direct call targets for router orchestration and for internal tests.

`policy_insights()` reports the versioned maintainer-owned policy insight bank from `source/policy_insights.json` without exposing raw trigger arguments or secret-like fixture values. The bank captures deterministic tool/router gate regressions for mutation denial, release/readiness read-only behavior, and sensitive-output redaction; see [Policy insight regression bank](./docs/policy-insights.md).

## Labs and Reports

Prototype automations for advanced workflows live under `source/labs`.
See [MCP Fun Labs](./docs/labs.md) for command examples and expected outputs.

## Documentation

- [Documentation Index](./docs/index.md)
- [Workflow task prototype](./docs/workflow-tasks.md)
- [Agent execution modes](./docs/execution-modes.md)
- [Tooling White Paper](./docs/tooling-whitepaper.md)
- [JSON Settings Files](./docs/json-settings.md)
- [MCP Fun Labs](./docs/labs.md)
- [Troubleshooting](./docs/troubleshooting.md)
- [Release Notes and Documentation Policy](./docs/release-notes-policy.md)
- [MCP Output Schemas](./docs/mcp-output-schemas.md)
- [Policy insight regression bank](./docs/policy-insights.md)
- [Self-optimization efficiency report](./docs/self-optimization-report.md)
- [E2E MCP workflow benchmarks](./docs/e2e-mcp-workflow-benchmarks.md)
