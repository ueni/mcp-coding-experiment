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

repo_slug_from_remote() {
  remote_url=$1
  case "$remote_url" in
    git@github.com:*)
      slug=${remote_url#git@github.com:}
      ;;
    ssh://git@github.com/*)
      slug=${remote_url#ssh://git@github.com/}
      ;;
    https://github.com/*)
      slug=${remote_url#https://github.com/}
      ;;
    http://github.com/*)
      slug=${remote_url#http://github.com/}
      ;;
    *)
      return 1
      ;;
  esac
  slug=${slug%.git}
  printf '%s\n' "$slug"
}

backup_if_exists() {
  target=$1
  if [ -e "$target" ]; then
    backup="${target}.bak"
    cp -R "$target" "$backup"
    log "Backed up $target -> $backup"
  fi
}

require_cmd git
require_cmd mkdir
require_cmd cp

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$REPO_ROOT" ]; then
  log "This script must run inside a Git repository."
  exit 1
fi

cd "$REPO_ROOT"

REMOTE_URL=$(git remote get-url origin 2>/dev/null || true)
if [ -z "$REMOTE_URL" ]; then
  log "Git remote 'origin' is not configured."
  exit 1
fi

REPO_SLUG=$(repo_slug_from_remote "$REMOTE_URL" || true)
if [ -z "$REPO_SLUG" ]; then
  log "Unable to derive a GitHub repository slug from remote origin: $REMOTE_URL"
  exit 1
fi
REPO_NAME=${REPO_SLUG##*/}

log "Bootstrapping codebase-tooling-mcp devcontainer into $REPO_SLUG"

mkdir -p .devcontainer

backup_if_exists .devcontainer/devcontainer.json
cat > .devcontainer/devcontainer.json <<EOF
{
  "name": "${REPO_NAME}",
  "image": "${IMAGE_REF}",
  "overrideCommand": false,
  "remoteUser": "app",
  "containerUser": "app",
  "workspaceFolder": "/repo",
  "containerEnv": {
    "DOCKER_HOST": "unix:///var/run/docker.sock",
    "MCP_APPLY_REPO_DEFAULTS": "true"
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
        "mhutchie.git-graph"
      ],
      "settings": {
        "mcpInlineAutocomplete.endpoint": "http://localhost:8000/mcp"
      }
    }
  },
  "mounts": [
    "source=\${localEnv:HOME}/.continue,target=/home/app/.continue,type=bind,consistency=cached",
    "source=\${localEnv:HOME}/.gitconfig,target=/home/app/.gitconfig,type=bind,consistency=cached",
    "source=/var/run/docker.sock,target=/var/run/docker.sock,type=bind"
  ]
}
EOF

log "Created:"
log "  .devcontainer/devcontainer.json"
log ""
log "The container image provides the inline autocomplete extension and repo defaults."
log "Next step: open this repository in VS Code and run 'Dev Containers: Reopen in Container'."
