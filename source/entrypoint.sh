#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

set -euo pipefail

umask 027

DEFAULT_OLLAMA_TEXT_ALIAS_SOURCE_MODEL=""
DEFAULT_OLLAMA_TEXT_ALIAS_MODEL=""
DEFAULT_CODING_AGENT_MODEL="qwen2.5-coder:1.5b"
DEFAULT_OLLAMA_TEXT_ALIAS_NUM_CTX="8192"
DEFAULT_CONTINUE_OLLAMA_MODELS="qwen2.5-coder:1.5b"

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

read_mcp_http_bearer_token_from_env_file() {
  local env_file="${1:-}"
  local line=""
  local token=""
  if [[ -z "${env_file}" || ! -r "${env_file}" ]]; then
    return 1
  fi
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    case "${line}" in
      MCP_HTTP_BEARER_TOKEN=*)
        token="${line#MCP_HTTP_BEARER_TOKEN=}"
        token="${token%"${token##*[![:space:]]}"}"
        token="${token%\"}"
        token="${token#\"}"
        token="${token%\'}"
        token="${token#\'}"
        if [[ -n "${token}" ]]; then
          printf '%s\n' "${token}"
          return 0
        fi
        ;;
    esac
  done < "${env_file}"
  return 1
}

secure_continue_env_file_for_devcontainer_user() {
  local continue_dir="${1:-/repo/.continue}"
  local env_file="${2:-${continue_dir}/.env}"
  if [[ "$(id -u)" -eq 0 ]] && id app >/dev/null 2>&1; then
    chown app:app "${continue_dir}" "${env_file}" || true
  fi
  chmod 700 "${continue_dir}" || true
  chmod 600 "${env_file}" || true
}

ensure_mcp_http_bearer_token() {
  local transport="${MCP_TRANSPORT:-stdio}"
  local auth_mode="${MCP_HTTP_AUTH_MODE:-token}"
  local token=""
  local env_file=""
  case "${transport}" in
    http|streamable-http|streamable_http) ;;
    *) return ;;
  esac
  case "${auth_mode}" in
    token|bearer|oauth-resource) ;;
    *) return ;;
  esac
  if [[ -n "${MCP_HTTP_BEARER_TOKEN:-}" ]]; then
    return
  fi

  for env_file in /repo/.continue/.env /repo/.env "${HOME:-/home/app}/.continue/.env"; do
    if token="$(read_mcp_http_bearer_token_from_env_file "${env_file}")"; then
      if [[ "${env_file}" == "/repo/.continue/.env" ]]; then
        secure_continue_env_file_for_devcontainer_user /repo/.continue "${env_file}"
      fi
      export MCP_HTTP_BEARER_TOKEN="${token}"
      echo "MCP_HTTP_BEARER_TOKEN loaded from local secret file ${env_file}" >&2
      return
    fi
  done

  if [[ -d /repo && -w /repo ]] && command -v openssl >/dev/null 2>&1; then
    token="$(openssl rand -hex 32)"
    mkdir -p /repo/.continue
    env_file="/repo/.continue/.env"
    if [[ -f "${env_file}" ]]; then
      printf '\nMCP_HTTP_BEARER_TOKEN=%s\n' "${token}" >> "${env_file}"
    else
      printf 'MCP_HTTP_BEARER_TOKEN=%s\n' "${token}" > "${env_file}"
    fi
    secure_continue_env_file_for_devcontainer_user /repo/.continue "${env_file}"
    export MCP_HTTP_BEARER_TOKEN="${token}"
    echo "MCP_HTTP_BEARER_TOKEN generated into local secret file ${env_file}" >&2
  fi
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

