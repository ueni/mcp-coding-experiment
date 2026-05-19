# syntax=docker/dockerfile:1.7

# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

FROM python:3.13-slim-trixie

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG APP_UID=1000
ARG APP_GID=1000
ARG OLLAMA_VERSION=0.18.2
ARG OLLAMA_PRELOAD_MODELS="qwen2.5-coder:1.5b"
ARG OLLAMA_PULL_TIMEOUT_SECONDS=7200
ARG VSCODE_PRELOAD_EXTENSIONS="Continue.continue ms-python.python ms-python.vscode-pylance ms-azuretools.vscode-docker openai.chatgpt mhutchie.git-graph"
ARG VSCODE_PRELOAD_EXTENSIONS_STRICT=false
ARG INSTALL_SENTENCE_TRANSFORMERS=false
ARG MCP_USE_LOCKED_DEPS=false
ARG MCP_BUILD_OFFLINE=false
ARG MCP_REFRESH_BUILD_DOWNLOAD_CACHE=false
ARG RUNTIME_IMAGE_VERSION_COMPATIBILITY=0
ARG RUNTIME_IMAGE_VERSION_FEATURE=0
ARG RUNTIME_IMAGE_VERSION_BUGFIX=0
ARG RUNTIME_IMAGE_VERSION_SUFFIX=-local-build
ARG MCP_CODING_EXPERIMENT_VERSION_COMPATIBILITY=0
ARG MCP_CODING_EXPERIMENT_VERSION_FEATURE=0
ARG MCP_CODING_EXPERIMENT_VERSION_BUGFIX=0
ARG MCP_CODING_EXPERIMENT_VERSION_SUFFIX=-local-build

LABEL io.modelcontextprotocol.server.name="io.github.ueni/codebase-tooling-mcp"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PIP_CACHE_DIR=/var/cache/buildkit/pip \
    DEBIAN_FRONTEND=noninteractive \
    REPO_PATH=/repo \
    HOST=0.0.0.0 \
    PORT=8000 \
    MCP_TRANSPORT=http \
    ALLOW_MUTATIONS=false \
    RUNTIME_IMAGE_VERSION_COMPATIBILITY=${RUNTIME_IMAGE_VERSION_COMPATIBILITY} \
    RUNTIME_IMAGE_VERSION_FEATURE=${RUNTIME_IMAGE_VERSION_FEATURE} \
    RUNTIME_IMAGE_VERSION_BUGFIX=${RUNTIME_IMAGE_VERSION_BUGFIX} \
    RUNTIME_IMAGE_VERSION_SUFFIX=${RUNTIME_IMAGE_VERSION_SUFFIX} \
    MCP_CODING_EXPERIMENT_VERSION_COMPATIBILITY=${MCP_CODING_EXPERIMENT_VERSION_COMPATIBILITY} \
    MCP_CODING_EXPERIMENT_VERSION_FEATURE=${MCP_CODING_EXPERIMENT_VERSION_FEATURE} \
    MCP_CODING_EXPERIMENT_VERSION_BUGFIX=${MCP_CODING_EXPERIMENT_VERSION_BUGFIX} \
    MCP_CODING_EXPERIMENT_VERSION_SUFFIX=${MCP_CODING_EXPERIMENT_VERSION_SUFFIX} \
    MAX_READ_BYTES=262144 \
    MAX_OUTPUT_CHARS=200000 \
    ALLOW_ORIGINS=* \
    LOCAL_MODELS_DIR=/models \
    LOCAL_EMBED_BACKEND=hash \
    LOCAL_EMBED_MODEL= \
    LOCAL_EMBED_DIM=256 \
    LOCAL_INFER_BACKEND=endpoint \
    LOCAL_INFER_MODEL= \
    LOCAL_INFER_ENDPOINT=http://127.0.0.1:11434/api/generate \
    CODING_VENV_PYTHON=/opt/codebase-tooling/coding-venv/bin/python \
    CODING_DEFAULT_MODEL=qwen2.5-coder:1.5b \
    CODING_AGENT_MODEL=qwen2.5-coder:1.5b \
    CODING_MICRO_MODEL=qwen2.5-coder:1.5b \
    CODING_MICRO_MAX_PROMPT_CHARS=600 \
    OLLAMA_CONTEXT_LENGTH=8192 \
    OLLAMA_TEXT_ALIAS_SOURCE_MODEL= \
    OLLAMA_TEXT_ALIAS_MODEL= \
    CONTINUE_OLLAMA_MODELS=qwen2.5-coder:1.5b \
    OLLAMA_ALLOW_PULL=false \
    OLLAMA_PRELOAD_MODELS=${OLLAMA_PRELOAD_MODELS} \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    HOST_CA_CERT_FILE= \
    MCP_APPLY_REPO_DEFAULTS=false \
    MCP_LOCKED_DEPS_MODE=${MCP_USE_LOCKED_DEPS} \
    MCP_BUILD_OFFLINE=${MCP_BUILD_OFFLINE} \
    MCP_REFRESH_BUILD_DOWNLOAD_CACHE=${MCP_REFRESH_BUILD_DOWNLOAD_CACHE}

