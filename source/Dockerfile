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

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
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
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    HOST_CA_CERT_FILE= \
    MCP_APPLY_REPO_DEFAULTS=false

RUN apt-get update \
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
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt
RUN pip install --no-cache-dir \
    --root-user-action=ignore \
    black==26.3.0 \
    isort==8.0.1 \
    mypy==1.18.2 \
    pyupgrade==3.21.2 \
    ruff==0.15.5

RUN python -m venv /opt/codebase-tooling/coding-venv \
    && /opt/codebase-tooling/coding-venv/bin/pip install --no-cache-dir \
       --root-user-action=ignore \
       pytest==8.4.2 \
       mypy==1.18.2 \
       ruff==0.15.5

RUN set -eux; \
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
    curl -fsSL "https://ollama.com/download/ollama-linux-${ollama_arch}.tgz?version=${OLLAMA_VERSION}" \
      | OLLAMA_SHA256="${ollama_sha256}" python -c 'import hashlib, io, os, sys, tarfile; data = sys.stdin.buffer.read(); expected = os.environ["OLLAMA_SHA256"]; actual = hashlib.sha256(data).hexdigest(); actual == expected or sys.exit(f"sha256 mismatch: expected {expected}, got {actual}"); tarfile.open(fileobj=io.BytesIO(data), mode="r:gz").extractall("/usr/local")'

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /bin/bash app \
    && mkdir -p /models /repo/.build/cache /repo/.build/reports /opt/codebase-tooling/defaults \
    && chown -R app:app /app /models /home/app /opt/codebase-tooling

COPY --chown=app:app server.py ./
COPY --chown=app:app entrypoint.sh ./
COPY --chown=app:app defaults/ /opt/codebase-tooling/defaults/
COPY --chown=app:app defaults/selftests/ /opt/codebase-tooling/defaults/selftests/
RUN chmod 0755 /app/entrypoint.sh

RUN mkdir -p /home/app/.vscode-server/extensions \
    && ln -sfn /opt/codebase-tooling/defaults/extensions/local.mcp-inline-autocomplete-0.0.1 /home/app/.vscode-server/extensions/local.mcp-inline-autocomplete-0.0.1 \
    && chown -R app:app /opt/codebase-tooling /home/app/.vscode-server

EXPOSE 8000
EXPOSE 11434

USER app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/entrypoint.sh"]
