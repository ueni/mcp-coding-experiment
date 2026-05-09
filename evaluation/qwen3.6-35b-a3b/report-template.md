<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Qwen3.6-35B-A3B Local Evaluation Report

This file is an evaluation-artifact/runtime-readiness report related to issue #1. It records the 2026-05-08 target-host attempt, the verified Docker GPU runtime path, and the 2026-05-09 one-scenario target-model smoke run. It does **not** satisfy the full issue #1 benchmark acceptance criteria because the Qwen3.6-35B-A3B run used CPU only (`offloaded 0/41 layers to GPU`) and the full seven-scenario/comparison benchmark remains blocked. No GPU-backed target-model result is fabricated.

## Run metadata

- Date: 2026-05-09
- Evaluator: Builder
- Repository commit: `ee38164c0c57018c7760a6fe88bbf022a5423b8a` plus Verifier smoke-result commit
- Target hardware observed: Lenovo ThinkPad T14 Gen1 AMD / `user-thinkpad-t14`
- Hardware deviation from Lenovo ThinkPad T14 Gen1 AMD, if any: none observed
- Power profile / AC or battery: `performance`; AC offline during the 2026-05-08 host probe; AC online during the 2026-05-09 target-model smoke run
- OS/kernel: `Linux user-thinkpad-t14 7.0.0-15-generic #15-Ubuntu SMP PREEMPT_DYNAMIC Wed Apr 22 16:06:43 UTC 2026 x86_64 GNU/Linux`
- Backend/runtime: official path is Docker/devcontainer using `source/Dockerfile`; the image installs Ollama `0.18.2` plus Vulkan/Mesa tooling and `.devcontainer/devcontainer.json` passes `/dev/dri` with `OLLAMA_VULKAN=1`
- Model source, revision, quantization, checksum: `unsloth/Qwen3.6-35B-A3B-GGUF`, `Qwen3.6-35B-A3B-UD-IQ1_M.gguf`, size `10047749088` bytes, SHA256 `0dc2488c89d916c5599f7c03a286cd8f37a6a75a02bc13caf41c6bac26d70c9e`
- Reference comparison implementation: current orchestrator implementation in this repository; target-model runtime comparison not executed because Qwen3.6-35B-A3B weights are absent
- Detailed evidence: `evaluation/qwen3.6-35b-a3b/host-gpu-probe-2026-05-08.md`, `evaluation/qwen3.6-35b-a3b/docker-gpu-runtime-2026-05-08.md`, `evaluation/qwen3.6-35b-a3b/target-model-acquisition-attempt-2026-05-09.md`, and `evaluation/qwen3.6-35b-a3b/target-model-smoke-2026-05-09.md`

## Setup and startup

- Model acquisition command(s): completed before this Verifier run; selected practical GGUF file now exists locally:
  - `Qwen3.6-35B-A3B-UD-IQ1_M.gguf`: 10,047,749,088 bytes (~9.4 GiB), SHA256 `0dc2488c89d916c5599f7c03a286cd8f37a6a75a02bc13caf41c6bac26d70c9e`
- Server startup command shape:

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

- Startup time: not measured for the target model; Docker runtime smoke was verified separately with bundled `qwen2.5-coder:1.5b` only
- Disk footprint: no additional model/runtime footprint created; repo filesystem had 147 GiB available at probe time
- Notes: CPU fallback was intentionally not attempted because ueni clarified that GPU must be used

## Aggregate results

| Backend | Scenarios completed | Median first-token latency (s) | Median end-to-end latency (s) | Sustained tokens/sec | Peak RAM (MB) | GPU/VRAM | Overall quality |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| qwen3.6-35b-a3b-local | 1/7 smoke only | 15.501 | 29.843 | 5.584 | not measured | CPU only for target model: Ollama logged `offloaded 0/41 layers to GPU`; model weights 9.3 GiB CPU, KV cache 1.6 GiB CPU | partial on one C/C++ embedded prompt; GPU/full run blocked |
| current-orchestrator | 0/7 | not measured | not measured | not measured | not measured | Docker runtime available; comparison deferred until target model is available | not evaluated; comparison blocked |