COPY build-download-cache.sh /usr/local/bin/build-download-cache.sh

RUN --mount=type=cache,id=codebase-tooling-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=codebase-tooling-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=cache,id=codebase-tooling-build-downloads,target=/var/cache/buildkit/downloads,sharing=locked \
    set -eux; \
    . /usr/local/bin/build-download-cache.sh; \
    rm -f /etc/apt/apt.conf.d/docker-clean; \
    apt_install_args=(); \
    if build_cache_bool "${MCP_BUILD_OFFLINE}"; then \
        apt_install_args=(--no-download); \
        find /var/lib/apt/lists -type f -name '*Packages*' -print -quit | grep -q . || { echo 'APT package lists are missing from the BuildKit cache while MCP_BUILD_OFFLINE=true.' >&2; exit 23; }; \
    else \
        apt-get update; \
    fi; \
    packages=( \
        antiword \
        ca-certificates \
        curl \
        git \
        jq \
        libvulkan1 \
        mesa-vulkan-drivers \
        ripgrep \
        sudo \
        tini \
        tesseract-ocr \
        htop \
        tree \
        vulkan-tools \
        zstd \
    ); \
    apt-get install -y --no-install-recommends "${apt_install_args[@]}" "${packages[@]}"; \
    install -m 0755 -d /etc/apt/keyrings \
    && build_cache_download /var/cache/buildkit/downloads/docker-debian-gpg.asc https://download.docker.com/linux/debian/gpg 'Docker Debian repository signing key' \
    && install -m 0644 /var/cache/buildkit/downloads/docker-debian-gpg.asc /etc/apt/keyrings/docker.asc \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list \
    && if build_cache_bool "${MCP_BUILD_OFFLINE}"; then \
         find /var/lib/apt/lists -type f -path '*download.docker.com*Packages*' -print -quit | grep -q . || { echo 'Docker APT package lists are missing from the BuildKit cache while MCP_BUILD_OFFLINE=true.' >&2; exit 23; }; \
       else \
         apt-get update; \
       fi \
    && apt-get install -y --no-install-recommends \
        "${apt_install_args[@]}" \
        docker-buildx-plugin \
        docker-ce-cli

WORKDIR /app

COPY requirements.txt requirements-embedding.txt requirements-coding-tools.txt requirements.lock requirements-embedding.lock requirements-coding-tools.lock dependency-locks.json ./
RUN if [ "${MCP_USE_LOCKED_DEPS}" = "true" ]; then \
      python -c 'exec("""\nimport hashlib\nimport json\nimport os\nfrom pathlib import Path\n\nmanifest = json.loads(Path(\"dependency-locks.json\").read_text(encoding=\"utf-8\"))\nif manifest.get(\"schema\") != \"codebase_tooling_mcp.dependency_locks.v1\":\n    raise SystemExit(\"dependency-locks.json has an unexpected schema\")\nnames = [\"runtime\", \"coding-tools\"]\nif os.environ.get(\"INSTALL_SENTENCE_TRANSFORMERS\") == \"true\":\n    names.append(\"embedding\")\nfor name in names:\n    section = manifest[\"sections\"][name]\n    input_path = Path(section[\"input\"]).name\n    lock_path = Path(section[\"lock\"]).name\n    for path, expected, label in (\n        (input_path, section[\"input_digest\"], \"input\"),\n        (lock_path, section[\"lock_digest\"], \"lock\"),\n    ):\n        actual = \"sha256:\" + hashlib.sha256(Path(path).read_bytes()).hexdigest()\n        if actual != expected:\n            raise SystemExit(\n                f\"stale dependency lock section={name} {label}={path}: \"\n                f\"expected {expected}, got {actual}; run scripts/dependency_lock.py refresh\"\n            )\n""")'; \
    fi
