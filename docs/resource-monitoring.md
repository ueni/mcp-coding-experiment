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
- waits for `/healthz`, then records startup memory with `docker stats --no-stream`;
- does not install packages, pull models, or require network access during runtime/bootstrap monitoring.

## Local smoke check

Build the validation image without runtime model or extension preloads:

```bash
docker build \
  --progress=plain \
  --build-arg OLLAMA_PRELOAD_MODELS= \
  --build-arg VSCODE_PRELOAD_EXTENSIONS= \
  --file source/Dockerfile \
  --tag codebase-tooling-mcp:test \
  source
```

Record the baseline:

```bash
scripts/monitor_runtime_resources.py --image codebase-tooling-mcp:test --json | tee docker-resource-baseline.json
```

Important fields for future comparisons:

- `image_size_bytes` / `image_size_mib`: compressed-independent Docker image size from `docker image inspect`.
- `container_rootfs_size_bytes` / `container_rootfs_size_mib`: root filesystem size from `docker container inspect --size`.
- `startup_memory_bytes` / `startup_memory_mib`: memory usage after `/healthz` succeeds, from `docker stats --no-stream`.
- `health_ok`: confirms the server reached the health-check path before memory was sampled.
- `offline_runtime_pull_allowed`: expected to remain `false` for this baseline path.

Example output shape:

```json
{
  "health_ok": true,
  "image": "codebase-tooling-mcp:test",
  "image_size_bytes": 1234567890,
  "image_size_mib": 1177.38,
  "offline_runtime_pull_allowed": false,
  "startup_memory_bytes": 73400320,
  "startup_memory_mib": 70.0
}
```

Exact values vary by architecture, Docker storage driver, and base image updates. Verifier should compare future runs on the same runner class and investigate large unexplained deltas.

## CI artifact

The `Build Devcontainer Image` workflow runs the monitor immediately after building `codebase-tooling-mcp:test`. It prints a concise summary in the job log and uploads `docker-resource-baseline.json` as the `docker-resource-baseline` artifact.
