<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Sandbox Profiles for Autonomous Coding Agents

This MCP server gives coding agents file, search, analysis, and optional mutation tools for one mounted repository. Treat any autonomous agent using it as untrusted code with repository write access: give it the smallest workspace, credentials, network, and rollback surface that can complete the task.

## Baseline rules

- Mount exactly one throwaway working copy at `/repo`; do not mount `$HOME`, `/`, `/var`, cloud sync folders, SSH agent sockets, password stores, browser profiles, or host package caches.
- Keep `ALLOW_MUTATIONS=false` for reconnaissance. Enable mutations only on a task branch or disposable clone.
- Prefer short-lived bearer tokens: generate `MCP_HTTP_BEARER_TOKEN` per run and unset it when the run ends.
- Keep host secrets outside the sandbox. If a task needs a token, inject a narrow, revocable token as an environment variable for that run only.
- Start with network egress disabled or allowlisted. Enable internet access only for tasks that need package downloads, API calls, or external docs.
- Have a rollback point before mutations: a clean Git worktree, branch, snapshot, VM checkpoint, or disposable clone you can delete.

## Profile 1: VS Code Dev Container, repository-only mount

Use this when the agent runs from VS Code or another MCP client on the host but the MCP server and tools run inside a devcontainer.

Copy this into `.devcontainer/devcontainer.sandbox.json` or adapt it into your existing `.devcontainer/devcontainer.json`:

```json
{
  "name": "codebase-tooling-mcp-sandbox",
  "build": {
    "context": "../source",
    "dockerfile": "../source/Dockerfile"
  },
  "overrideCommand": false,
  "remoteUser": "app",
  "containerUser": "app",
  "workspaceFolder": "/repo",
  "containerEnv": {
    "MCP_TRANSPORT": "http",
    "MCP_HTTP_BEARER_TOKEN": "${localEnv:MCP_HTTP_BEARER_TOKEN}",
    "ALLOW_MUTATIONS": "false",
    "REPO_PATH": "/repo",
    "HOST": "0.0.0.0",
    "PORT": "8000"
  },
  "forwardPorts": [8000],
  "mounts": [
    "source=${localWorkspaceFolder},target=/repo,type=bind,consistency=cached"
  ],
  "runArgs": [
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges:true",
    "--pids-limit=512",
    "--memory=4g",
    "--cpus=4"
  ],
  "customizations": {
    "vscode": {
      "settings": {
        "mcpInlineAutocomplete.endpoint": "http://localhost:8000/mcp"
      }
    }
  }
}
```

Start read-only reconnaissance:

```bash
export MCP_HTTP_BEARER_TOKEN="$(openssl rand -hex 32)"
code --folder-uri "vscode-remote://dev-container+$(pwd)"
```

When you are ready to let the agent edit files, rebuild with `ALLOW_MUTATIONS=true` on a disposable branch or clone:

```bash
git switch -c agent/task-123
# Set ALLOW_MUTATIONS to "true" in the sandbox profile, rebuild, then verify diffs before committing.
```

Do **not** add these shortcuts to this profile unless you intentionally accept the risk:

- `source=/var/run/docker.sock,target=/var/run/docker.sock,type=bind`: a Docker socket mount lets the container start privileged sibling containers and can become host-root equivalent.
- `"--privileged"`, `"--cap-add=SYS_ADMIN"`, or unconfined seccomp/AppArmor: privileged containers can bypass much of the sandbox boundary.
- `source=${localEnv:HOME},target=/home/app/host-home,type=bind`: broad home-directory mounts expose SSH keys, cloud credentials, browser sessions, package tokens, and private documents.
- Long-lived host credential mounts such as `.ssh`, `.aws`, `.config/gh`, `.docker`, `.kube`, password stores, or agent sockets.

## Profile 2: disposable container workspace with controlled egress

Use this when you want a clean copy of a repository and a container lifecycle you can throw away after one autonomous run.

Create a disposable working copy first:

```bash
export WORKDIR="$(mktemp -d -t mcp-agent-repo.XXXXXX)"
git clone --depth=1 "https://github.com/OWNER/REPO.git" "$WORKDIR/repo"
cd "$WORKDIR/repo"
git switch -c agent/sandbox-run
export MCP_HTTP_BEARER_TOKEN="$(openssl rand -hex 32)"
```

Run without network egress after the image is already available locally:

