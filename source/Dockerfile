# syntax=docker/dockerfile:1.7

# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

FROM python:3.12.8-slim-bookworm

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG APP_UID=1000
ARG APP_GID=1000
ARG OLLAMA_VERSION=0.12.2
ARG OLLAMA_SHA256_AMD64="abe5d1a64f813445a184bc44885bec08f8d6d8a95076ffb80ef96ed23124b64b"
ARG OLLAMA_SHA256_ARM64="45e089fd5fb31fd08d2be151bb537d3047a2be1f3437b41f35372fc5a7e5981c"
ARG OLLAMA_PRELOAD_MODELS="qwen2.5-coder:7b"
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
    CODING_DEFAULT_MODEL=qwen2.5-coder:7b \
    CONTINUE_OLLAMA_MODELS=qwen2.5-coder:7b,granite3.2:2b,phi4-mini:3.8b,phi4-mini-reasoning:3.8b,deepseek-r1:1.5b,deepscaler:1.5b,granite3.2-vision:2b,llama3.2:3b \
    OLLAMA_PRELOAD_MODELS=${OLLAMA_PRELOAD_MODELS} \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    HOST_CA_CERT_FILE= \
    MCP_APPLY_REPO_DEFAULTS=false

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        antiword \
        ca-certificates \
        curl \
        git \
        gnupg \
        jq \
        ripgrep \
        tini \
        tesseract-ocr \
    && install -m 0755 -d /etc/apt/keyrings \
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
    pip install \
    --root-user-action=ignore \
    black==26.3.0 \
    isort==8.0.1 \
    mypy==1.18.2 \
    pyupgrade==3.21.2 \
    ruff==0.15.5

RUN --mount=type=cache,target=/var/cache/buildkit/pip,sharing=locked \
    python -m venv /opt/codebase-tooling/coding-venv \
    && /opt/codebase-tooling/coding-venv/bin/pip install \
       --root-user-action=ignore \
       pytest==8.4.2 \
       mypy==1.18.2 \
       ruff==0.15.5

RUN --mount=type=cache,target=/var/cache/buildkit/ollama,sharing=locked \
    set -eux; \
    ollama_sha256=""; \
    case "$(dpkg --print-architecture)" in \
      amd64) ollama_arch="amd64"; ollama_sha256="${OLLAMA_SHA256_AMD64}" ;; \
      arm64) ollama_arch="arm64"; ollama_sha256="${OLLAMA_SHA256_ARM64}" ;; \
      *) echo "Unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    if [ -z "${ollama_sha256}" ]; then \
      echo "Missing Ollama SHA256 for architecture ${ollama_arch}." >&2; \
      exit 1; \
    fi; \
    ollama_tgz="/var/cache/buildkit/ollama/ollama-${OLLAMA_VERSION}-linux-${ollama_arch}.tgz"; \
    if [ ! -f "${ollama_tgz}" ]; then \
      curl -fL --progress-bar "https://ollama.com/download/ollama-linux-${ollama_arch}.tgz?version=${OLLAMA_VERSION}" -o "${ollama_tgz}"; \
    fi; \
    OLLAMA_SHA256="${ollama_sha256}" OLLAMA_TGZ="${ollama_tgz}" python -c 'import hashlib, os, pathlib, sys, tarfile; tgz_path = pathlib.Path(os.environ["OLLAMA_TGZ"]); expected = os.environ["OLLAMA_SHA256"]; data = tgz_path.read_bytes(); actual = hashlib.sha256(data).hexdigest(); actual == expected or sys.exit(f"sha256 mismatch: expected {expected}, got {actual}"); tarfile.open(tgz_path, mode="r:gz").extractall("/usr/local")'

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
    && mkdir -p /models /repo/.build/cache /repo/.build/reports /opt/codebase-tooling/defaults /home/app/.ollama \
    && cp -a /tmp/ollama-models/. /home/app/.ollama/models/ \
    && rm -rf /tmp/ollama-models \
    && chown -R app:app /app /models /home/app /opt/codebase-tooling

COPY --chown=app:app defaults/ /opt/codebase-tooling/defaults/

RUN set -eux; \
    mkdir -p /opt/codebase-tooling/defaults/extensions /tmp/vscode-vsix; \
    for extension_id in ${VSCODE_PRELOAD_EXTENSIONS}; do \
      publisher="${extension_id%%.*}"; \
      extension_name="${extension_id#*.}"; \
      vsix_path="/tmp/vscode-vsix/${publisher}.${extension_name}.vsix"; \
      if ! curl -fL --progress-bar "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/${publisher}/vsextensions/${extension_name}/latest/vspackage" -o "${vsix_path}"; then \
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
    rm -rf /tmp/vscode-vsix; \
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