RUN --mount=type=cache,id=codebase-tooling-pip,target=/var/cache/buildkit/pip,sharing=locked \
    --mount=type=cache,id=codebase-tooling-pip-wheelhouse,target=/var/cache/buildkit/pip-wheelhouse,sharing=locked \
    . /usr/local/bin/build-download-cache.sh; \
    if [ "${MCP_USE_LOCKED_DEPS}" = "true" ]; then \
      build_cache_pip_install python runtime requirements.lock true; \
    else \
      build_cache_pip_install python runtime requirements.txt false; \
    fi \
    && if [ "${INSTALL_SENTENCE_TRANSFORMERS}" = "true" ]; then \
         if [ "${MCP_USE_LOCKED_DEPS}" = "true" ]; then \
           build_cache_pip_install python embedding requirements-embedding.lock true; \
         else \
           build_cache_pip_install python embedding requirements-embedding.txt false; \
         fi; \
       fi
RUN --mount=type=cache,id=codebase-tooling-pip,target=/var/cache/buildkit/pip,sharing=locked \
    --mount=type=cache,id=codebase-tooling-pip-wheelhouse,target=/var/cache/buildkit/pip-wheelhouse,sharing=locked \
    . /usr/local/bin/build-download-cache.sh; \
    python -m venv /opt/codebase-tooling/coding-venv \
    && if [ "${MCP_USE_LOCKED_DEPS}" = "true" ]; then \
         build_cache_pip_install /opt/codebase-tooling/coding-venv/bin/python coding-runtime requirements.lock true; \
       else \
         build_cache_pip_install /opt/codebase-tooling/coding-venv/bin/python coding-runtime requirements.txt false; \
       fi \
    && if [ "${INSTALL_SENTENCE_TRANSFORMERS}" = "true" ]; then \
         if [ "${MCP_USE_LOCKED_DEPS}" = "true" ]; then \
           build_cache_pip_install /opt/codebase-tooling/coding-venv/bin/python coding-embedding requirements-embedding.lock true; \
         else \
           build_cache_pip_install /opt/codebase-tooling/coding-venv/bin/python coding-embedding requirements-embedding.txt false; \
         fi; \
       fi \
    && if [ "${MCP_USE_LOCKED_DEPS}" = "true" ]; then \
         build_cache_pip_install /opt/codebase-tooling/coding-venv/bin/python coding-tools requirements-coding-tools.lock true; \
       else \
         build_cache_pip_install /opt/codebase-tooling/coding-venv/bin/python coding-tools requirements-coding-tools.txt false; \
       fi \
    && for tool in black isort mypy pyupgrade pytest ruff; do \
         ln -sf "/opt/codebase-tooling/coding-venv/bin/${tool}" "/usr/local/bin/${tool}"; \
       done

