# syntax=docker/dockerfile:1.7

# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

FROM python:3.13-slim-trixie

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG APP_UID=1000
ARG APP_GID=1000
ARG OLLAMA_VERSION=0.18.2
ARG OLLAMA_PRELOAD_MODELS="qwen2.5-coder:3b,qwen2.5-coder:1.5b,granite3.3:2b,phi4-mini:3.8b,phi4-mini-reasoning:3.8b,deepseek-r1:1.5b,deepscaler:1.5b,granite3.2-vision:2b,llama3.2:1b"
ARG VSCODE_PRELOAD_EXTENSIONS="Continue.continue ms-python.python ms-python.vscode-pylance ms-azuretools.vscode-docker openai.chatgpt mhutchie.git-graph"
ARG VSCODE_PRELOAD_EXTENSIONS_STRICT=false

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
    CODING_DEFAULT_MODEL=qwen2.5-coder:3b \
    CODING_MICRO_MODEL=qwen2.5-coder:1.5b \
    CODING_MICRO_MAX_PROMPT_CHARS=600 \
    CONTINUE_OLLAMA_MODELS=qwen2.5-coder:3b,qwen2.5-coder:1.5b,granite3.3:2b,phi4-mini:3.8b,phi4-mini-reasoning:3.8b,deepseek-r1:1.5b,deepscaler:1.5b,granite3.2-vision:2b,llama3.2:1b \
    OLLAMA_ALLOW_PULL=false \
    OLLAMA_PRELOAD_MODELS=${OLLAMA_PRELOAD_MODELS} \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    HOST_CA_CERT_FILE= \
    MCP_APPLY_REPO_DEFAULTS=false

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
    apt-get update; \
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
    apt-get install -y --no-install-recommends "${packages[@]}"; \
    install -m 0755 -d /etc/apt/keyrings \
    && curl -fL --progress-bar https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        docker-buildx-plugin \
        docker-ce-cli

WORKDIR /app

COPY requirements.txt ./
RUN --mount=type=cache,target=/var/cache/buildkit/pip,sharing=locked \
    pip install --root-user-action=ignore -r requirements.txt
RUN --mount=type=cache,target=/var/cache/buildkit/pip,sharing=locked \
    python -m venv /opt/codebase-tooling/coding-venv \
    && /opt/codebase-tooling/coding-venv/bin/pip install \
       --root-user-action=ignore \
       -r requirements.txt \
    && /opt/codebase-tooling/coding-venv/bin/pip install \
       --root-user-action=ignore \
       black==26.3.0 \
       isort==8.0.1 \
       mypy==1.18.2 \
       pyupgrade==3.21.2 \
       pytest==8.4.2 \
       ruff==0.15.5 \
    && for tool in black isort mypy pyupgrade pytest ruff; do \
         ln -sf "/opt/codebase-tooling/coding-venv/bin/${tool}" "/usr/local/bin/${tool}"; \
       done

RUN --mount=type=cache,target=/var/cache/buildkit/ollama,sharing=locked \
    set -eux; \
    case "$(dpkg --print-architecture)" in \
      amd64) ollama_arch="amd64" ;; \
      arm64) ollama_arch="arm64" ;; \
      *) echo "Unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    ver_param="${OLLAMA_VERSION:+?version=${OLLAMA_VERSION}}"; \
    archive_ext=""; \
    download_url=""; \
    if curl --fail --silent --head --location "https://ollama.com/download/ollama-linux-${ollama_arch}.tar.zst${ver_param}" >/dev/null 2>&1; then \
      archive_ext="tar.zst"; \
      download_url="https://ollama.com/download/ollama-linux-${ollama_arch}.tar.zst${ver_param}"; \
    elif curl --fail --silent --head --location "https://ollama.com/download/ollama-linux-${ollama_arch}.tgz${ver_param}" >/dev/null 2>&1; then \
      archive_ext="tgz"; \
      download_url="https://ollama.com/download/ollama-linux-${ollama_arch}.tgz${ver_param}"; \
    elif curl --fail --silent --head --location "https://ollama.com/download/ollama-linux-${ollama_arch}.tar.zst" >/dev/null 2>&1; then \
      echo "Requested Ollama version ${OLLAMA_VERSION} is unavailable from ollama.com/download; falling back to the latest tar.zst package." >&2; \
      archive_ext="tar.zst"; \
      download_url="https://ollama.com/download/ollama-linux-${ollama_arch}.tar.zst"; \
    else \
      echo "Requested Ollama version ${OLLAMA_VERSION} is unavailable from ollama.com/download; falling back to the latest tgz package." >&2; \
      archive_ext="tgz"; \
      download_url="https://ollama.com/download/ollama-linux-${ollama_arch}.tgz"; \
    fi; \
    ollama_version_label="${OLLAMA_VERSION:-latest}"; \
    ollama_archive="/var/cache/buildkit/ollama/ollama-${ollama_version_label}-linux-${ollama_arch}.${archive_ext}"; \
    if [ ! -f "${ollama_archive}" ]; then \
      curl -fL --progress-bar "${download_url}" -o "${ollama_archive}"; \
    fi; \
    rm -rf /usr/local/lib/ollama; \
    case "${archive_ext}" in \
      tar.zst) zstd -dc "${ollama_archive}" | tar -xf - -C /usr/local ;; \
      tgz) tar -xzf "${ollama_archive}" -C /usr/local ;; \
    esac

