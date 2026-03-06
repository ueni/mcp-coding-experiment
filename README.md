# mcp-git-server

Minimal MCP server for one mounted Git repository.

## Build

```bash
docker build -t mcp-git-server .
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

## Notes

- All paths are repository-relative.
- Path traversal outside the mounted repo is blocked.
- Mutating operations require `ALLOW_MUTATIONS=true`.
- `git commit` still needs Git user identity to be configured in the repo or via environment.
- In stdio mode, do not write logs to stdout.