RUN --mount=type=cache,id=codebase-tooling-ollama-binary,target=/var/cache/buildkit/ollama,sharing=locked \
    set -eux; \
    . /usr/local/bin/build-download-cache.sh; \
    case "$(dpkg --print-architecture)" in \
      amd64) ollama_arch="amd64" ;; \
      arm64) ollama_arch="arm64" ;; \
      *) echo "Unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    ollama_version_label="${OLLAMA_VERSION:-latest}"; \
    ver_param="${OLLAMA_VERSION:+?version=${OLLAMA_VERSION}}"; \
    archive_ext=""; \
    download_url=""; \
    for candidate_ext in tar.zst tgz; do \
      candidate_archive="/var/cache/buildkit/ollama/ollama-${ollama_version_label}-linux-${ollama_arch}.${candidate_ext}"; \
      if [ -f "${candidate_archive}" ] && ! build_cache_bool "${MCP_REFRESH_BUILD_DOWNLOAD_CACHE}"; then \
        case "${candidate_ext}" in \
          tar.zst) zstd -t "${candidate_archive}" || rm -f "${candidate_archive}" ;; \
          tgz) tar -tzf "${candidate_archive}" >/dev/null || rm -f "${candidate_archive}" ;; \
        esac; \
        if [ -f "${candidate_archive}" ]; then \
          archive_ext="${candidate_ext}"; \
          ollama_archive="${candidate_archive}"; \
          break; \
        fi; \
      fi; \
    done; \
    if [ -z "${archive_ext}" ]; then \
      if build_cache_bool "${MCP_BUILD_OFFLINE}"; then \
        echo "Ollama binary is missing from the BuildKit cache while MCP_BUILD_OFFLINE=true." >&2; \
        exit 23; \
      fi; \
      if build_cache_url_exists "https://ollama.com/download/ollama-linux-${ollama_arch}.tar.zst${ver_param}"; then \
        archive_ext="tar.zst"; \
        download_url="https://ollama.com/download/ollama-linux-${ollama_arch}.tar.zst${ver_param}"; \
      elif build_cache_url_exists "https://ollama.com/download/ollama-linux-${ollama_arch}.tgz${ver_param}"; then \
        archive_ext="tgz"; \
        download_url="https://ollama.com/download/ollama-linux-${ollama_arch}.tgz${ver_param}"; \
      elif build_cache_url_exists "https://ollama.com/download/ollama-linux-${ollama_arch}.tar.zst"; then \
        echo "Requested Ollama version ${OLLAMA_VERSION} is unavailable from ollama.com/download; falling back to the latest tar.zst package." >&2; \
        archive_ext="tar.zst"; \
        download_url="https://ollama.com/download/ollama-linux-${ollama_arch}.tar.zst"; \
      else \
        echo "Requested Ollama version ${OLLAMA_VERSION} is unavailable from ollama.com/download; falling back to the latest tgz package." >&2; \
        archive_ext="tgz"; \
        download_url="https://ollama.com/download/ollama-linux-${ollama_arch}.tgz"; \
      fi; \
      ollama_archive="/var/cache/buildkit/ollama/ollama-${ollama_version_label}-linux-${ollama_arch}.${archive_ext}"; \
      build_cache_download "${ollama_archive}" "${download_url}" "Ollama ${ollama_version_label} ${ollama_arch} binary"; \
      case "${archive_ext}" in \
        tar.zst) zstd -t "${ollama_archive}" ;; \
        tgz) tar -tzf "${ollama_archive}" >/dev/null ;; \
      esac; \
    else \
      echo "Using cached Ollama ${ollama_version_label} ${ollama_arch} binary: ${ollama_archive}"; \
    fi; \
    rm -rf /usr/local/lib/ollama; \
    case "${archive_ext}" in \
      tar.zst) zstd -dc "${ollama_archive}" | tar -xf - -C /usr/local ;; \
      tgz) tar -xzf "${ollama_archive}" -C /usr/local ;; \
    esac
