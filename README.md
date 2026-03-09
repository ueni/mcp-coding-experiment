# mcp-git-server

Minimal MCP server for one mounted Git repository.

## Use With VS Code Dev Containers

1. Open this repository in VS Code.
2. Run `Dev Containers: Reopen in Container`.
3. Wait for the `mcp-git-server` service to start.
4. Use the MCP endpoint at `http://localhost:8000/mcp`.

The VS Code entry point stays in [`.devcontainer/devcontainer.json`](/home/user/source/mcp-server-git-local-files/.devcontainer/devcontainer.json), while the underlying container implementation lives in [`toolchain/dev/docker-compose.yml`](/home/user/source/mcp-server-git-local-files/toolchain/dev/docker-compose.yml). The repository is mounted at `/repo` and port `8000` is forwarded automatically.

## Build

```bash
docker build -t mcp-git-server ./toolchain/dev
```

## Run over Streamable HTTP

```bash
docker run --rm \
  -p 8000:8000 \
  -e MCP_TRANSPORT=http \
  -e ALLOW_MUTATIONS=true \
  -v "$PWD:/repo" \
  mcp-git-server
```

## Run over stdio

```bash
docker run --rm -i \
  -e MCP_TRANSPORT=stdio \
  -e ALLOW_MUTATIONS=true \
  -v "$PWD:/repo" \
  mcp-git-server
```

## MCP endpoint (HTTP mode)

```text
http://localhost:8000/mcp
```

## Health endpoint (HTTP mode)

```text
http://localhost:8000/healthz
```

## Example Claude Code registration

### HTTP server

```bash
claude mcp add --transport http repo-git http://localhost:8000/mcp
```

### Local stdio server via Docker

```json
{
  "mcpServers": {
    "repo-git": {
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
        "mcp-git-server"
      ]
    }
  }
}
```

## Environment variables

- `MCP_TRANSPORT=http|stdio`
- `REPO_PATH=/repo`
- `ALLOW_MUTATIONS=true|false`
- `HOST=0.0.0.0`
- `PORT=8000`
- `MAX_READ_BYTES=262144`
- `MAX_OUTPUT_CHARS=200000`
- `ALLOW_ORIGINS=*`

## Tools

- repo_info
- git_init
- list_files
- read_file
- write_file
- delete_path
- move_path
- git_status
- git_diff
- git_log
- git_show
- git_add
- git_restore
- git_commit
- git_checkout
- git_create_branch
- git_fetch
- git_pull
- git_push
- grep
- find_paths
- replace_in_files
- read_snippet
- read_batch
- semantic_find
- tree_sitter_core
- repo_index_daemon
- symbol_index
- dependency_map
- call_graph
- ast_search
- apply_unified_diff
- command_runner
- impact_tests
- summarize_diff
- risk_scoring
- json_query
- prompt_optimize
- math_parser
- math_solver
- math_verify
- sql_expert
- security_triage
- doc_summarizer_small
- code_review_classifier
- test_gen_small
- vision_ocr_parser
- translation_small
- diagram_from_code
- mermaid_lint_fix
- drawio_generator
- diagram_sync_check
- local_model_status
- local_embed
- local_infer
- local_rerank
- token_budget_guard
- cache_control
- result_handle
- tool_benchmark
- self_test
- output_size_guard
- memory_upsert
- memory_get
- memory_validate
- failure_memory
- workspace_facts
- doc_sync_check
- api_surface_snapshot
- self_check_pipeline
- edit_transaction
- lab_release_rehearsal
- lab_refactor_tournament
- lab_policy_gatekeeper
- lab_branch_swarm
- lab_narrated_pr
- lab_repo_digital_twin

## Fun Labs

Prototype automations for advanced workflows live under `toolchain/dev/labs`:

- `release_rehearsal.py`: dry-run a release, run checks, synthesize changelog, and emit `.build/reports/RELEASE_REHEARSAL.md`.
- `refactor_tournament.py`: evaluate multiple refactor strategies on isolated branches and emit `.build/reports/REFACTOR_TOURNAMENT.md`.
- `policy_gatekeeper.py`: enforce policy-as-code checks and emit `.build/reports/POLICY_GATEKEEPER.md`.
- `branch_swarm_lab.py`: run strategy swarms with benchmark scoring and emit `.build/reports/BRANCH_SWARM_REPORT.md`.
- `narrated_pr_generator.py`: generate a narrated PR packet and reviewer checklist in `.build/reports/PR_PACKET.md`.
- `repo_digital_twin.py`: generate `.build/reports/REPO_DIGITAL_TWIN.json` and `.build/reports/REPO_DIGITAL_TWIN.md` snapshots.

Start here: `toolchain/dev/labs/README.md`

## Notes

- All paths are repository-relative.
- Path traversal outside the mounted repo is blocked.
- Mutating operations require `ALLOW_MUTATIONS=true`.
- Tool outputs default to compact responses; adjust with `token_budget_guard` and per-tool `output_profile`.
- Prefer prompt/tool usage with `fields`, `offset`, `limit`, `summary_mode="quick"`, and `store_result=true` for large outputs.
- Offline local-model routing is configurable via `LOCAL_INFER_*` and `LOCAL_EMBED_*` environment variables.
- OCR support uses `tesseract-ocr` plus Python `Pillow`/`pytesseract`.
- `git commit` still needs Git user identity to be configured in the repo or via environment.
- In stdio mode, do not write logs to stdout.
