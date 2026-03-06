# mcp-git-server

Minimal MCP server for one mounted Git repository.

## Build

```bash
docker build -t mcp-git-server .
```

## Run

```bash
docker run --rm \
  -p 8000:8000 \
  -e ALLOW_MUTATIONS=true \
  -v "$PWD:/repo" \
  mcp-git-server
```

## MCP endpoint

```text
http://localhost:8000/mcp
```

## Health endpoint

```text
http://localhost:8000/healthz
```

## Example Claude Code registration

```bash
claude mcp add --transport http repo-git http://localhost:8000/mcp
```

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
