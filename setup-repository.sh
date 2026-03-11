#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

set -eu

IMAGE_REF="ueniueni/codebase-tooling-mcp:latest"

log() {
  printf '%s\n' "$*" >&2
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "Missing required command: $1"
    exit 1
  fi
}

find_repo_root() {
  current_dir=$(pwd)
  while [ "$current_dir" != "/" ]; do
    if [ -e "$current_dir/.git" ]; then
      printf '%s\n' "$current_dir"
      return 0
    fi
    current_dir=$(dirname "$current_dir")
  done
  return 1
}

fail_if_exists() {
  target=$1
  if [ -e "$target" ]; then
    log "Refusing to overwrite existing path: $target"
    exit 1
  fi
}

require_cmd mkdir
require_cmd cp
require_cmd grep

REPO_ROOT=$(find_repo_root || true)
if [ -z "$REPO_ROOT" ]; then
  log "This script must run inside a Git repository."
  exit 1
fi

cd "$REPO_ROOT"

REPO_NAME=$(basename "$REPO_ROOT")

ensure_gitignore_entry() {
  entry=$1
  if [ ! -f .gitignore ]; then
    : > .gitignore
  fi
  if ! grep -qxF "$entry" .gitignore; then
    printf '%s\n' "$entry" >> .gitignore
  fi
}

log "Bootstrapping codebase-tooling-mcp devcontainer into $REPO_ROOT"

mkdir -p .devcontainer

fail_if_exists .devcontainer/devcontainer.json
cat > .devcontainer/devcontainer.json <<EOF
{
  "name": "${REPO_NAME}",
  "image": "${IMAGE_REF}",
  "overrideCommand": false,
  "remoteUser": "app",
  "containerUser": "root",
  "workspaceFolder": "/repo",
  "containerEnv": {
    "DOCKER_HOST": "unix:///var/run/docker.sock",
    "MCP_APPLY_REPO_DEFAULTS": "true",
    "MCP_TRANSPORT": "http",
    "ALLOW_MUTATIONS": "true",
  },
  "forwardPorts": [
    8000
  ],
  "portsAttributes": {
    "8000": {
      "label": "MCP Server",
      "onAutoForward": "notify"
    }
  },
  "customizations": {
    "vscode": {
      "extensions": [
        "Continue.continue",
        "ms-python.python",
        "ms-python.vscode-pylance",
        "ms-azuretools.vscode-docker",
        "openai.chatgpt",
        "mhutchie.git-graph"
      ],
      "settings": {
        "mcpInlineAutocomplete.endpoint": "http://localhost:8000/mcp"
      }
    }
  },
  "mounts": [
    "source=\${localEnv:HOME}/.codex,target=/host/.codex,type=bind,consistency=cached",readOnly=true,
    "source=\${localEnv:HOME}/.continue,target=/host/.continue,type=bind,consistency=cached,readOnly=true",
    "source=\${localEnv:HOME}/.gitconfig,target=/host/.gitconfig,type=bind,consistency=cached,readOnly=true",
    "source=/var/run/docker.sock,target=/var/run/docker.sock,type=bind",
    "source=\${localWorkspaceFolder},target=/repo,type=bind,consistency=cached",
    "source=/etc/ssl/certs,target=/etc/ssl/certs,type=bind,consistency=cached,readOnly=true"
  ]
}
EOF

if [ ! -f .gitignore ]; then
  : > .gitignore
fi
if ! grep -qxF '# codebase-tooling-mcp generated' .gitignore; then
  printf '\n# codebase-tooling-mcp generated\n' >> .gitignore
fi
ensure_gitignore_entry '/.build/'
ensure_gitignore_entry '/.continue/'
ensure_gitignore_entry '/.config/'
ensure_gitignore_entry '/.devcontainer/'
ensure_gitignore_entry '/.gitignore_codebase_tooling_mcp.touched'
: > .gitignore_codebase_tooling_mcp.touched

log "Created:"
log "  .devcontainer/devcontainer.json"
log "  .gitignore entries for generated hidden folders (one-time bootstrap)"
log ""
log "The container image provides the inline autocomplete extension and repo defaults."
log "Next step: open this repository in VS Code and run 'Dev Containers: Reopen in Container'."