ollama_manifest_path_for_model_ref() {
  local model_ref="${1:-}"
  local tag="latest"
  local model_path="${model_ref}"
  local last_segment="${model_ref##*/}"

  if [[ "${last_segment}" == *:* ]]; then
    tag="${last_segment##*:}"
    model_path="${model_ref%:*}"
  fi

  if [[ "${model_path}" == */*/* ]]; then
    printf '%s/manifests/%s/%s\n' "${OLLAMA_MODELS}" "${model_path}" "${tag}"
  elif [[ "${model_path}" == */* ]]; then
    printf '%s/manifests/registry.ollama.ai/%s/%s\n' "${OLLAMA_MODELS}" "${model_path}" "${tag}"
  else
    printf '%s/manifests/registry.ollama.ai/library/%s/%s\n' "${OLLAMA_MODELS}" "${model_path}" "${tag}"
  fi
}

ollama_model_blob_path_from_manifest() {
  local manifest_path="${1:-}"
  python - "${manifest_path}" "${OLLAMA_MODELS}" <<'PY'
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
models_root = pathlib.Path(sys.argv[2])

try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    print(f"unable to read Ollama manifest {manifest_path}: {exc}", file=sys.stderr)
    raise SystemExit(1)

for layer in manifest.get("layers", []):
    if layer.get("mediaType") != "application/vnd.ollama.image.model":
        continue
    digest = str(layer.get("digest", ""))
    if digest.startswith("sha256:"):
        print(models_root / "blobs" / digest.replace(":", "-"))
        raise SystemExit(0)

print(f"no model blob layer found in Ollama manifest {manifest_path}", file=sys.stderr)
raise SystemExit(1)
PY
}

