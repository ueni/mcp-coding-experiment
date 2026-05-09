<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Target Model Smoke Run: Qwen3.6-35B-A3B

Date: 2026-05-09
Host: `user-thinkpad-t14`
PR: #2
Issue: #1
Runtime image: `codebase-tooling-mcp:qwen36-eval`
Runtime source: `source/Dockerfile`; `.devcontainer/devcontainer.json` remains the equivalent developer entrypoint for the same `/dev/dri` + `OLLAMA_VULKAN=1` runtime path.

## Result

A smallest safe target-model smoke run completed for one scenario with Vulkan GPU offload enabled after adding the host render device group to the direct Docker invocation.

The selected GGUF was present and importable, so Qwen3.6-35B-A3B weights were available for this selected quantization:

- File: `.qwen-eval-models/Qwen3.6-35B-A3B-UD-IQ1_M.gguf`
- Size: `10047749088` bytes
- SHA256: `0dc2488c89d916c5599f7c03a286cd8f37a6a75a02bc13caf41c6bac26d70c9e`
- Host GPU device: `AMD Radeon Graphics (RADV RENOIR)` via Vulkan
- Direct Docker requirement: pass `/dev/dri` and `/dev/kfd`, then add the host render group with `--group-add "$(stat -c '%g' /dev/dri/renderD128)"` so the non-root `app` user can open the device nodes.

The Docker/Ollama run completed `embedded-c-review-001` with `--num-predict 80`:

| Metric | Value |
| --- | ---: |
| Scenarios completed | 1/7 |
| First-token latency | 19.161 s |
| End-to-end latency | 29.357 s |
| Input tokens | 145 |
| Output tokens | 80 |
| Sustained output rate | 7.929 tokens/sec |
| Ollama load duration | 13.108 s |
| Prompt eval duration | 5.832 s |
| Eval duration | 10.090 s |
| Quality verdict | partial |

Machine-readable result: `evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-smoke-2026-05-09.json`.
Full captured log: `evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-smoke-2026-05-09.log`.

## GPU offload evidence

The earlier direct Docker command passed `/dev/dri` but did not add the render group to the `app` user, so Ollama only discovered CPU compute and reported `offloaded 0/41 layers to GPU`. The rerun adds the host render/KFD group ID to the container. Vulkan discovery and Ollama offload then worked:

```text
GPU0:
    deviceType         = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
    deviceName         = AMD Radeon Graphics (RADV RENOIR)
time=2026-05-09T11:30:25.199Z level=INFO source=types.go:42 msg="inference compute" id=00000000-0700-0000-0000-000000000000 filter_id="" library=Vulkan compute=0.0 name=Vulkan0 description="AMD Radeon Graphics (RADV RENOIR)" libdirs=ollama,vulkan driver=0.0 pci_id=0000:07:00.0 type=iGPU total="16.3 GiB" available="15.1 GiB"
load_backend: loaded Vulkan backend from /usr/local/lib/ollama/vulkan/libggml-vulkan.so
time=2026-05-09T11:31:16.311Z level=INFO source=ggml.go:482 msg="offloading 40 repeating layers to GPU"
time=2026-05-09T11:31:16.311Z level=INFO source=ggml.go:489 msg="offloading output layer to GPU"
time=2026-05-09T11:31:16.311Z level=INFO source=ggml.go:494 msg="offloaded 41/41 layers to GPU"
time=2026-05-09T11:31:16.311Z level=INFO source=device.go:240 msg="model weights" device=Vulkan0 size="9.1 GiB"
time=2026-05-09T11:31:16.311Z level=INFO source=device.go:251 msg="kv cache" device=Vulkan0 size="1.6 GiB"
```

## Command run

```bash
head -n 1 evaluation/qwen3.6-35b-a3b/coding-scenarios.jsonl > /tmp/qwen36-one-scenario.jsonl
render_gid="$(stat -c '%g' /dev/dri/renderD128)"
kfd_gid="$(stat -c '%g' /dev/kfd)"
/usr/bin/time -v docker run --rm \
  --security-opt=seccomp=unconfined \
  --security-opt=apparmor=unconfined \
  --device=/dev/dri \
  --device=/dev/kfd \
  --group-add "$render_gid" \
  --group-add "$kfd_gid" \
  -e OLLAMA_VULKAN=1 \
  -e OLLAMA_HOST=127.0.0.1:11434 \
  -v "$PWD:/repo" \
  -v "$PWD/.qwen-eval-models:/models:ro" \
  -v /tmp/qwen36-one-scenario.jsonl:/tmp/qwen36-one-scenario.jsonl:ro \
  codebase-tooling-mcp:qwen36-eval \
  bash -lc 'ollama serve >/tmp/ollama.log 2>&1 &
            for i in $(seq 1 120); do curl -fsS http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done
            cat > /tmp/Modelfile.qwen36 <<EOF
FROM /models/Qwen3.6-35B-A3B-UD-IQ1_M.gguf
PARAMETER temperature 0.1
PARAMETER num_ctx 4096
EOF
            ollama create qwen3.6-35b-a3b-iq1m -f /tmp/Modelfile.qwen36
            python3 /repo/evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py \
              --scenarios /tmp/qwen36-one-scenario.jsonl \
              --model qwen3.6-35b-a3b-iq1m \
              --backend qwen3.6-35b-a3b-local-docker-ollama-vulkan \
              --num-predict 80 \
              --timeout 1800 \
              --output /repo/evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-smoke-2026-05-09.json'
```

## Interpretation

- Reproducible target-model acquisition is no longer blocked for the selected IQ1_M GGUF: the file is complete and checksummed locally.
- Target-model inference starts in the repository Docker/Ollama runtime.
- The Docker/Ollama GPU offload blocker was the missing host render device group in direct `docker run` commands that bypass `/app/entrypoint.sh` group mapping.
- The rerun used Vulkan and offloaded 41/41 layers to GPU, producing 7.929 tokens/sec on the first scenario.
- This is still only a one-scenario smoke result with a `partial` quality verdict, not the full seven-scenario issue #1 benchmark acceptance result.
