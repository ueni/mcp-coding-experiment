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
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir \
    black \
    isort \
    mypy \
    pyupgrade \
    ruff

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
    curl -fsSLo /tmp/ollama.tgz "https://ollama.com/download/ollama-linux-${ollama_arch}.tgz?version=${OLLAMA_VERSION}"; \
    echo "${ollama_sha256}  /tmp/ollama.tgz" | sha256sum -c -; \
    tar -xzf /tmp/ollama.tgz -C /usr/local; \
    rm -f /tmp/ollama.tgz

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /bin/bash app \
    && mkdir -p /models /repo/.build/cache /repo/.build/reports /opt/codebase-tooling/defaults \
    && chown -R app:app /app /models /home/app

COPY --chown=app:app server.py ./
COPY --chown=app:app entrypoint.sh ./
COPY --chown=app:app defaults/ /opt/codebase-tooling/defaults/
RUN chmod 0755 /app/entrypoint.sh

RUN mkdir -p /home/app/.vscode-server/extensions \
    && ln -sfn /opt/codebase-tooling/defaults/extensions/local.mcp-inline-autocomplete-0.0.1 /home/app/.vscode-server/extensions/local.mcp-inline-autocomplete-0.0.1 \
    && chown -R app:app /opt/codebase-tooling /home/app/.vscode-server

EXPOSE 8000
EXPOSE 11434

USER app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/entrypoint.sh"]
