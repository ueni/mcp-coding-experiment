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

A smallest safe target-model smoke run completed for one scenario, but it did **not** satisfy the GPU-backed benchmark requirement.

The selected GGUF was present and importable, so Qwen3.6-35B-A3B weights were available for this selected quantization:

- File: `.qwen-eval-models/Qwen3.6-35B-A3B-UD-IQ1_M.gguf`
- Size: `10047749088` bytes
- SHA256: `0dc2488c89d916c5599f7c03a286cd8f37a6a75a02bc13caf41c6bac26d70c9e`
- AC power: online during the run
- Host memory before run: 28 GiB total, 23 GiB available, 788 MiB free swap

The Docker/Ollama run completed `embedded-c-review-001` with `--num-predict 80`:

| Metric | Value |
| --- | ---: |
| Scenarios completed | 1/7 |
| First-token latency | 15.501 s |
| End-to-end latency | 29.843 s |
| Input tokens | 145 |
| Output tokens | 80 |
| Sustained output rate | 5.584 tokens/sec |
| Ollama load duration | 10.081 s |
| Prompt eval duration | 5.028 s |
| Eval duration | 14.326 s |
| Quality verdict | partial |

Machine-readable result: `evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-smoke-2026-05-09.json`.

## Critical blocker found

Although `/dev/dri` was passed and `OLLAMA_VULKAN=1` was set, Ollama did not use GPU acceleration for Qwen3.6-35B-A3B-UD-IQ1_M on this run.

Relevant Ollama log excerpts:

```text
time=2026-05-09T10:33:31.776Z level=INFO source=types.go:60 msg="inference compute" id=cpu library=cpu compute="" name=cpu description=cpu libdirs=ollama driver="" pci_id="" type="" total="28.6 GiB" available="28.5 GiB"
time=2026-05-09T10:33:52.654Z level=INFO source=sched.go:484 msg="system memory" total="28.6 GiB" free="23.1 GiB" free_swap="788.7 MiB"
time=2026-05-09T10:33:52.782Z level=INFO source=ggml.go:136 msg="" architecture=qwen35moe file_type=unknown name=Qwen3.6-35B-A3B description="" num_tensors=733 num_key_values=55
load_backend: loaded CPU backend from /usr/local/lib/ollama/libggml-cpu-haswell.so
time=2026-05-09T10:33:53.731Z level=INFO source=ggml.go:494 msg="offloaded 0/41 layers to GPU"
time=2026-05-09T10:33:53.731Z level=INFO source=device.go:245 msg="model weights" device=CPU size="9.3 GiB"
time=2026-05-09T10:33:53.731Z level=INFO source=device.go:256 msg="kv cache" device=CPU size="1.6 GiB"
time=2026-05-09T10:33:53.731Z level=INFO source=device.go:272 msg="total memory" size="11.0 GiB"
time=2026-05-09T10:34:02.143Z level=INFO source=server.go:1388 msg="llama runner started in 9.49 seconds"
[GIN] 2026/05/09 - 10:34:21 | 200 | 29.826022057s | 127.0.0.1 | POST "/api/generate"
```

Because ueni clarified that GPU must be used, this smoke run is evidence of a target-runtime blocker, not a PASS for issue #1.

## Command run

```bash
head -n 1 evaluation/qwen3.6-35b-a3b/coding-scenarios.jsonl > /tmp/qwen36-one-scenario.jsonl
/usr/bin/time -v docker run --rm \
  --security-opt=seccomp=unconfined \
  --security-opt=apparmor=unconfined \
  --device=/dev/dri \
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
              --backend qwen3.6-35b-a3b-local-docker-ollama \
              --num-predict 80 \
              --timeout 1800 \
              --output /repo/evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-smoke-2026-05-09.json'
```

## Interpretation

- Reproducible target-model acquisition is no longer blocked for the selected IQ1_M GGUF: the file is complete and checksummed locally.
- Target-model inference can start in the repository Docker/Ollama runtime.
- GPU acceleration for the target model did not activate: `offloaded 0/41 layers to GPU` and the CPU backend was loaded.
- The observed 5.584 tokens/sec on the first scenario is below the approximately 14 tokens/sec expectation and is CPU-only, so it cannot be accepted as the required GPU-backed throughput measurement.
- Full seven-scenario quality comparison and current-orchestrator comparison remain blocked until the target runtime uses GPU or the issue scope is explicitly changed to allow CPU-only evaluation.
