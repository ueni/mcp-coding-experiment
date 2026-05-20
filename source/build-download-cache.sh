#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

# Shared Docker-build download cache helpers. These functions are intentionally
# shell-only so they can run inside Dockerfile RUN steps without adding runtime
# dependencies beyond Python/pip and curl.

build_cache_bool() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

build_cache_fail() {
  echo "build-download-cache: $*" >&2
  return 23
}

build_cache_download() {
  local cache_path="$1"
  local download_url="$2"
  local label="${3:-${cache_path}}"
  local tmp_path retries retry_delay retry_max_time

  if [ -z "${cache_path}" ] || [ -z "${download_url}" ]; then
    build_cache_fail "cache path and download URL are required"
    return $?
  fi

  mkdir -p "$(dirname "${cache_path}")"
  if [ -s "${cache_path}" ] && ! build_cache_bool "${MCP_REFRESH_BUILD_DOWNLOAD_CACHE:-false}"; then
    echo "Using cached ${label}: ${cache_path}"
    return 0
  fi

  if build_cache_bool "${MCP_BUILD_OFFLINE:-false}"; then
    build_cache_fail "missing required cached ${label} at ${cache_path} while MCP_BUILD_OFFLINE=true"
    return $?
  fi

  retries="${BUILD_CACHE_DOWNLOAD_RETRIES:-12}"
  retry_delay="${BUILD_CACHE_DOWNLOAD_RETRY_DELAY_SECONDS:-5}"
  retry_max_time="${BUILD_CACHE_DOWNLOAD_RETRY_MAX_TIME_SECONDS:-1800}"
  tmp_path="${cache_path}.part"
  if build_cache_bool "${MCP_REFRESH_BUILD_DOWNLOAD_CACHE:-false}"; then
    rm -f "${cache_path}" "${tmp_path}"
  fi

  if curl --fail --location --retry "${retries}" --retry-all-errors --retry-delay "${retry_delay}" \
    --retry-max-time "${retry_max_time}" --connect-timeout 30 --speed-limit 1024 --speed-time 120 \
    --continue-at - --http1.1 \
    "${download_url}" \
    -o "${tmp_path}"; then
    mv "${tmp_path}" "${cache_path}"
  else
    local curl_status=$?
    echo "build-download-cache: failed to download ${label}; preserved partial at ${tmp_path}" >&2
    return "${curl_status}"
  fi
}

build_cache_url_exists() {
  local url="$1"
  if build_cache_bool "${MCP_BUILD_OFFLINE:-false}"; then
    return 1
  fi
  curl --fail --location --retry 5 --retry-all-errors --retry-delay 5 --connect-timeout 30 --http1.1 \
    --silent --head "${url}" >/dev/null 2>&1
}

build_cache_requirement_digest() {
  python - "$@" <<'PY'
import hashlib
import sys
from pathlib import Path

hasher = hashlib.sha256()
for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    hasher.update(path.name.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(path.read_bytes())
    hasher.update(b"\0")
print(hasher.hexdigest())
PY
}

build_cache_python_tag() {
  "$1" - <<'PY'
import sys
import sysconfig

print(f"py{sys.version_info[0]}{sys.version_info[1]}-{sysconfig.get_platform()}")
PY
}

build_cache_pip_install() {
  local python_bin="$1"
  local section_name="$2"
  local requirements_file="$3"
  local locked_mode="${4:-false}"
  local py_tag digest mode_label wheelhouse tmp_wheelhouse
  local -a download_args install_args

  if [ ! -s "${requirements_file}" ]; then
    build_cache_fail "requirements file is missing: ${requirements_file}"
    return $?
  fi

  py_tag="$(build_cache_python_tag "${python_bin}")"
  mode_label="unlocked"
  download_args=()
  install_args=(--root-user-action=ignore)
  if build_cache_bool "${locked_mode}"; then
    mode_label="locked"
    download_args=(--require-hashes --only-binary=:all:)
    install_args+=(--require-hashes --only-binary=:all:)
  fi

  digest="$(build_cache_requirement_digest "${requirements_file}")"
  wheelhouse="${MCP_BUILD_PIP_WHEELHOUSE_ROOT:-/var/cache/buildkit/pip-wheelhouse}/${section_name}-${mode_label}-${py_tag}-${digest}"

  if [ ! -f "${wheelhouse}/.complete" ] || build_cache_bool "${MCP_REFRESH_BUILD_DOWNLOAD_CACHE:-false}"; then
    if build_cache_bool "${MCP_BUILD_OFFLINE:-false}"; then
      build_cache_fail "missing required cached pip wheelhouse for ${section_name} at ${wheelhouse} while MCP_BUILD_OFFLINE=true"
      return $?
    fi
    tmp_wheelhouse="${wheelhouse}.tmp.$$"
    rm -rf "${tmp_wheelhouse}"
    mkdir -p "${tmp_wheelhouse}"
    "${python_bin}" -m pip download "${download_args[@]}" -r "${requirements_file}" -d "${tmp_wheelhouse}"
    touch "${tmp_wheelhouse}/.complete"
    rm -rf "${wheelhouse}"
    mv "${tmp_wheelhouse}" "${wheelhouse}"
  else
    echo "Using cached pip wheelhouse for ${section_name}: ${wheelhouse}"
  fi

  "${python_bin}" -m pip install "${install_args[@]}" --no-index --find-links "${wheelhouse}" -r "${requirements_file}"
}