RUN --mount=type=cache,id=codebase-tooling-ollama-models,target=/var/cache/buildkit/ollama-models,sharing=locked \
    set -eux; \
    . /usr/local/bin/build-download-cache.sh; \
    mkdir -p /tmp/ollama-models; \
    if [ -z "$(echo "${OLLAMA_PRELOAD_MODELS}" | xargs)" ]; then \
      echo "Skipping Ollama model preload because OLLAMA_PRELOAD_MODELS is empty."; \
      exit 0; \
    fi; \
    mkdir -p /var/cache/buildkit/ollama-models; \
    export OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=/var/cache/buildkit/ollama-models; \
    ollama serve >/tmp/ollama-build.log 2>&1 & \
    pid=$!; \
    ready=0; \
    for _ in $(seq 1 30); do \
      if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then ready=1; break; fi; \
      sleep 1; \
    done; \
    if [ "$ready" -ne 1 ]; then cat /tmp/ollama-build.log >&2; kill "$pid"; wait "$pid" || true; exit 1; fi; \
    IFS=',' read -ra preload_models <<<"${OLLAMA_PRELOAD_MODELS}"; \
    for model in "${preload_models[@]}"; do \
      model="$(echo "$model" | xargs)"; \
      if [ -n "$model" ]; then \
        if ollama show "$model" >/dev/null 2>&1; then \
          echo "Ollama model ${model} is already present in the BuildKit cache; skipping pull."; \
          continue; \
        fi; \
        if build_cache_bool "${MCP_BUILD_OFFLINE}"; then \
          echo "Ollama model ${model} is missing from the BuildKit cache while MCP_BUILD_OFFLINE=true." >&2; \
          kill "$pid" || true; \
          wait "$pid" || true; \
          exit 23; \
        fi; \
        pull_log="/tmp/ollama-pull-$(echo "$model" | tr -c 'A-Za-z0-9_.-' '_').log"; \
        echo "Pulling Ollama model ${model}"; \
        timeout "${OLLAMA_PULL_TIMEOUT_SECONDS}" ollama pull "$model" >"${pull_log}" 2>&1 || { \
          status="$?"; \
          cat "${pull_log}" >&2 || true; \
          echo "Failed to pull Ollama model ${model} (exit ${status}; timeout ${OLLAMA_PULL_TIMEOUT_SECONDS}s)." >&2; \
          kill "$pid" || true; \
          wait "$pid" || true; \
          exit "$status"; \
        }; \
        ollama show "$model" >/dev/null 2>&1; \
      fi; \
    done; \
    rm -rf /tmp/ollama-models/*; \
    cp -a /var/cache/buildkit/ollama-models/. /tmp/ollama-models/; \
    kill "$pid"; \
    wait "$pid" || true

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /bin/bash app \
    && echo 'app ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/app \
    && chmod 0440 /etc/sudoers.d/app \
    && visudo -cf /etc/sudoers.d/app \
    && mkdir -p /models /repo/.codebase-tooling-mcp/cache /repo/.codebase-tooling-mcp/reports /opt/codebase-tooling/defaults /opt/codebase-tooling/preloaded-ollama-models /home/app/.ollama \
    && cp -a /tmp/ollama-models/. /opt/codebase-tooling/preloaded-ollama-models/ \
    && rm -rf /tmp/ollama-models \
    && ln -sfn /opt/codebase-tooling/preloaded-ollama-models /home/app/.ollama/models \
    && chown -R app:app /app /models /repo /home/app /opt/codebase-tooling/defaults /opt/codebase-tooling/preloaded-ollama-models

RUN --mount=type=cache,id=codebase-tooling-vscode-vsix,target=/var/cache/buildkit/vscode-vsix,sharing=locked \
    set -eux; \
    . /usr/local/bin/build-download-cache.sh; \
    mkdir -p /opt/codebase-tooling/defaults/extensions /var/cache/buildkit/vscode-vsix; \
    if [ -z "$(echo "${VSCODE_PRELOAD_EXTENSIONS}" | xargs)" ]; then \
      echo "Skipping VS Code extension preload because VSCODE_PRELOAD_EXTENSIONS is empty."; \
    else \
      for extension_ref in ${VSCODE_PRELOAD_EXTENSIONS}; do \
        extension_version=""; \
        extension_id="${extension_ref}"; \
        if [[ "${extension_ref}" == *@* ]]; then \
          extension_version="${extension_ref##*@}"; \
          extension_id="${extension_ref%@*}"; \
        fi; \
        publisher="${extension_id%%.*}"; \
        extension_name="${extension_id#*.}"; \
        version_label="${extension_version:-latest}"; \
        vsix_path="/var/cache/buildkit/vscode-vsix/${publisher}.${extension_name}.${version_label}.vsix"; \
        if [ -f "${vsix_path}" ] && ! python -c "import sys, zipfile; assert zipfile.is_zipfile(sys.argv[1]), 'cached file is not a zip archive'" "${vsix_path}"; then \
          echo "Discarding corrupt cached VS Code extension ${extension_ref}." >&2; \
          rm -f "${vsix_path}"; \
        fi; \
        if [ ! -f "${vsix_path}" ] || build_cache_bool "${MCP_REFRESH_BUILD_DOWNLOAD_CACHE}"; then \
          if [ -n "${extension_version}" ]; then \
            vsix_url="https://marketplace.visualstudio.com/_apis/public/gallery/publishers/${publisher}/vsextensions/${extension_name}/${extension_version}/vspackage"; \
          else \
            vsix_url="https://marketplace.visualstudio.com/_apis/public/gallery/publishers/${publisher}/vsextensions/${extension_name}/latest/vspackage"; \
          fi; \
          if ! build_cache_download "${vsix_path}" "${vsix_url}" "VS Code extension ${extension_ref}"; then \
            if build_cache_bool "${MCP_BUILD_OFFLINE}" || [ "${VSCODE_PRELOAD_EXTENSIONS_STRICT}" = "true" ]; then exit 1; fi; \
            echo "Skipping VS Code extension ${extension_ref}: download failed." >&2; \
            rm -f "${vsix_path}"; \
            continue; \
          fi; \
          if ! python -c "import sys, zipfile; assert zipfile.is_zipfile(sys.argv[1]), 'downloaded file is not a zip archive'" "${vsix_path}"; then \
            if build_cache_bool "${MCP_BUILD_OFFLINE}" || [ "${VSCODE_PRELOAD_EXTENSIONS_STRICT}" = "true" ]; then exit 1; fi; \
            echo "Skipping VS Code extension ${extension_ref}: marketplace payload was not a valid VSIX archive." >&2; \
            rm -f "${vsix_path}"; \
            continue; \
          fi; \
        else \
          echo "Using cached VS Code extension ${extension_ref}: ${vsix_path}."; \
        fi; \
        if ! python -c "import json, shutil, sys, tempfile, zipfile; from pathlib import Path; vsix_path = Path(sys.argv[1]); extensions_root = Path(sys.argv[2]); tmp_dir = tempfile.mkdtemp(); tmp_path = Path(tmp_dir); assert zipfile.is_zipfile(vsix_path), 'downloaded file is not a zip archive'; zipfile.ZipFile(vsix_path).extractall(tmp_path); extension_dir = tmp_path / 'extension'; package = json.loads((extension_dir / 'package.json').read_text()); install_dir = extensions_root / (package['publisher'] + '.' + package['name'] + '-' + package['version']); shutil.rmtree(install_dir, ignore_errors=True); shutil.copytree(extension_dir, install_dir); shutil.rmtree(tmp_dir)" "${vsix_path}" /opt/codebase-tooling/defaults/extensions; then \
          if build_cache_bool "${MCP_BUILD_OFFLINE}" || [ "${VSCODE_PRELOAD_EXTENSIONS_STRICT}" = "true" ]; then exit 1; fi; \
          echo "Skipping VS Code extension ${extension_ref}: cached VSIX archive could not be installed." >&2; \
          rm -f "${vsix_path}"; \
          continue; \
        fi; \
      done; \
    fi; \
    for server_root in /home/app/.vscode-server /home/app/.vscode-server-insiders /home/app/.openvscode-server; do \
      mkdir -p "${server_root}"; \
      ln -sfn /opt/codebase-tooling/defaults/extensions "${server_root}/extensions"; \
    done; \
    chown -R app:app /opt/codebase-tooling/defaults/extensions /home/app/.vscode-server /home/app/.vscode-server-insiders /home/app/.openvscode-server

COPY --chown=app:app defaults/ /opt/codebase-tooling/defaults/

# Keep high-churn app files in the last layers to speed iterative rebuilds.
COPY --chown=app:app server.py ./
COPY --chown=app:app tool_output_schemas.py tool_catalog_integrity.py tool_catalog_baseline.json version_metadata.py ./source/
COPY --chown=app:app entrypoint.sh ./
RUN chmod 0755 /app/entrypoint.sh
RUN ln -sf /usr/local/bin/python3 /usr/bin/python3

EXPOSE 8000
EXPOSE 2345

USER app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/entrypoint.sh"]
