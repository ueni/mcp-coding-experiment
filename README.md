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
3. Wait for the `codebase-tooling-mcp` service to start.
4. Use the MCP endpoint at `http://localhost:8000/mcp`.

The VS Code entry point is [`.devcontainer/devcontainer.json`](./.devcontainer/devcontainer.json), while the underlying container implementation is [`source/docker-compose.yml`](./source/docker-compose.yml). The repository is mounted at `/repo` and port `8000` is forwarded automatically.

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

- `.continue/mcpServers/codebase-tooling-mcp.yaml`
- `.config/labs/*.json`
- `/.build/` entry in `.gitignore`

The image also ensures a default Codex MCP client entry exists at:

- `~/.codex/config.toml`

The inline autocomplete extension is bundled into the image, so the target
repository does not need a local `vscode/mcp-inline-autocomplete/` copy.

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
| `MCP_TRANSPORT` | `http` | No | `http`, `stdio` | Selects server transport mode. |
| `REPO_PATH` | `/repo` | No | Absolute path | Root path tools may operate on. |
| `ALLOW_MUTATIONS` | `false` (recommended default) | No | `true`, `false` | Enables/disables write and git-mutating operations. |
| `HOST` | `0.0.0.0` | No | Host/IP string | Bind address for HTTP mode. |
| `PORT` | `8000` | No | Integer port | HTTP listen port. |
| `MAX_READ_BYTES` | `262144` | No | Positive integer | Max bytes read by file tools per request. |
| `MAX_OUTPUT_CHARS` | `200000` | No | Positive integer | Output truncation limit for tool responses. |
| `ALLOW_ORIGINS` | `*` | No | CORS origin list | Controls browser/client origins for HTTP mode. |
| `SSL_CERT_FILE` | `/etc/ssl/certs/ca-certificates.crt` | No | Path | CA bundle for outbound HTTPS. |
| `HOST_CA_CERT_FILE` | empty | No | Path | Optional mounted host CA bundle path. |

## Safety and Mutation Controls

- Path traversal outside the mounted repository is blocked.
- Read-only usage is the safest default: keep `ALLOW_MUTATIONS=false` unless changes are required.
- Mutating operations (for example `write_file`, `delete_path`, `move_path`, Git writes) require `ALLOW_MUTATIONS=true`.
- Git commits still require Git user identity in repo config or environment.
- In stdio mode, avoid writing logs to stdout to preserve protocol framing.

## Tool Catalog by Category

### Repository and File I/O

- `repo_info`
- `list_files`
- `read_file`
- `read_document`
- `read_snippet`
- `read_batch`
- `write_file`
- `delete_path`
- `move_path`
- `find_paths`
- `replace_in_files`
- `json_query`

### Git and Change Management

- `git_init`
- `git_status`
- `git_diff`
- `git_log`
- `git_show`
- `git_add`
- `git_restore`
- `git_commit`
- `git_checkout`
- `git_create_branch`
- `git_fetch`
- `git_pull`
- `git_push`
- `apply_unified_diff`
- `edit_transaction`
- `summarize_diff`
- `risk_scoring`
- `security_triage`

### Search, Indexing, and Structure

- `grep`
- `semantic_find`
- `tree_sitter_core`
- `repo_index_daemon`
- `symbol_index`
- `dependency_map`
- `call_graph`
- `ast_search`
- `impact_tests`
- `doc_sync_check`
- `api_surface_snapshot`

### Analysis and Productivity

- `command_runner`
- `terminal_support_session`
- `prompt_optimize`
- `doc_summarizer_small`
- `code_review_classifier`
- `test_gen_small`
- `self_test`
- `self_check_pipeline`
- `output_size_guard`
- `token_budget_guard`
- `cache_control`
- `result_handle`
- `tool_benchmark`
- `workspace_facts`
- `failure_memory`
- `memory_upsert`
- `memory_get`
- `memory_validate`
- `license_monitor`
- `install_git_hooks`
- `commit_lint_tag`
- `golden_output_guard`
- `flaky_test_detector`
- `change_impact_gate`
- `smart_fix_batch`
- `release_readiness`
- `required_tool_chain`
- `fast_path_dev`
- `workflow_compiler`
- `state_snapshot`
- `state_restore`
- `policy_simulator`
- `tool_router_learned`
- `artifact_memory_index`
- `constraint_solver_for_tasks`
- `spec_to_tests`
- `auto_sharding_for_analysis`
- `confidence_scoring`
- `runtime_contract_checker`
- `cost_budget_enforcer`
- `multi_agent_lane`
- `human_approval_points`
- `root_cause_memory`
- `execution_replay`
- `encode_lossless`
- `decode_lossless`
- `roundtrip_verify`
- `delta_encode`
- `delta_apply`

### Math, Data, and Content

- `math_parser`
- `math_solver`
- `math_verify`
- `sql_expert`
- `vision_ocr_parser`
- `image_interpret`
- `translation_small`
- `interpret_presentation`
- `browse_web`

### Diagramming and Architecture Docs

- `diagram_from_code`
- `mermaid_lint_fix`
- `drawio_generator`
- `diagram_sync_check`

### Local Model and Retrieval

- `local_model_status`
- `local_embed`
- `local_infer`
- `autocomplete`
- `local_rerank`

### Labs

- `lab_release_rehearsal`
- `lab_refactor_tournament`
- `lab_policy_gatekeeper`
- `lab_branch_swarm`
- `lab_narrated_pr`
- `lab_repo_digital_twin`

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
