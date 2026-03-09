#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

set -euo pipefail

umask 027

apply_repo_defaults() {
  local defaults_root="/opt/codebase-tooling/defaults"
  if [[ "${MCP_APPLY_REPO_DEFAULTS:-false}" != "true" ]]; then
    return
  fi
  if [[ ! -d /repo || ! -w /repo ]]; then
    return
  fi

  mkdir -p /repo/.continue/mcpServers
  if [[ ! -f /repo/.continue/mcpServers/http-mcp-server.yaml ]]; then
    cp "${defaults_root}/continue/http-mcp-server.yaml" /repo/.continue/mcpServers/http-mcp-server.yaml
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
  elif ! grep -qxF '/.build/' /repo/.gitignore; then
    printf '\n# codebase-tooling-mcp\n/.build/\n' >> /repo/.gitignore
  fi
}

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

exec python /app/server.py
