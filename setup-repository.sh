#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

set -eu

IMAGE_REF="ueniueni/codebase-tooling-mcp:latest"
ENABLE_VULKAN_GPU=false
CONTINUE_MODEL_PROFILE=${CONTINUE_MODEL_PROFILE:-auto}
CONTINUE_MODEL_ID=${CONTINUE_MODEL_ID:-}
CONTINUE_MODEL_API_BASE=${CONTINUE_MODEL_API_BASE:-}
CONTINUE_MODEL_PROXY=${CONTINUE_MODEL_PROXY:-}
CONTINUE_MODEL_CA_BUNDLE=${CONTINUE_MODEL_CA_BUNDLE:-}

log() {
  printf '%s\n' "$*" >&2
}

usage() {
  log "Usage: $0 [--enable-vulkan-gpu|--disable-vulkan-gpu] [--continue-model-profile local|openai-compatible|none]"
  log "Environment overrides for OpenAI-compatible setup: CONTINUE_MODEL_ID, CONTINUE_MODEL_API_BASE, CONTINUE_MODEL_PROXY, CONTINUE_MODEL_CA_BUNDLE"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "Missing required command: $1"
    exit 1
  fi
}

script_dir() {
  case "$0" in
    */*) dirname "$0" ;;
    *) pwd ;;
  esac
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
    --continue-model-profile)
      if [ "$#" -lt 2 ]; then
        log "Missing value for --continue-model-profile"
        usage
        exit 1
      fi
      CONTINUE_MODEL_PROFILE=$2
      shift
      ;;
    --continue-model-profile=*)
      CONTINUE_MODEL_PROFILE=${1#*=}
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
require_cmd sed

REPO_ROOT=$(find_repo_root || true)
if [ -z "$REPO_ROOT" ]; then
  log "This script must run inside a Git repository."
  exit 1
fi

cd "$REPO_ROOT"

REPO_NAME=$(basename "$REPO_ROOT")

DEVCONTAINER_RUNARGS_BLOCK='  "runArgs": [
    "-p",
    "127.0.0.1:8000:8000",
    "-p",
    "127.0.0.1:2345:2345",
    "--security-opt=seccomp=unconfined",
    "--security-opt=apparmor=unconfined"'
DEVCONTAINER_GPU_ENV_BLOCK=""
if [ "$ENABLE_VULKAN_GPU" = true ]; then
  DEVCONTAINER_RUNARGS_BLOCK="${DEVCONTAINER_RUNARGS_BLOCK},
    \"--device=/dev/dri\""
  if [ -e /dev/kfd ]; then
    DEVCONTAINER_RUNARGS_BLOCK="${DEVCONTAINER_RUNARGS_BLOCK},
    \"--device=/dev/kfd\""
  fi
  DEVCONTAINER_GPU_ENV_BLOCK='    "OLLAMA_VULKAN": "1",'
  if [ ! -e /dev/dri ]; then
    log "Warning: Vulkan GPU passthrough was forced on, but /dev/dri is not present on this host."
  fi
fi
if [ -z "$DEVCONTAINER_GPU_ENV_BLOCK" ]; then
  DEVCONTAINER_GPU_ENV_BLOCK='    "OLLAMA_VULKAN": "0",'
fi
DEVCONTAINER_RUNARGS_BLOCK="${DEVCONTAINER_RUNARGS_BLOCK}
  ],"

ensure_gitignore_entry() {
  entry=$1
  if [ ! -f .gitignore ]; then
    : > .gitignore
  fi
  if ! grep -qxF "$entry" .gitignore; then
    printf '%s\n' "$entry" >> .gitignore
  fi
}


json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

prompt_continue_model_profile() {
  if [ "$CONTINUE_MODEL_PROFILE" != "auto" ]; then
    return
  fi
  CONTINUE_MODEL_PROFILE=local
  if [ -t 0 ]; then
    log ""
    log "Continue model setup:"
    log "  1) local bundled Ollama model (qwen2.5-coder:1.5b)"
    log "  2) OpenAI-compatible endpoint (custom apiBase/model, optional MITM proxy)"
    log "  3) skip Continue model config"
    printf 'Select Continue model profile [1]: ' >&2
    read -r choice || choice=""
    case "$choice" in
      2) CONTINUE_MODEL_PROFILE=openai-compatible ;;
      3) CONTINUE_MODEL_PROFILE=none ;;
      *) CONTINUE_MODEL_PROFILE=local ;;
    esac
  fi
}

write_openai_compatible_continue_model() {
  profile_path=$1
  model_id=${CONTINUE_MODEL_ID:-}
  api_base=${CONTINUE_MODEL_API_BASE:-}
  proxy=${CONTINUE_MODEL_PROXY:-}
  ca_bundle=${CONTINUE_MODEL_CA_BUNDLE:-}

  if [ -t 0 ]; then
    if [ -z "$model_id" ]; then
      printf 'OpenAI-compatible model id [gpt-4.1-mini]: ' >&2
      read -r model_id || model_id=""
      model_id=${model_id:-gpt-4.1-mini}
    fi
    if [ -z "$api_base" ]; then
      printf 'OpenAI-compatible apiBase, include /v1 [http://127.0.0.1:4000/v1]: ' >&2
      read -r api_base || api_base=""
      api_base=${api_base:-http://127.0.0.1:4000/v1}
    fi
    if [ -z "$proxy" ]; then
      printf 'Optional MITM/proxy URL, empty for none: ' >&2
      read -r proxy || proxy=""
    fi
    if [ -n "$proxy" ] && [ -z "$ca_bundle" ]; then
      printf 'Optional MITM CA bundle path, empty to use system trust: ' >&2
      read -r ca_bundle || ca_bundle=""
    fi
  fi

  model_id=${model_id:-gpt-4.1-mini}
  api_base=${api_base:-http://127.0.0.1:4000/v1}
  CONTINUE_MODEL_ID=$model_id
  CONTINUE_MODEL_API_BASE=$api_base
  CONTINUE_MODEL_PROXY=$proxy
  CONTINUE_MODEL_CA_BUNDLE=$ca_bundle

  cat > "$profile_path" <<EOF
name: coding-openai-compatible
version: 0.0.1
schema: v1
models:
  - name: Coding - OpenAI Compatible Endpoint
    provider: openai
    model: $(json_escape "$model_id")
    apiBase: $(json_escape "$api_base")
    roles:
      - chat
      - edit
      - apply
    requestOptions:
      timeout: 300000
EOF
  if [ -n "$proxy" ] || [ -n "$ca_bundle" ]; then
    {
      if [ -n "$proxy" ]; then
        printf '      proxy: %s\n' "$(json_escape "$proxy")"
      fi
      if [ -n "$ca_bundle" ]; then
        printf '      caBundlePath: %s\n' "$(json_escape "$ca_bundle")"
      fi
    } >> "$profile_path"
  fi
}

write_continue_model_routing() {
  routing_path=$1
  model_id=$2
  model_file=$3
  cat > "$routing_path" <<EOF
schema: v1
router:
  model: ${model_id}
  file: ${model_file}
routes:
  coding:
    model: ${model_id}
    file: ${model_file}
  coding_agent:
    model: ${model_id}
    file: ${model_file}
  coding_micro:
    model: qwen2.5-coder:1.5b
    file: .continue/models/coding-qwen2.5-coder-1.5b.yaml
EOF
}

configure_continue_models() {
  prompt_continue_model_profile
  if [ "$CONTINUE_MODEL_PROFILE" = "none" ]; then
    return
  fi
  case "$CONTINUE_MODEL_PROFILE" in
    local|openai-compatible) ;;
    *)
      log "Unknown Continue model profile: $CONTINUE_MODEL_PROFILE"
      usage
      exit 1
      ;;
  esac

  defaults_root="$(script_dir)/source/defaults/continue"
  mkdir -p .continue/models .continue/mcpServers
  if [ -f "$defaults_root/codebase-tooling-mcp.yaml" ]; then
    cp "$defaults_root/codebase-tooling-mcp.yaml" .continue/mcpServers/codebase-tooling-mcp.yaml
  fi
  if [ -d "$defaults_root/models" ]; then
    cp "$defaults_root/models"/*.yaml .continue/models/ 2>/dev/null || true
  fi

  case "$CONTINUE_MODEL_PROFILE" in
    openai-compatible)
      write_openai_compatible_continue_model .continue/models/coding-openai-compatible.yaml
      write_continue_model_routing .continue/model-routing.yaml "${CONTINUE_MODEL_ID:-gpt-4.1-mini}" ".continue/models/coding-openai-compatible.yaml"
      ;;
    local)
      if [ -f "$defaults_root/model-routing.yaml" ]; then
        cp "$defaults_root/model-routing.yaml" .continue/model-routing.yaml
      fi
      ;;
  esac
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
${DEVCONTAINER_RUNARGS_BLOCK}
  "containerEnv": {
    "DOCKER_HOST": "unix:///var/run/docker.sock",
    "DOCKER_CONFIG": "/home/app/.docker",
    "MCP_APPLY_REPO_DEFAULTS": "true",
    "MCP_TRANSPORT": "http",
    "MCP_HTTP_BEARER_TOKEN": "\${localEnv:MCP_HTTP_BEARER_TOKEN}",
    "MCP_AGENT_EXECUTION_MODE": "online",
    "ALLOW_MUTATIONS": "true",
${DEVCONTAINER_GPU_ENV_BLOCK}
    "OLLAMA_HOST": "0.0.0.0:2345",
    "OLLAMA_FALLBACK_HOST": "0.0.0.0:2345",
    "OLLAMA_CONTEXT_LENGTH": "8192",
    "OLLAMA_TEXT_ALIAS_NUM_CTX": "8192",
    "CODING_DEFAULT_MODEL": "qwen2.5-coder:1.5b",
    "CODING_AGENT_MODEL": "qwen2.5-coder:1.5b",
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
ensure_gitignore_entry '/.codebase-tooling-mcp/'
ensure_gitignore_entry '/.continue/'
ensure_gitignore_entry '/.config/'
ensure_gitignore_entry '/.devcontainer/'
ensure_gitignore_entry '/.gitignore_codebase_tooling_mcp.touched'
: > .gitignore_codebase_tooling_mcp.touched

configure_continue_models

log "Created:"
log "  .devcontainer/devcontainer.json"
if [ "$CONTINUE_MODEL_PROFILE" != "none" ]; then
  log "  .continue/ Continue model and MCP profiles"
fi
if [ "$ENABLE_VULKAN_GPU" = true ]; then
  log "  Vulkan GPU passthrough for Ollama enabled via /dev/dri"
fi
log "  .gitignore entries for generated hidden folders (one-time bootstrap)"
log ""
log "The container image provides the inline autocomplete extension and repo defaults."
log "Next step: export MCP_HTTP_BEARER_TOKEN before opening VS Code, then run 'Dev Containers: Reopen in Container'."
