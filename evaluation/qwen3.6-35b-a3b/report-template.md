<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Qwen3.6-35B-A3B Local Evaluation Report

This file is an evaluation report related to issue #1. It records the 2026-05-08 target-host probe, the verified Docker GPU runtime path, the 2026-05-09 GPU-backed target-model smoke/bounded runs, and the current remaining comparison blocker. It does **not** satisfy the full issue #1 benchmark acceptance criteria because the current-orchestrator comparison has not been executed and the bounded local model run is below the productive/default-assistant threshold. No CPU-only or orchestrator result is fabricated.

## Run metadata

- Date: 2026-05-09
- Evaluator: Builder
- Repository commit: updated from `a833c495e6a5ffac20c82770c62418e4b3653f8d` with full bounded evidence
- Target hardware observed: Lenovo ThinkPad T14 Gen1 AMD / `user-thinkpad-t14`
- Hardware deviation from Lenovo ThinkPad T14 Gen1 AMD, if any: none observed
- Power profile / AC or battery: `performance`; AC offline during the 2026-05-08 host probe; AC online during the 2026-05-09 target-model runs
- OS/kernel: `Linux user-thinkpad-t14 7.0.0-15-generic #15-Ubuntu SMP PREEMPT_DYNAMIC Wed Apr 22 16:06:43 UTC 2026 x86_64 GNU/Linux`
- Backend/runtime: official path is Docker/devcontainer using `source/Dockerfile`; the image installs Ollama `0.18.2` plus Vulkan/Mesa tooling and `.devcontainer/devcontainer.json` passes `/dev/dri` with `OLLAMA_VULKAN=1`
- Model source, revision, quantization, checksum: `unsloth/Qwen3.6-35B-A3B-GGUF`, `Qwen3.6-35B-A3B-UD-IQ1_M.gguf`, size `10047749088` bytes, SHA256 `0dc2488c89d916c5599f7c03a286cd8f37a6a75a02bc13caf41c6bac26d70c9e`
- Reference comparison implementation: current orchestrator implementation in this repository; not executed because no checked-in comparable latency/token harness currently maps the scenario manifest to the orchestrator with the same measurement schema
- Detailed evidence: `evaluation/qwen3.6-35b-a3b/host-gpu-probe-2026-05-08.md`, `evaluation/qwen3.6-35b-a3b/docker-gpu-runtime-2026-05-08.md`, `evaluation/qwen3.6-35b-a3b/target-model-acquisition-attempt-2026-05-09.md`, `evaluation/qwen3.6-35b-a3b/target-model-smoke-2026-05-09.md`, `evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-verifier-bounded-2026-05-09.json`, and `evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-full-2026-05-09.json`

## Setup and startup

- Model acquisition command(s): completed before this run; selected practical GGUF file exists locally:
  - `Qwen3.6-35B-A3B-UD-IQ1_M.gguf`: 10,047,749,088 bytes (~9.4 GiB), SHA256 `0dc2488c89d916c5599f7c03a286cd8f37a6a75a02bc13caf41c6bac26d70c9e`
- Server startup command shape:

  ```bash
  render_gid=$(stat -c '%g' /dev/dri/renderD* | head -n1)
  kfd_gid=$(stat -c '%g' /dev/kfd 2>/dev/null || echo "$render_gid")
  docker run --rm \
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
    codebase-tooling-mcp:qwen36-eval bash -lc 'ollama serve ...; ollama create ...; python3 /repo/evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py ...'
  ```

- Startup/runtime evidence: full bounded run elapsed wall clock `2:34.21`; Ollama runner startup `10.55` seconds; first scenario included model load and measured first token `17.196`s.
- Disk footprint: selected GGUF is 10,047,749,088 bytes; no model weights are committed. The Qwen3.6-35B-A3B weights remain local-only evidence and are not committed.
- Notes: CPU-only target-model evidence (`offloaded 0/41`, 5.584 tokens/sec) is retained only as historical failed setup context. Current target-model results are GPU-backed with Vulkan/RADV and `offloaded 41/41 layers to GPU`.

## Aggregate results

| Backend | Scenarios completed | Median first-token latency (s) | Median end-to-end latency (s) | Sustained tokens/sec | Peak RAM (MB) | GPU/VRAM | Overall quality |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| qwen3.6-35b-a3b-local | 7/7 bounded | 3.307 | 13.337 | 8.056 | not measured by harness | Vulkan/RADV on AMD Radeon Graphics; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan; total memory 11.1 GiB | mixed: 4 pass, 2 partial, 1 fail under 80-token cap |
| current-orchestrator | 0/7 | not measured | not measured | not measured | not measured | Docker/runtime available | not evaluated; comparable measurement harness missing |