```bash
docker run --rm --name codebase-tooling-mcp-sandbox \
  --network=none \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  --pids-limit=512 \
  --memory=4g \
  --cpus=4 \
  -e MCP_TRANSPORT=http \
  -e MCP_HTTP_BEARER_TOKEN="$MCP_HTTP_BEARER_TOKEN" \
  -e ALLOW_MUTATIONS=false \
  -e REPO_PATH=/repo \
  -v "$PWD:/repo" \
  codebase-tooling-mcp:latest
```

If the agent must install packages or fetch docs, use an explicit proxy or allowlisted bridge network instead of general internet access:

```bash
docker network create --internal mcp-agent-internal
# Attach a controlled proxy/cache container to mcp-agent-internal, then run the MCP server on that network.
docker run --rm --name codebase-tooling-mcp-sandbox \
  --network=mcp-agent-internal \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  --pids-limit=512 \
  --memory=4g \
  --cpus=4 \
  -e HTTPS_PROXY=http://proxy:3128 \
  -e HTTP_PROXY=http://proxy:3128 \
  -e NO_PROXY=localhost,127.0.0.1 \
  -e MCP_TRANSPORT=http \
  -e MCP_HTTP_BEARER_TOKEN="$MCP_HTTP_BEARER_TOKEN" \
  -e ALLOW_MUTATIONS=true \
  -e REPO_PATH=/repo \
  -v "$PWD:/repo" \
  codebase-tooling-mcp:latest
```

Rollback is intentionally simple: inspect the diff, keep the branch if useful, or delete the disposable `$WORKDIR` after preserving patches you want.

## MicroVM-oriented variant

For stronger isolation, run the same container image inside a microVM runtime such as Kata Containers, Firecracker-based tooling, or a CI runner that provides per-job VMs. Keep the same policy shape:

```bash
export WORKDIR="$(mktemp -d -t mcp-agent-repo.XXXXXX)"
git clone "https://github.com/OWNER/REPO.git" "$WORKDIR/repo"
cd "$WORKDIR/repo"
git switch -c agent/microvm-run
export MCP_HTTP_BEARER_TOKEN="$(openssl rand -hex 32)"

# Example shape for Docker configured with a Kata runtime.
docker run --rm --runtime=kata-runtime \
  --network=none \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  -e MCP_TRANSPORT=http \
  -e MCP_HTTP_BEARER_TOKEN="$MCP_HTTP_BEARER_TOKEN" \
  -e ALLOW_MUTATIONS=true \
  -e REPO_PATH=/repo \
  -v "$PWD:/repo" \
  codebase-tooling-mcp:latest
```

Take a VM snapshot or use a disposable VM root disk before enabling mutations. After the run, export only `git diff`, commits, or an archive of `/repo`; do not copy VM home directories or tool caches back to the host.

## Validation checklist

Before giving an autonomous agent mutation access, run this lightweight check:

```bash
# 1. Confirm the working copy is disposable and recoverable.
git status --short
git branch --show-current

# 2. Confirm the MCP server sees only the intended repository.
curl -sS -H "Authorization: Bearer $MCP_HTTP_BEARER_TOKEN" \
  http://localhost:8000/healthz

# 3. Confirm risky host mounts are absent.
docker inspect codebase-tooling-mcp-sandbox \
  --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'

# 4. Confirm privilege and network posture.
docker inspect codebase-tooling-mcp-sandbox \
  --format 'privileged={{.HostConfig.Privileged}} network={{.HostConfig.NetworkMode}} capAdd={{.HostConfig.CapAdd}} capDrop={{.HostConfig.CapDrop}}'
```

Expected results:

- The branch is task-specific and can be reset or deleted.
- `/healthz` reports `repo_path` as `/repo` and `allow_mutations` matches the current phase.
- Mounts show the repository only; no Docker socket, broad home mount, SSH/GitHub/cloud credentials, or host secret stores.
- The container is not privileged, has no added capabilities, and uses disabled or controlled network egress.
- A rollback path exists before edits: Git reset, branch deletion, disposable clone removal, container teardown, VM snapshot restore, or CI job discard.

## Risk summary

Autonomous coding agents can execute tool-driven changes quickly. The highest-impact mistakes are usually environmental, not code-level: exposing the Docker socket, running privileged containers, mounting a full home directory, leaking host secrets, allowing unrestricted network egress, or starting without a rollback/snapshot plan. Make those choices explicit in the profile and verify them before each run.
