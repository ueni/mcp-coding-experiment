#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

set -euo pipefail

umask 027

ensure_ollama_models_installed() {
  local models_csv="${CONTINUE_OLLAMA_MODELS:-qwen2.5-coder:7b,granite3.2:2b,phi4-mini:3.8b,phi4-mini-reasoning:3.8b,deepseek-r1:1.5b,deepscaler:1.5b,granite3.2-vision:2b,llama3.2:3b}"
  local model_name=""
  local old_ifs="${IFS}"
  IFS=','
  for model_name in ${models_csv}; do
    model_name="$(echo "${model_name}" | xargs)"
    if [[ -z "${model_name}" ]]; then
      continue
    fi
    if ollama show "${model_name}" >/dev/null 2>&1; then
      continue
    fi
    ollama pull "${model_name}"
  done
  IFS="${old_ifs}"
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
  if ! grep -qxF '# codebase-tooling-mcp generated' /repo/.gitignore; then
    printf '\n# codebase-tooling-mcp generated\n' >> /repo/.gitignore
  fi
  for entry in '/.build/' '/.continue/' '/.config/' '/.devcontainer/'; do
    if ! grep -qxF "${entry}" /repo/.gitignore; then
      printf '%s\n' "${entry}" >> /repo/.gitignore
    fi
  done
}

if [[ "$(id -u)" -eq 0 ]] && [[ "${1:-}" != "--as-app" ]]; then
  maybe_fix_docker_sock_group
  bootstrap_user_home_from_host_mounts
  exec su -m -s /bin/bash app -c "/app/entrypoint.sh --as-app"
fi

if [[ "${1:-}" == "--as-app" ]]; then
  shift
fi

apply_repo_defaults

ollama serve >/tmp/ollama.log 2>&1 &

ready=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if [[ "${ready}" -ne 1 ]]; then
  echo "ollama failed to start; see /tmp/ollama.log" >&2
  exit 1
fi

ensure_ollama_models_installed

exec python /app/server.py
