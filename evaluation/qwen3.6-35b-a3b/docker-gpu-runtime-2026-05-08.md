<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Docker GPU Runtime Evidence for Qwen3.6-35B-A3B Evaluation

Date: 2026-05-08
Host: `user-thinkpad-t14`
Target: Lenovo ThinkPad T14 Gen1 AMD
Official runtime path: `source/Dockerfile` via `.devcontainer/devcontainer.json` or an equivalent `docker run` using the same image, device pass-through, and environment.

## Runtime definition

The evaluation runtime is the repository Docker image built from `source/Dockerfile`.
The VS Code Dev Container entry point is `.devcontainer/devcontainer.json`.

Verified configuration in this branch:

- `source/Dockerfile` installs Ollama `0.18.2` through `ARG OLLAMA_VERSION=0.18.2`.
- `source/Dockerfile` installs Vulkan/Mesa userspace packages: `libvulkan1`, `mesa-vulkan-drivers`, and `vulkan-tools`.
- `.devcontainer/devcontainer.json` passes the AMD DRM devices with `--device=/dev/dri`.
- `.devcontainer/devcontainer.json` sets `OLLAMA_VULKAN=1`.
- `.devcontainer/devcontainer.json` exposes the bundled Ollama API on port `2345` and points `LOCAL_INFER_ENDPOINT` at `http://127.0.0.1:2345/api/generate`.

## Exact Docker command shape

The devcontainer is the canonical developer path. For a non-VS Code reproduction, use the same Dockerfile and runtime flags:

```bash
docker build -t codebase-tooling-mcp:qwen-eval ./source

docker run --rm \
  --security-opt=seccomp=unconfined \
  --security-opt=apparmor=unconfined \
  --device=/dev/dri \
  -p 8000:8000 \
  -p 2345:2345 \
  -e MCP_TRANSPORT=http \
  -e ALLOW_MUTATIONS=true \
  -e OLLAMA_VULKAN=1 \
  -e OLLAMA_HOST=0.0.0.0:2345 \
  -e OLLAMA_FALLBACK_HOST=0.0.0.0:2345 \
  -e LOCAL_INFER_ENDPOINT=http://127.0.0.1:2345/api/generate \
  -v "$PWD:/repo" \
  codebase-tooling-mcp:qwen-eval
```

Inside the running container, record GPU/runtime visibility before benchmark execution:

```bash
vulkaninfo --summary
ollama --version
curl -fsS http://127.0.0.1:2345/api/tags | jq .
```

Then run the target model only after weights are authorized/provided:

```bash
curl -fsS http://127.0.0.1:2345/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"<authorized-qwen3.6-35b-a3b-model>","prompt":"Return one sentence confirming the runtime is ready.","stream":false}'
```

## Verifier Docker GPU probe

Verifier confirmed the Docker/devcontainer runtime path on the target AMD host:

- GPU detected as `AMD Radeon Graphics (RADV RENOIR)`.
- Ollama ran with Vulkan enabled in Docker.
- A smoke generation using the already available `qwen2.5-coder:1.5b` model completed.
- Ollama offloaded `29/29` layers to GPU for that smoke model.

This smoke result validates the Docker GPU/Ollama runtime only. It is not a Qwen3.6-35B-A3B benchmark result and must not be used for target-model quality, latency, or throughput conclusions.

## Remaining blocker

The Qwen3.6-35B-A3B weights are still absent from the runtime. The target benchmark remains blocked until ueni explicitly authorizes a specific large model pull/download or provides the model weights/cache.

No large target-model weights were downloaded as part of this evidence update.
