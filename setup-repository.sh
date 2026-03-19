#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

set -eu

IMAGE_REF="ueniueni/codebase-tooling-mcp:latest"
ENABLE_VULKAN_GPU=auto

log() {
  printf '%s\n' "$*" >&2
}

usage() {
  log "Usage: $0 [--enable-vulkan-gpu|--disable-vulkan-gpu]"
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

while [ "$#" -gt 0 ]; do
  case "$1" in
    --enable-vulkan-gpu)
      ENABLE_VULKAN_GPU=true
      ;;
    --disable-vulkan-gpu)
      ENABLE_VULKAN_GPU=false
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      log "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
  shift
done

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

if [ "$ENABLE_VULKAN_GPU" = auto ]; then
  if [ -e /dev/dri ]; then
    ENABLE_VULKAN_GPU=true
  else
    ENABLE_VULKAN_GPU=false
  fi
fi

DEVCONTAINER_GPU_BLOCK=""
if [ "$ENABLE_VULKAN_GPU" = true ]; then
  DEVCONTAINER_GPU_BLOCK='  "runArgs": [
    "--device=/dev/dri"
  ],'
  if [ ! -e /dev/dri ]; then
    log "Warning: Vulkan GPU passthrough was forced on, but /dev/dri is not present on this host."
  fi
fi

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
${DEVCONTAINER_GPU_BLOCK}
  "containerEnv": {
    "DOCKER_HOST": "unix:///var/run/docker.sock",
    "DOCKER_CONFIG": "/home/app/.docker",
    "MCP_APPLY_REPO_DEFAULTS": "true",
    "MCP_TRANSPORT": "http",
    "ALLOW_MUTATIONS": "true",
    "OLLAMA_HOST": "0.0.0.0:2345",
    "OLLAMA_FALLBACK_HOST": "0.0.0.0:2345",
    "LOCAL_INFER_ENDPOINT": "http://127.0.0.1:2345/api/generate"
  },
  "forwardPorts": [
    8000,
    2345
  ],
  "portsAttributes": {
    "8000": {
      "label": "MCP Server",
      "onAutoForward": "notify"
    },
    "2345": {
      "label": "Bundled LLM",
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
    "source=\${localEnv:HOME}/.continue,target=/host/.continue,type=bind,consistency=cached,readOnly=true",
    "source=\${localEnv:HOME}/.docker,target=/host/.docker,type=bind,consistency=cached,readOnly=true",
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
if [ "$ENABLE_VULKAN_GPU" = true ]; then
  log "  Vulkan GPU passthrough for Ollama enabled via /dev/dri"
fi
log "  .gitignore entries for generated hidden folders (one-time bootstrap)"
log ""
log "The container image provides the inline autocomplete extension and repo defaults."
log "Next step: open this repository in VS Code and run 'Dev Containers: Reopen in Container'."
