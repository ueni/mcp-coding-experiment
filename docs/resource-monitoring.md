<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Docker resource monitoring

Use `scripts/monitor_runtime_resources.py` to capture a repeatable baseline for the local devcontainer/runtime image.

The monitor is intentionally lightweight:

- uses only Python standard library plus the Docker CLI;
- measures an image that already exists locally;
- starts the container with `OLLAMA_ENABLED=false` and `OLLAMA_ALLOW_PULL=false`;
- waits for `/healthz`, then records RAM with `docker stats --no-stream`;
- does not install packages, pull models, or require network access during runtime/bootstrap monitoring.

## Local smoke check

Build the validation image without runtime model or extension preloads:

```bash
docker build \
  --progress=plain \
  --build-arg OLLAMA_PRELOAD_MODELS= \
  --build-arg VSCODE_PRELOAD_EXTENSIONS= \
  --build-arg INSTALL_SENTENCE_TRANSFORMERS=false \
  --file source/Dockerfile \
  --tag codebase-tooling-mcp:test \
  source
```

Record the default lightweight baseline (one sample after `/healthz`):

```bash
scripts/monitor_runtime_resources.py --image codebase-tooling-mcp:test --json | tee docker-resource-baseline.json
```

Opt in to continuous monitoring when a verifier needs RAM/VRAM visibility for the whole running period. This samples until the container exits or the configured monitor timeout is reached:

```bash
scripts/monitor_runtime_resources.py \
  --image codebase-tooling-mcp:test \
  --continuous \
  --sample-interval-seconds 2 \
  --monitor-timeout-seconds 300 \
  --json | tee docker-resource-continuous.json
```

Important fields for future comparisons:

- `image_size_bytes` / `image_size_mib`: compressed-independent Docker image size from `docker image inspect`.
- `container_rootfs_size_bytes` / `container_rootfs_size_mib`: root filesystem size from `docker container inspect --size`.
- `startup_memory_bytes` / `startup_memory_mib`: first RAM sample after `/healthz` succeeds, kept for CI-baseline compatibility.
- `peak_memory_bytes` / `peak_memory_mib`: peak sampled RAM. In default mode this matches the one-shot startup sample; with `--continuous` it is the peak across the monitored runtime.
- `peak_vram_bytes` / `peak_vram_mib`: peak sampled VRAM from local `nvidia-smi` when available.
- `vram_status`: explicit GPU/VRAM availability status. CPU-only/offline runners report an unavailable reason without failing the baseline path.
- `monitoring_mode`, `monitor_stop_reason`, and `sample_count`: whether this was one-shot or continuous and why sampling stopped.
- `health_ok`: confirms the server reached the health-check path before resources were sampled.
- `offline_runtime_pull_allowed`: expected to remain `false` for this baseline path.

Example output shape:

```json
{
  "health_ok": true,
  "image": "codebase-tooling-mcp:test",
  "image_size_bytes": 1234567890,
  "image_size_mib": 1177.38,
  "monitoring_mode": "continuous",
  "monitor_stop_reason": "timeout",
  "offline_runtime_pull_allowed": false,
  "peak_memory_bytes": 83886080,
  "peak_memory_mib": 80.0,
  "peak_vram_bytes": null,
  "peak_vram_mib": null,
  "sample_count": 150,
  "startup_memory_bytes": 73400320,
  "startup_memory_mib": 70.0,
  "vram_status": "unavailable: nvidia-smi not found"
}
```

Exact values vary by architecture, Docker storage driver, and base image updates. Verifier should compare future runs on the same runner class and investigate large unexplained deltas.

The largest removable contributor in the default image was the optional `sentence-transformers` dependency chain, which pulls in PyTorch and CUDA wheels even though the runtime default is `LOCAL_EMBED_BACKEND=hash`. Keep `INSTALL_SENTENCE_TRANSFORMERS=false` for the offline bootstrap baseline; opt in only when a sentence-transformers model is also supplied locally.

## CI artifact

The `Build Devcontainer Image` workflow runs the monitor immediately after building `codebase-tooling-mcp:test`. It prints a concise summary in the job log and uploads `docker-resource-baseline.json` as the `docker-resource-baseline` artifact.
