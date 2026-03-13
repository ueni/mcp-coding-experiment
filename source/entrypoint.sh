#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

set -euo pipefail

umask 027

DEFAULT_CONTINUE_OLLAMA_MODELS="qwen2.5-coder:7b,granite3.2:2b,phi4-mini:3.8b,phi4-mini-reasoning:3.8b,deepseek-r1:1.5b,deepscaler:1.5b,granite3.2-vision:2b,llama3.2:3b"

is_truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

sanitize_positive_int() {
  local raw="${1:-}"
  local fallback="${2:-1}"
  local var_name="${3:-value}"
  if [[ "${raw}" =~ ^[0-9]+$ ]] && [[ "${raw}" -gt 0 ]]; then
    echo "${raw}"
    return 0
  fi
  echo "Invalid ${var_name}='${raw}'; using default ${fallback}" >&2
  echo "${fallback}"
}

_ollama_probe_url() {
  local host_port="${1:-127.0.0.1:11434}"
  local host="${host_port%:*}"
  local port="${host_port##*:}"
  if [[ "${host}" == "0.0.0.0" ]] || [[ "${host}" == "::" ]]; then
    host="127.0.0.1"
  fi
  echo "http://${host}:${port}/api/tags"
}

iter_ollama_models() {
  local models_csv="${1:-}"
  local model_name=""
  local old_ifs="${IFS}"
  IFS=','
  for model_name in ${models_csv}; do
    model_name="$(echo "${model_name}" | xargs)"
    if [[ -n "${model_name}" ]]; then
      printf '%s\n' "${model_name}"
    fi
  done
  IFS="${old_ifs}"
}

csv_has_model() {
  local models_csv="${1:-}"
  local wanted="${2:-}"
  local model_name=""
  if [[ -z "${wanted}" ]]; then
    return 1
  fi
  while IFS= read -r model_name; do
    if [[ "${model_name}" == "${wanted}" ]]; then
      return 0
    fi
  done < <(iter_ollama_models "${models_csv}")
  return 1
}

start_ollama_with_host() {
  local host_port="${1}"
  local probe_url
  probe_url="$(_ollama_probe_url "${host_port}")"
  export OLLAMA_HOST="${host_port}"
  ollama serve >/tmp/ollama.log 2>&1 &
  local ollama_pid=$!
  local ready=0
  for _ in $(seq 1 "${OLLAMA_STARTUP_TIMEOUT}"); do
    if curl -fsS "${probe_url}" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  if [[ "${ready}" -eq 1 ]]; then
    return 0
  fi
  kill "${ollama_pid}" >/dev/null 2>&1 || true
  wait "${ollama_pid}" >/dev/null 2>&1 || true
  return 1
}