## Scenario results

| Scenario ID | Backend | First token (s) | End-to-end (s) | Input tokens | Output tokens | Tokens/sec | Resources | Verdict | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| embedded-c-review-001 | qwen3.6-35b-a3b-local | 15.501 | 29.843 | 145 | 80 | 5.584 | CPU backend; 0/41 layers offloaded to GPU; model weights 9.3 GiB CPU, KV cache 1.6 GiB CPU | partial | One-scenario smoke only, `--num-predict 80`; does not satisfy GPU requirement |
| embedded-c-review-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Docker runtime available | blocked | Comparison deferred until target model is available |
| bash-hardening-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Docker GPU path verified; target weights absent | blocked | Same blocker |
| bash-hardening-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Docker runtime available | blocked | Same blocker |
| python-refactor-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Docker GPU path verified; target weights absent | blocked | Same blocker |
| python-refactor-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Docker runtime available | blocked | Same blocker |
| javascript-async-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Docker GPU path verified; target weights absent | blocked | Same blocker |
| javascript-async-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Docker runtime available | blocked | Same blocker |
| debug-review-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Docker GPU path verified; target weights absent | blocked | Same blocker |
| debug-review-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Docker runtime available | blocked | Same blocker |
| long-context-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Docker GPU path verified; target weights absent | blocked | Same blocker |
| long-context-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Docker runtime available | blocked | Same blocker |
| structured-json-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Docker GPU path verified; target weights absent | blocked | Same blocker |
| structured-json-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Docker runtime available | blocked | Same blocker |

## Quality notes

- C/C++ embedded: one target-model smoke output generated. It identified race/concurrency and memory-safety risks but was truncated to 80 output tokens; verdict `partial`.
- Bash: not evaluated; no model output generated.
- Python: not evaluated; no model output generated.
- JavaScript: not evaluated; no model output generated.
- Debugging/review: not evaluated; no model output generated.
- Long-context prompts: not evaluated; no model output generated.
- Structured output reliability: not evaluated; no model output generated.

## Limitations and failure patterns

- Reproducibility issues: target hardware is correct and GPU is visible through Vulkan/RADV. The official Docker runtime path is verified and the target model can be imported, but target-model inference used CPU only (`offloaded 0/41 layers to GPU`).
- Latency/throughput issues: the one-scenario CPU-only smoke measured first-token latency 15.501s, end-to-end latency 29.843s, and 5.584 sustained output tokens/sec. This is below the ~14 tokens/sec expectation and not GPU-backed.
- Resource pressure: host has 28 GiB RAM. The IQ1_M GGUF uses a 10,047,749,088-byte file; Ollama logged 9.3 GiB CPU model weights, 1.6 GiB CPU KV cache, and 11.0 GiB total memory for the target smoke. Integrated GPU memory is shared system memory, and GPU offload did not occur.
- Quality failures: no candidate or orchestrator outputs were produced, so no coding-quality comparison is possible yet.
- Operational costs: before the target evaluation can run, someone must authorize a large model pull/download or provide a model cache/weights. The machine should be on AC power for representative measurements.

## Final recommendation

Choose exactly one:

- suitable for productive coding usage
- suitable only for limited/offline scenarios
- not viable

Selected recommendation: **not viable** for the current host state.

Rationale: The target host has a usable AMD Renoir iGPU via Vulkan/RADV, and the repository Docker/devcontainer path has been verified with Ollama Vulkan using the bundled `qwen2.5-coder:1.5b` smoke model. The selected Qwen3.6-35B-A3B IQ1_M GGUF is now present, checksummed, and importable by Ollama, but the actual target-model smoke run loaded the CPU backend and offloaded `0/41` layers to GPU. The one measured scenario produced 5.584 tokens/sec, below the ~14 tokens/sec expectation, and cannot count as the required GPU-backed benchmark. Full seven-scenario target-model testing, resource characterization, and current-orchestrator comparison remain blocked until the target runtime uses GPU offload or the issue scope explicitly permits CPU-only evaluation.