## Scenario results

| Scenario ID | Backend | First token (s) | End-to-end (s) | Input tokens | Output tokens | Tokens/sec | Resources | Verdict | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| embedded-c-review-001 | qwen3.6-35b-a3b-local | 17.196 | 27.320 | 145 | 80 | 7.983 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | partial | Bounded `--num-predict 80`; see JSON output preview |
| bash-hardening-001 | qwen3.6-35b-a3b-local | 2.608 | 12.621 | 57 | 80 | 8.056 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | pass | Bounded `--num-predict 80`; see JSON output preview |
| python-refactor-001 | qwen3.6-35b-a3b-local | 3.298 | 13.285 | 77 | 80 | 8.078 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | pass | Bounded `--num-predict 80`; see JSON output preview |
| javascript-async-001 | qwen3.6-35b-a3b-local | 3.223 | 13.234 | 69 | 80 | 8.062 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | pass | Bounded `--num-predict 80`; see JSON output preview |
| debug-review-001 | qwen3.6-35b-a3b-local | 3.380 | 13.421 | 77 | 80 | 8.040 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | partial | Bounded `--num-predict 80`; see JSON output preview |
| long-context-001 | qwen3.6-35b-a3b-local | 4.070 | 14.091 | 145 | 80 | 8.057 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | pass | Bounded `--num-predict 80`; see JSON output preview |
| structured-json-001 | qwen3.6-35b-a3b-local | 3.307 | 13.337 | 78 | 80 | 8.048 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | fail | Bounded `--num-predict 80`; see JSON output preview |
| all scenarios | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Docker/runtime available | blocked | Comparable current-orchestrator latency/token/quality run was not available without adding new harness code |

## Quality notes

- c_cpp_embedded / `embedded-c-review-001`: verdict `partial` at 7.983 tokens/sec; output truncated to 80 tokens for bounded local testing.
- bash / `bash-hardening-001`: verdict `pass` at 8.056 tokens/sec; output truncated to 80 tokens for bounded local testing.
- python / `python-refactor-001`: verdict `pass` at 8.078 tokens/sec; output truncated to 80 tokens for bounded local testing.
- javascript / `javascript-async-001`: verdict `pass` at 8.062 tokens/sec; output truncated to 80 tokens for bounded local testing.
- debugging_review / `debug-review-001`: verdict `partial` at 8.040 tokens/sec; output truncated to 80 tokens for bounded local testing.
- long_context / `long-context-001`: verdict `pass` at 8.057 tokens/sec; output truncated to 80 tokens for bounded local testing.
- structured_output / `structured-json-001`: verdict `fail` at 8.048 tokens/sec; output truncated to 80 tokens for bounded local testing.

The simple automated verdict function is a coarse screen, not a human code review. The strict JSON scenario failed because the model emitted non-parseable strict JSON under the bounded generation settings.

## Limitations and failure patterns

- Reproducibility: target hardware is correct, GPU is visible through Vulkan/RADV, and the target model now offloads `41/41` layers to GPU when the Docker invocation includes `/dev/kfd` and host render/KFD groups.
- Latency/throughput: the full bounded GPU-backed run measured median first-token latency `3.307`s, median end-to-end latency `13.337`s, and median `8.056` sustained output tokens/sec. This is below the ~14 tokens/sec expectation.
- Resource pressure: host has 28.6 GiB RAM. Ollama logged 9.1 GiB model weights on Vulkan, 272.8 MiB model weights on CPU, 1.6 GiB Vulkan KV cache, 98.0 MiB Vulkan compute graph, and 11.1 GiB total model memory. Free swap during full run startup was only 21.7 MiB, so longer generations/context should be treated cautiously.
- Quality failures: bounded `--num-predict 80` limits output depth; strict structured JSON failed; embedded C and debugging/review were partial.
- Comparison blocker: current-orchestrator comparison outputs are absent because no existing comparable benchmark harness was found for the scenario manifest and measurement schema.
- Operational costs: setup requires a 10 GB GGUF, GPU device/group setup, Docker image build/cache, AC power, and local disk/RAM headroom.

## Final recommendation

Choose exactly one:

- suitable for productive coding usage
- suitable only for limited/offline scenarios
- not viable

Selected recommendation: **suitable only for limited/offline scenarios**.

Rationale: The target host can run the selected Qwen3.6-35B-A3B IQ1_M GGUF through Docker/Ollama with Vulkan/RADV and full GPU layer offload, and all seven scenario categories completed in the bounded run. However, throughput is around `8.056` tokens/sec rather than the expected ~14, quality is mixed under the 80-token cap, strict JSON failed, and there is still no current-orchestrator comparison. This supports limited/offline fallback use, not replacement as the default productive coding assistant.