ensure_ollama_text_alias_from_preloaded_model() {
  local alias="${1:-}"
  local source_model="${2:-}"
  local num_ctx="${3:-}"
  local manifest_path=""
  local blob_path=""
  local alias_manifest_path=""
  local alias_blob_path=""
  local modelfile=""
  local create_log=""
  local safe_alias=""

  if [[ -z "${alias}" ]] || [[ -z "${source_model}" ]]; then
    return 0
  fi

  if ! ollama show "${source_model}" >/dev/null 2>&1; then
    if is_truthy "${OLLAMA_ALLOW_PULL:-false}"; then
      echo "pulling Ollama source model '${source_model}' for local text alias '${alias}'" >&2
      ollama pull "${source_model}"
    else
      echo "Ollama source model '${source_model}' is missing and OLLAMA_ALLOW_PULL=false; cannot create local text alias '${alias}'." >&2
      return 1
    fi
  fi

  manifest_path="$(ollama_manifest_path_for_model_ref "${source_model}")"
  if [[ ! -f "${manifest_path}" ]]; then
    echo "Ollama manifest '${manifest_path}' is missing; cannot create local text alias '${alias}'." >&2
    return 1
  fi

  if ! blob_path="$(ollama_model_blob_path_from_manifest "${manifest_path}")"; then
    return 1
  fi
  if [[ ! -f "${blob_path}" ]]; then
    echo "Ollama model blob '${blob_path}' is missing; cannot create local text alias '${alias}'." >&2
    return 1
  fi

  if ollama show "${alias}" >/dev/null 2>&1; then
    alias_manifest_path="$(ollama_manifest_path_for_model_ref "${alias}")"
    if [[ -f "${alias_manifest_path}" ]] && alias_blob_path="$(ollama_model_blob_path_from_manifest "${alias_manifest_path}")"; then
      if [[ "${alias_blob_path}" == "${blob_path}" ]]; then
        if [[ ! "${num_ctx}" =~ ^[0-9]+$ ]] || [[ "${num_ctx}" -le 0 ]] \
          || ollama show "${alias}" --modelfile 2>/dev/null \
            | grep -Eq "^[[:space:]]*PARAMETER[[:space:]]+num_ctx[[:space:]]+${num_ctx}([[:space:]]|$)"; then
          return 0
        fi
        echo "Ollama alias '${alias}' exists with stale num_ctx; recreating it with num_ctx=${num_ctx}." >&2
      else
        echo "Ollama alias '${alias}' exists but does not point at '${source_model}'; recreating it." >&2
      fi
    else
      echo "Ollama alias '${alias}' exists but its manifest cannot be inspected; recreating it." >&2
    fi
  fi

  modelfile="$(mktemp)"
  safe_alias="$(printf '%s' "${alias}" | tr -c 'A-Za-z0-9_.-' '_')"
  create_log="/tmp/ollama-create-${safe_alias}.log"
  {
    printf 'FROM %s\n' "${blob_path}"
    if [[ "${num_ctx}" =~ ^[0-9]+$ ]] && [[ "${num_ctx}" -gt 0 ]]; then
      printf 'PARAMETER num_ctx %s\n' "${num_ctx}"
    fi
  } > "${modelfile}"

  if ! ollama create "${alias}" -f "${modelfile}" >"${create_log}" 2>&1; then
    cat "${create_log}" >&2 || true
    rm -f "${modelfile}"
    return 1
  fi

  rm -f "${modelfile}"
  echo "created Ollama text-only alias '${alias}' from '${source_model}'" >&2
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
  local allow_pull="${OLLAMA_ALLOW_PULL:-false}"
  local model_name=""
  local missing_models=()

  if [[ -z "${models_csv// }" ]]; then
    echo "CONTINUE_OLLAMA_MODELS is empty; no default bundled Ollama model set is declared." >&2
    if ! is_truthy "${allow_pull}"; then
      echo "OLLAMA_ALLOW_PULL=false; runtime model downloads remain disabled." >&2
    fi
    if [[ -n "${CODING_DEFAULT_MODEL:-}" ]]; then
      echo "CODING_DEFAULT_MODEL='${CODING_DEFAULT_MODEL}' will not be guaranteed while CONTINUE_OLLAMA_MODELS is empty." >&2
    fi
    if [[ -n "${CODING_MICRO_MODEL:-}" ]]; then
      echo "CODING_MICRO_MODEL='${CODING_MICRO_MODEL}' will not be guaranteed while CONTINUE_OLLAMA_MODELS is empty." >&2
    fi
    return 0
  fi

  if [[ -n "${CODING_DEFAULT_MODEL:-}" ]] && ! csv_has_model "${models_csv}" "${CODING_DEFAULT_MODEL}"; then
    echo "CODING_DEFAULT_MODEL='${CODING_DEFAULT_MODEL}' is not included in CONTINUE_OLLAMA_MODELS. The image will not guarantee the coding model is present." >&2
  fi
  if [[ -n "${CODING_MICRO_MODEL:-}" ]] && ! csv_has_model "${models_csv}" "${CODING_MICRO_MODEL}"; then
    echo "CODING_MICRO_MODEL='${CODING_MICRO_MODEL}' is not included in CONTINUE_OLLAMA_MODELS. The image will not guarantee the micro coding model is present." >&2
  fi

  while IFS= read -r model_name; do
    if ollama show "${model_name}" >/dev/null 2>&1; then
      continue
    fi
    if is_truthy "${allow_pull}"; then
      echo "pulling missing Ollama model '${model_name}' because OLLAMA_ALLOW_PULL=${allow_pull}" >&2
      ollama pull "${model_name}"
    fi
    if ! ollama show "${model_name}" >/dev/null 2>&1; then
      missing_models+=("${model_name}")
    fi
  done < <(iter_ollama_models "${models_csv}")

  if [[ ${#missing_models[@]} -gt 0 ]]; then
    if is_truthy "${allow_pull}"; then
      echo "Missing Ollama models after runtime pull attempt: ${missing_models[*]}" >&2
    else
      echo "Missing bundled Ollama models with OLLAMA_ALLOW_PULL=false: ${missing_models[*]}" >&2
    fi
    return 1
  fi

  if [[ -n "${CODING_DEFAULT_MODEL:-}" ]] && ! ollama show "${CODING_DEFAULT_MODEL}" >/dev/null 2>&1; then
    echo "CODING_DEFAULT_MODEL='${CODING_DEFAULT_MODEL}' is unavailable after Ollama model verification." >&2
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
  if ! is_truthy "${OLLAMA_ALLOW_PULL:-false}"; then
    echo "OLLAMA_ALLOW_PULL=false; refusing runtime download of missing Ollama model '${model_name}'." >&2
    return 1
  fi
  ollama pull "${model_name}"
  ollama show "${model_name}" >/dev/null 2>&1
}

seed_ollama_models_from_image_preload() {
  local image_models_dir="${OLLAMA_IMAGE_MODELS:-/opt/codebase-tooling/preloaded-ollama-models}"
  if [[ ! -d "${image_models_dir}" ]]; then
    return
  fi

  mkdir -p "${OLLAMA_MODELS}"
  if [[ "$(readlink -f "${image_models_dir}")" == "$(readlink -f "${OLLAMA_MODELS}")" ]]; then
    return
  fi
  if cp -an "${image_models_dir}/." "${OLLAMA_MODELS}/" 2>/dev/null; then
    return
  fi

  # Fallback for cp variants that do not accept -n with -a.
  cp -a "${image_models_dir}/." "${OLLAMA_MODELS}/"
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

  if [[ -d /host/.docker ]]; then
    mkdir -p /home/app/.docker
    if ! find /home/app/.docker -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
      cp -a /host/.docker/. /home/app/.docker/
    fi
    if [[ -f /host/.docker/config.json ]]; then
      python - <<'PY'
import json
import pathlib
import shutil
import sys

source = pathlib.Path("/host/.docker/config.json")
target = pathlib.Path("/home/app/.docker/config.json")

try:
    config = json.loads(source.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    print(f"warning: unable to load host Docker config: {exc}", file=sys.stderr)
    raise SystemExit(0)

changed = False

creds_store = config.get("credsStore")
if isinstance(creds_store, str) and creds_store:
    if shutil.which(f"docker-credential-{creds_store}") is None:
        config.pop("credsStore", None)
        changed = True

cred_helpers = config.get("credHelpers")
if isinstance(cred_helpers, dict):
    filtered = {
        registry: helper
        for registry, helper in cred_helpers.items()
        if not isinstance(helper, str)
        or not helper
        or shutil.which(f"docker-credential-{helper}") is not None
    }
    if filtered != cred_helpers:
        if filtered:
            config["credHelpers"] = filtered
        else:
            config.pop("credHelpers", None)
        changed = True

target.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
if changed:
    print(
        "sanitized Docker config for container use by removing unavailable credential helpers",
        file=sys.stderr,
    )
PY
    fi
    chown -R app:app /home/app/.docker
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

maybe_fix_gpu_device_groups() {
  if [[ "$(id -u)" -ne 0 ]]; then
    return
  fi
  if ! command -v usermod >/dev/null 2>&1; then
    return
  fi

  local device_path device_gid device_group fallback_group
  for device_path in /dev/dri/renderD* /dev/dri/card* /dev/kfd; do
    if [[ ! -e "${device_path}" ]]; then
      continue
    fi
    device_gid="$(stat -c '%g' "${device_path}" 2>/dev/null || true)"
    if [[ -z "${device_gid}" ]] || [[ "${device_gid}" == "0" ]]; then
      continue
    fi
    device_group="$(getent group "${device_gid}" | cut -d: -f1 || true)"
    if [[ -z "${device_group}" ]] && command -v groupadd >/dev/null 2>&1; then
      case "${device_path}" in
        /dev/kfd) fallback_group="kfd-host-${device_gid}" ;;
        *) fallback_group="dri-host-${device_gid}" ;;
      esac
      groupadd --gid "${device_gid}" "${fallback_group}" || true
      device_group="${fallback_group}"
    fi
    if [[ -n "${device_group}" ]]; then
      usermod -aG "${device_group}" app || true
    fi
  done
}

copy_continue_default_if_missing_or_stale() {
  local default_path="$1"
  local target_path="$2"
  local target_name
  target_name=$(basename "${target_path}")

  if [[ ! -f "${target_path}" ]]; then
    cp "${default_path}" "${target_path}"
    return
  fi

  case "${target_name}" in
    codebase-tooling-mcp.yaml)
      if grep -q 'secret-token' "${target_path}"; then
        echo "Continue MCP server profile has stale auth header; refreshing ${target_path}." >&2
        cp "${default_path}" "${target_path}"
      fi
      ;;
  esac
}

remove_retired_continue_model_default() {
  local target_path="$1"
  local model_name="$2"
  if [[ -f "${target_path}" ]] && grep -q "model: ${model_name}" "${target_path}"; then
    echo "Removing retired Continue model profile ${target_path}." >&2
    rm -f "${target_path}"
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
  copy_continue_default_if_missing_or_stale \
    "${defaults_root}/continue/codebase-tooling-mcp.yaml" \
    /repo/.continue/mcpServers/codebase-tooling-mcp.yaml
  mkdir -p /repo/.continue/models
  remove_retired_continue_model_default \
    /repo/.continue/models/coding-agent-llama3.1-8b.yaml \
    "llama3.1:8b"
  remove_retired_continue_model_default \
    /repo/.continue/models/coding-qwen3.6-35b-a3b.yaml \
    "qwen3.6-35b-a3b:iq1"
  while IFS= read -r model_path; do
    model_name=$(basename "${model_path}")
    copy_continue_default_if_missing_or_stale \
      "${model_path}" \
      "/repo/.continue/models/${model_name}"
  done < <(find "${defaults_root}/continue/models" -maxdepth 1 -type f -name '*.yaml' | sort)
  if [[ ! -f /repo/.continue/model-routing.yaml ]]; then
    cp "${defaults_root}/continue/model-routing.yaml" /repo/.continue/model-routing.yaml
  elif grep -q 'model: llama3.1:8b' /repo/.continue/model-routing.yaml \
    || grep -q 'model: qwen3.6-35b-a3b:iq1' /repo/.continue/model-routing.yaml; then
    echo "Continue model routing references retired bundled model profiles; refreshing /repo/.continue/model-routing.yaml." >&2
    cp "${defaults_root}/continue/model-routing.yaml" /repo/.continue/model-routing.yaml
  fi

  mkdir -p /home/app/.codex
  if [[ ! -f /home/app/.codex/config.toml ]]; then
    cp "${defaults_root}/codex/config.toml" /home/app/.codex/config.toml
  else
    if grep -q '^\[mcp_servers.codebase_tooling_mcp\]$' /home/app/.codex/config.toml; then
      sed -i 's/^\[mcp_servers\.codebase_tooling_mcp\]$/[mcp_servers."codebase-tooling-mcp"]/g' /home/app/.codex/config.toml
    fi
    if ! grep -q '^\[mcp_servers\."codebase-tooling-mcp"\]$' /home/app/.codex/config.toml; then
      printf '
[mcp_servers."codebase-tooling-mcp"]
url = "http://localhost:8000/mcp"
bearer_token_env_var = "MCP_HTTP_BEARER_TOKEN"
' >> /home/app/.codex/config.toml
    fi
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
    for entry in '/.codebase-tooling-mcp/' '/.continue/' '/.config/' '/.devcontainer/' '/.gitignore_codebase_tooling_mcp.touched'; do
      if ! grep -qxF "${entry}" /repo/.gitignore; then
        printf '%s\n' "${entry}" >> /repo/.gitignore
      fi
    done
    : > "${gitignore_touch_file}"
  fi
}

if [[ "$(id -u)" -eq 0 ]] && [[ "${1:-}" != "--as-app" ]]; then
  maybe_fix_docker_sock_group
  maybe_fix_gpu_device_groups
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
seed_ollama_models_from_image_preload
export OLLAMA_VULKAN="${OLLAMA_VULKAN:-0}"
CODING_DEFAULT_MODEL="${CODING_DEFAULT_MODEL:-qwen2.5-coder:1.5b}"
CODING_AGENT_MODEL="${CODING_AGENT_MODEL:-${DEFAULT_CODING_AGENT_MODEL}}"
CODING_MICRO_MODEL="${CODING_MICRO_MODEL:-qwen2.5-coder:1.5b}"
OLLAMA_TEXT_ALIAS_SOURCE_MODEL="${OLLAMA_TEXT_ALIAS_SOURCE_MODEL:-${DEFAULT_OLLAMA_TEXT_ALIAS_SOURCE_MODEL}}"
OLLAMA_TEXT_ALIAS_MODEL="${OLLAMA_TEXT_ALIAS_MODEL:-${DEFAULT_OLLAMA_TEXT_ALIAS_MODEL}}"
OLLAMA_TEXT_ALIAS_NUM_CTX="${OLLAMA_TEXT_ALIAS_NUM_CTX:-${OLLAMA_CONTEXT_LENGTH:-${DEFAULT_OLLAMA_TEXT_ALIAS_NUM_CTX}}}"
OLLAMA_STARTUP_TIMEOUT="${OLLAMA_STARTUP_TIMEOUT:-30}"
OLLAMA_ENABLED="${OLLAMA_ENABLED:-true}"
OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
OLLAMA_FALLBACK_HOST="${OLLAMA_FALLBACK_HOST:-0.0.0.0:11434}"
OLLAMA_BLOCK_UNTIL_DEFAULT_MODEL="${OLLAMA_BLOCK_UNTIL_DEFAULT_MODEL:-true}"
OLLAMA_ALLOW_PULL="${OLLAMA_ALLOW_PULL:-false}"
OLLAMA_STARTUP_TIMEOUT="$(sanitize_positive_int "${OLLAMA_STARTUP_TIMEOUT}" 30 "OLLAMA_STARTUP_TIMEOUT")"

ensure_mcp_http_bearer_token
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
    if [[ -n "${OLLAMA_TEXT_ALIAS_MODEL}" ]] \
      && { [[ "${CODING_DEFAULT_MODEL:-}" == "${OLLAMA_TEXT_ALIAS_MODEL}" ]] \
        || csv_has_model "${CONTINUE_OLLAMA_MODELS-${DEFAULT_CONTINUE_OLLAMA_MODELS}}" "${OLLAMA_TEXT_ALIAS_MODEL}"; }; then
      if ! ensure_ollama_text_alias_from_preloaded_model \
        "${OLLAMA_TEXT_ALIAS_MODEL}" \
        "${OLLAMA_TEXT_ALIAS_SOURCE_MODEL}" \
        "${OLLAMA_TEXT_ALIAS_NUM_CTX}"; then
        echo "failed to create Ollama text-only alias '${OLLAMA_TEXT_ALIAS_MODEL}' from '${OLLAMA_TEXT_ALIAS_SOURCE_MODEL}'" >&2
        echo "continuing with running Ollama and current model set" >&2
      fi
    fi

    if is_truthy "${OLLAMA_BLOCK_UNTIL_DEFAULT_MODEL}" && [[ -n "${CODING_DEFAULT_MODEL:-}" ]]; then
      echo "ensuring default Ollama model '${CODING_DEFAULT_MODEL}' is available before server startup" >&2
      if ! ensure_ollama_model_installed "${CODING_DEFAULT_MODEL}"; then
        echo "failed to ensure default Ollama model '${CODING_DEFAULT_MODEL}' is available before server startup" >&2
        echo "continuing with running Ollama and current model set" >&2
      fi
    fi
    (
      if ! ensure_ollama_models_installed; then
        echo "ollama model ensure failed in background; see logs above" >&2
        echo "continuing with running Ollama and current model set" >&2
      fi
    ) &
    echo "ollama model availability check running in background (OLLAMA_ALLOW_PULL=${OLLAMA_ALLOW_PULL})" >&2
  fi
else
  echo "OLLAMA_ENABLED=false; skipping Ollama startup" >&2
fi


exec python /app/server.py