ensure_ollama_models_installed() {
  local models_csv="${CONTINUE_OLLAMA_MODELS-${DEFAULT_CONTINUE_OLLAMA_MODELS}}"
  local model_name=""
  local missing_models=()

  if [[ -z "${models_csv// }" ]]; then
    echo "CONTINUE_OLLAMA_MODELS is empty; skipping Ollama model pre-pull. This is an explicit opt-out, so Continue may report 'model not found' until models are installed manually." >&2
    if [[ -n "${CODING_DEFAULT_MODEL:-}" ]]; then
      echo "CODING_DEFAULT_MODEL='${CODING_DEFAULT_MODEL}' will not be pulled automatically while CONTINUE_OLLAMA_MODELS is empty." >&2
    fi
    return 0
  fi

  if [[ -n "${CODING_DEFAULT_MODEL:-}" ]] && ! csv_has_model "${models_csv}" "${CODING_DEFAULT_MODEL}"; then
    echo "CODING_DEFAULT_MODEL='${CODING_DEFAULT_MODEL}' is not included in CONTINUE_OLLAMA_MODELS. Bootstrap will not guarantee the coding model is present." >&2
  fi

  while IFS= read -r model_name; do
    if ollama show "${model_name}" >/dev/null 2>&1; then
      continue
    fi
    ollama pull "${model_name}"
  done < <(iter_ollama_models "${models_csv}")

  while IFS= read -r model_name; do
    if ! ollama show "${model_name}" >/dev/null 2>&1; then
      missing_models+=("${model_name}")
    fi
  done < <(iter_ollama_models "${models_csv}")

  if [[ ${#missing_models[@]} -gt 0 ]]; then
    echo "Missing Ollama models after bootstrap: ${missing_models[*]}" >&2
    return 1
  fi

  if [[ -n "${CODING_DEFAULT_MODEL:-}" ]] && ! ollama show "${CODING_DEFAULT_MODEL}" >/dev/null 2>&1; then
    echo "CODING_DEFAULT_MODEL='${CODING_DEFAULT_MODEL}' is unavailable after Ollama bootstrap." >&2
    return 1
  fi

  return 0
}

ensure_ollama_model_installed() {
  local model_name="${1:-}"
  if [[ -z "${model_name}" ]]; then
    return 0
  fi
  if ollama show "${model_name}" >/dev/null 2>&1; then
    return 0
  fi
  ollama pull "${model_name}"
  ollama show "${model_name}" >/dev/null 2>&1
}

bootstrap_user_home_from_host_mounts() {
  if [[ "$(id -u)" -ne 0 ]]; then
    return
  fi

  if [[ -d /host/.continue ]]; then
    mkdir -p /home/app/.continue
    if ! find /home/app/.continue -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
      cp -a /host/.continue/. /home/app/.continue/
      chown -R app:app /home/app/.continue
    fi
  fi

  if [[ -d /host/.codex ]]; then
    mkdir -p /home/app/.codex
    if ! find /home/app/.codex -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
      cp -a /host/.codex/. /home/app/.codex/
      chown -R app:app /home/app/.codex
    fi
  fi

  if [[ -f /host/.gitconfig ]] && [[ ! -f /home/app/.gitconfig ]]; then
    cp /host/.gitconfig /home/app/.gitconfig
    chown app:app /home/app/.gitconfig
  fi
}

maybe_fix_docker_sock_group() {
  if [[ "$(id -u)" -ne 0 ]]; then
    return
  fi
  if [[ ! -S /var/run/docker.sock ]]; then
    return
  fi
  if ! command -v usermod >/dev/null 2>&1; then
    return
  fi

  local sock_gid sock_group
  sock_gid="$(stat -c '%g' /var/run/docker.sock)"
  sock_group="$(getent group "${sock_gid}" | cut -d: -f1 || true)"

  if [[ -z "${sock_group}" ]] && command -v groupadd >/dev/null 2>&1; then
    sock_group="docker-host"
    groupadd --gid "${sock_gid}" "${sock_group}" || true
  fi

  if [[ -n "${sock_group}" ]]; then
    usermod -aG "${sock_group}" app || true
  fi
}

apply_repo_defaults() {
  local defaults_root="/opt/codebase-tooling/defaults"
  if [[ "${MCP_APPLY_REPO_DEFAULTS:-false}" != "true" ]]; then
    return
  fi
  if [[ ! -d /repo || ! -w /repo ]]; then
    return
  fi

  mkdir -p /repo/.continue/mcpServers
  if [[ ! -f /repo/.continue/mcpServers/codebase-tooling-mcp.yaml ]]; then
    cp "${defaults_root}/continue/codebase-tooling-mcp.yaml" /repo/.continue/mcpServers/codebase-tooling-mcp.yaml
  fi
  mkdir -p /repo/.continue/models
  while IFS= read -r model_path; do
    model_name=$(basename "${model_path}")
    if [[ ! -f "/repo/.continue/models/${model_name}" ]]; then
      cp "${model_path}" "/repo/.continue/models/${model_name}"
    fi
  done < <(find "${defaults_root}/continue/models" -maxdepth 1 -type f -name '*.yaml' | sort)
  if [[ ! -f /repo/.continue/model-routing.yaml ]]; then
    cp "${defaults_root}/continue/model-routing.yaml" /repo/.continue/model-routing.yaml
  fi

  mkdir -p /home/app/.codex
  if [[ ! -f /home/app/.codex/config.toml ]]; then
    cp "${defaults_root}/codex/config.toml" /home/app/.codex/config.toml
  elif ! grep -q '^\[mcp_servers.codebase_tooling_mcp\]$' /home/app/.codex/config.toml; then
    printf '\n[mcp_servers.codebase_tooling_mcp]\nurl = "http://localhost:8000/mcp"\n' >> /home/app/.codex/config.toml
  fi

  mkdir -p /repo/.config/labs
  while IFS= read -r config_path; do
    config_name=$(basename "${config_path}")
    if [[ ! -f "/repo/.config/labs/${config_name}" ]]; then
      cp "${config_path}" "/repo/.config/labs/${config_name}"
    fi
  done < <(find "${defaults_root}/config/labs" -maxdepth 1 -type f -name '*.json' | sort)

  if [[ ! -f /repo/.gitignore ]]; then
    cp "${defaults_root}/gitignore" /repo/.gitignore
  fi
  local gitignore_touch_file="/repo/.gitignore_codebase_tooling_mcp.touched"
  if [[ ! -f "${gitignore_touch_file}" ]]; then
    if ! grep -qxF '# codebase-tooling-mcp generated' /repo/.gitignore; then
      printf '\n# codebase-tooling-mcp generated\n' >> /repo/.gitignore
    fi
    for entry in '/.build/' '/.continue/' '/.config/' '/.devcontainer/' '/.gitignore_codebase_tooling_mcp.touched'; do
      if ! grep -qxF "${entry}" /repo/.gitignore; then
        printf '%s\n' "${entry}" >> /repo/.gitignore
      fi
    done
    : > "${gitignore_touch_file}"
  fi
}

if [[ "$(id -u)" -eq 0 ]] && [[ "${1:-}" != "--as-app" ]]; then
  maybe_fix_docker_sock_group
  bootstrap_user_home_from_host_mounts
  export HOME="/home/app"
  export USER="app"
  export LOGNAME="app"
  export OLLAMA_MODELS="${OLLAMA_MODELS:-/home/app/.ollama/models}"
  exec su -m -s /bin/bash app -c "/app/entrypoint.sh --as-app"
fi

if [[ "${1:-}" == "--as-app" ]]; then
  shift
fi

export HOME="${HOME:-/home/app}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-${HOME}/.ollama/models}"
CODING_DEFAULT_MODEL="${CODING_DEFAULT_MODEL:-qwen2.5-coder:7b}"
OLLAMA_STARTUP_TIMEOUT="${OLLAMA_STARTUP_TIMEOUT:-30}"
OLLAMA_ENABLED="${OLLAMA_ENABLED:-true}"
OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
OLLAMA_FALLBACK_HOST="${OLLAMA_FALLBACK_HOST:-0.0.0.0:11434}"
OLLAMA_BLOCK_UNTIL_DEFAULT_MODEL="${OLLAMA_BLOCK_UNTIL_DEFAULT_MODEL:-true}"
OLLAMA_STARTUP_TIMEOUT="$(sanitize_positive_int "${OLLAMA_STARTUP_TIMEOUT}" 30 "OLLAMA_STARTUP_TIMEOUT")"

apply_repo_defaults

if is_truthy "${OLLAMA_ENABLED}"; then
  started=0
  hosts=("${OLLAMA_HOST}")
  if [[ -n "${OLLAMA_FALLBACK_HOST}" ]] && [[ "${OLLAMA_FALLBACK_HOST}" != "${OLLAMA_HOST}" ]]; then
    hosts+=("${OLLAMA_FALLBACK_HOST}")
  fi

  for host in "${hosts[@]}"; do
    if start_ollama_with_host "${host}"; then
      echo "ollama ready on ${host}" >&2
      started=1
      break
    fi
    echo "ollama failed to start on ${host}; see /tmp/ollama.log" >&2
  done

  if [[ "${started}" -ne 1 ]]; then
    echo "continuing without Ollama" >&2
  else
    if is_truthy "${OLLAMA_BLOCK_UNTIL_DEFAULT_MODEL}" && [[ -n "${CODING_DEFAULT_MODEL:-}" ]]; then
      echo "ensuring default Ollama model '${CODING_DEFAULT_MODEL}' before server startup" >&2
      if ! ensure_ollama_model_installed "${CODING_DEFAULT_MODEL}"; then
        echo "failed to install default Ollama model '${CODING_DEFAULT_MODEL}' before server startup" >&2
        echo "continuing with running Ollama and current model set" >&2
      fi
    fi
    (
      if ! ensure_ollama_models_installed; then
        echo "ollama model ensure failed in background; see logs above" >&2
        echo "continuing with running Ollama and current model set" >&2
      fi
    ) &
    echo "ollama model ensure running in background" >&2
  fi
else
  echo "OLLAMA_ENABLED=false; skipping Ollama startup" >&2
fi


exec python /app/server.py