RUN --mount=type=cache,target=/var/cache/buildkit/ollama-models,sharing=locked \
    mkdir -p /tmp/ollama-models /var/cache/buildkit/ollama-models \
    && export OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=/var/cache/buildkit/ollama-models \
    && ollama serve >/tmp/ollama-build.log 2>&1 & \
    pid=$! \
    && ready=0 \
    && for _ in $(seq 1 30); do \
         if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then ready=1; break; fi; \
         sleep 1; \
       done \
    && if [ "$ready" -ne 1 ]; then cat /tmp/ollama-build.log >&2; kill "$pid"; wait "$pid" || true; exit 1; fi \
    && IFS=',' read -ra preload_models <<<"${OLLAMA_PRELOAD_MODELS}" \
    && for model in "${preload_models[@]}"; do \
         model="$(echo "$model" | xargs)"; \
         if [ -n "$model" ]; then ollama pull "$model"; fi; \
       done \
    && rm -rf /tmp/ollama-models/* \
    && cp -a /var/cache/buildkit/ollama-models/. /tmp/ollama-models/ \
    && kill "$pid" \
    && wait "$pid" || true

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /bin/bash app \
    && echo 'app ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/app \
    && chmod 0440 /etc/sudoers.d/app \
    && visudo -cf /etc/sudoers.d/app \
    && mkdir -p /models /repo/.codebase-tooling-mcp/cache /repo/.codebase-tooling-mcp/reports /opt/codebase-tooling/defaults /opt/codebase-tooling/preloaded-ollama-models /home/app/.ollama/models \
    && cp -a /tmp/ollama-models/. /opt/codebase-tooling/preloaded-ollama-models/ \
    && cp -a /tmp/ollama-models/. /home/app/.ollama/models/ \
    && rm -rf /tmp/ollama-models \
    && chown -R app:app /app /models /home/app /opt/codebase-tooling

COPY --chown=app:app defaults/ /opt/codebase-tooling/defaults/

RUN --mount=type=cache,target=/var/cache/buildkit/vscode-vsix,sharing=locked \
    set -eux; \
    mkdir -p /opt/codebase-tooling/defaults/extensions /var/cache/buildkit/vscode-vsix; \
    for extension_id in ${VSCODE_PRELOAD_EXTENSIONS}; do \
      publisher="${extension_id%%.*}"; \
      extension_name="${extension_id#*.}"; \
      vsix_path="/var/cache/buildkit/vscode-vsix/${publisher}.${extension_name}.vsix"; \
      if [ ! -f "${vsix_path}" ] && ! curl -fL --progress-bar "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/${publisher}/vsextensions/${extension_name}/latest/vspackage" -o "${vsix_path}"; then \
        if [ "${VSCODE_PRELOAD_EXTENSIONS_STRICT}" = "true" ]; then exit 1; fi; \
        echo "Skipping VS Code extension ${extension_id}: download failed." >&2; \
        rm -f "${vsix_path}"; \
        continue; \
      fi; \
      if ! python -c "import json, shutil, sys, tempfile, zipfile; from pathlib import Path; vsix_path = Path(sys.argv[1]); extensions_root = Path(sys.argv[2]); tmp_dir = tempfile.mkdtemp(); tmp_path = Path(tmp_dir); assert zipfile.is_zipfile(vsix_path), 'downloaded file is not a zip archive'; zipfile.ZipFile(vsix_path).extractall(tmp_path); extension_dir = tmp_path / 'extension'; package = json.loads((extension_dir / 'package.json').read_text()); install_dir = extensions_root / (package['publisher'] + '.' + package['name'] + '-' + package['version']); shutil.rmtree(install_dir, ignore_errors=True); shutil.copytree(extension_dir, install_dir); shutil.rmtree(tmp_dir)" "${vsix_path}" /opt/codebase-tooling/defaults/extensions; then \
        if [ "${VSCODE_PRELOAD_EXTENSIONS_STRICT}" = "true" ]; then exit 1; fi; \
        echo "Skipping VS Code extension ${extension_id}: marketplace payload was not a valid VSIX archive." >&2; \
        rm -f "${vsix_path}"; \
        continue; \
      fi; \
    done; \
    for server_root in /home/app/.vscode-server /home/app/.vscode-server-insiders /home/app/.openvscode-server; do \
      mkdir -p "${server_root}/extensions"; \
      cp -a /opt/codebase-tooling/defaults/extensions/. "${server_root}/extensions/"; \
    done; \
    chown -R app:app /opt/codebase-tooling /home/app/.vscode-server /home/app/.vscode-server-insiders /home/app/.openvscode-server

# Keep high-churn app files in the last layers to speed iterative rebuilds.
COPY --chown=app:app server.py ./
COPY --chown=app:app entrypoint.sh ./
RUN chmod 0755 /app/entrypoint.sh
RUN ln -sf /usr/local/bin/python3 /usr/bin/python3

EXPOSE 8000
EXPOSE 2345

USER app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/entrypoint.sh"]
