<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Qwen3.6-35B-A3B Local Evaluation Report

This file is an evaluation report related to issue #1. It records the 2026-05-08 target-host probe, the verified Docker GPU runtime path, the 2026-05-09 GPU-backed target-model smoke/bounded runs, and the 2026-05-09 current-orchestrator comparison harness result. The revised artifacts satisfy the issue #1 evaluation acceptance criteria for a **limited/offline** viability recommendation, not for replacing the default/productive assistant path. The remaining comparison limitation is explicit: the current-orchestrator harness could only exercise the repository task router's non-streaming degraded fallback path in this environment, not a hosted/default-assistant production endpoint. The bounded GPU-backed run's median `8.056` sustained tokens/sec meets the revised approximately 7 sustained tokens/sec throughput threshold. No CPU-only or orchestrator result is fabricated.

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
- Reference comparison implementation: current orchestrator implementation in this repository, executed via `evaluation/qwen3.6-35b-a3b/run-current-orchestrator-eval.py` against `task_router(mode="task")` with persistence disabled; first-token latency is not exposed by this non-streaming route and token counts are approximate
- Detailed evidence: `evaluation/qwen3.6-35b-a3b/host-gpu-probe-2026-05-08.md`, `evaluation/qwen3.6-35b-a3b/docker-gpu-runtime-2026-05-08.md`, `evaluation/qwen3.6-35b-a3b/target-model-acquisition-attempt-2026-05-09.md`, `evaluation/qwen3.6-35b-a3b/target-model-smoke-2026-05-09.md`, `evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-verifier-bounded-2026-05-09.json`, `evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-full-2026-05-09.json`, `evaluation/qwen3.6-35b-a3b/results/results-current-orchestrator-2026-05-09.json`, `evaluation/qwen3.6-35b-a3b/results/results-current-orchestrator-2026-05-09.log`, and historical blocker note `evaluation/qwen3.6-35b-a3b/current-orchestrator-comparison-blocker-2026-05-09.md`

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
| current-orchestrator | 7/7 harness invocations | n/a (non-streaming task router) | 0.031 | 1022.964 estimated output tokens/sec for fallback text | 0.75 process RSS delta, not host peak | not applicable; repository task router `tool_fallback`/unavailable path | degraded fallback comparison: 1 pass, 4 partial, 1 fail, 1 blocked by model unavailable |

## Scenario results

| Scenario ID | Backend | First token (s) | End-to-end (s) | Input tokens | Output tokens | Tokens/sec | Resources | Requested output format | Verdict | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| embedded-c-review-001 | qwen3.6-35b-a3b-local | 17.196 | 27.320 | 145 | 80 | 7.983 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | Markdown review with risks plus minimal C patch/snippet | partial | Bounded `--num-predict 80`; see JSON output preview |
| bash-hardening-001 | qwen3.6-35b-a3b-local | 2.608 | 12.621 | 57 | 80 | 8.056 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | Unified diff followed by short rationale | pass | Bounded `--num-predict 80`; see JSON output preview |
| python-refactor-001 | qwen3.6-35b-a3b-local | 3.298 | 13.285 | 77 | 80 | 8.078 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | Only changed Python code blocks including pytest cases | pass | Bounded `--num-predict 80`; see JSON output preview |
| javascript-async-001 | qwen3.6-35b-a3b-local | 3.223 | 13.234 | 69 | 80 | 8.062 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | Explanation followed by corrected JavaScript implementation | pass | Bounded `--num-predict 80`; see JSON output preview |
| debug-review-001 | qwen3.6-35b-a3b-local | 3.380 | 13.421 | 77 | 80 | 8.040 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | Markdown sections for root cause, diagnostic, and fix | partial | Bounded `--num-predict 80`; see JSON output preview |
| long-context-001 | qwen3.6-35b-a3b-local | 4.070 | 14.091 | 145 | 80 | 8.057 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | Sectioned risk-ranked plan, open questions, and test strategy | pass | Bounded `--num-predict 80`; see JSON output preview |
| structured-json-001 | qwen3.6-35b-a3b-local | 3.307 | 13.337 | 78 | 80 | 8.048 | Vulkan/RADV; `offloaded 41/41 layers to GPU`; model weights 9.1 GiB Vulkan + 272.8 MiB CPU; KV cache 1.6 GiB Vulkan | Strict JSON only matching the findings/summary schema | fail | Bounded `--num-predict 80`; see JSON output preview |
| all scenarios | current-orchestrator | n/a (non-streaming) | 0.031 median | estimated | estimated | process RSS delta max 0.75 MB; no GPU | Same per-scenario requested formats copied into result JSON | degraded comparison | `7/7` task_router harness invocations; `tool_fallback`/unavailable local inference path, token counts approximate |

Each result row in the committed JSON artifacts includes `requested_output_format` copied from the canonical manifest. Output-format adherence is judged separately from semantic quality: Markdown/diff/code-block scenarios are checked against the requested organization, while `structured-json-001` must be parseable JSON without markdown fences/prose and must use the exact `findings`/`summary` schema. The structured JSON scenario failed because the output preview begins with thinking/prose before the JSON object and truncates before a complete parseable document.

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
- Latency/throughput: the full bounded GPU-backed run measured median first-token latency `3.307`s, median end-to-end latency `13.337`s, and median `8.056` sustained output tokens/sec. This meets the revised ~7 tokens/sec expectation.
- Resource pressure: host has 28.6 GiB RAM. Ollama logged 9.1 GiB model weights on Vulkan, 272.8 MiB model weights on CPU, 1.6 GiB Vulkan KV cache, 98.0 MiB Vulkan compute graph, and 11.1 GiB total model memory. Free swap during full run startup was only 21.7 MiB, so longer generations/context should be treated cautiously.
- Quality failures: bounded `--num-predict 80` limits output depth; strict structured JSON failed; embedded C and debugging/review were partial.
- Current-orchestrator comparison: `evaluation/qwen3.6-35b-a3b/run-current-orchestrator-eval.py` now records a same-manifest comparison through `task_router(mode="task")`. The route is non-streaming and does not expose tokenizer counts, so first-token latency remains `n/a` and token throughput is explicitly estimated. The run used the degraded `tool_fallback`/unavailable path, so it is useful as repository-orchestrator evidence but not as a full hosted/default-assistant quality substitute.
- Operational costs: setup requires a 10 GB GGUF, GPU device/group setup, Docker image build/cache, AC power, and local disk/RAM headroom.

## Final recommendation

Choose exactly one:

- suitable for productive coding usage
- suitable only for limited/offline scenarios
- not viable

Selected recommendation: **suitable only for limited/offline scenarios**.

Rationale: The target host can run the selected Qwen3.6-35B-A3B IQ1_M GGUF through Docker/Ollama with Vulkan/RADV and full GPU layer offload, all seven scenario categories completed in the bounded run, and median throughput is around `8.056` tokens/sec, above the revised expected ~7. However, quality is mixed under the 80-token cap, strict JSON failed, and the current-orchestrator comparison only covers the repository task router degraded fallback path rather than a full hosted/default-assistant model. This supports limited/offline fallback use, not replacement as the default productive coding assistant.
