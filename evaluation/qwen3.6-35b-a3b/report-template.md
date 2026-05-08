<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Qwen3.6-35B-A3B Local Evaluation Report

This file is the canonical report for issue #1. It records the 2026-05-08 target-host attempt, the verified Docker GPU runtime path, and the exact remaining blocker. No target-model quality or throughput numbers are fabricated.

## Run metadata

- Date: 2026-05-08
- Evaluator: Builder
- Repository commit: update this field to the exact benchmark commit before a full rerun
- Target hardware observed: Lenovo ThinkPad T14 Gen1 AMD / `user-thinkpad-t14`
- Hardware deviation from Lenovo ThinkPad T14 Gen1 AMD, if any: none observed
- Power profile / AC or battery: `performance`; AC offline; battery discharging at 64% during the host probe
- OS/kernel: `Linux user-thinkpad-t14 7.0.0-15-generic #15-Ubuntu SMP PREEMPT_DYNAMIC Wed Apr 22 16:06:43 UTC 2026 x86_64 GNU/Linux`
- Backend/runtime: official path is Docker/devcontainer using `source/Dockerfile`; the image installs Ollama `0.18.2` plus Vulkan/Mesa tooling and `.devcontainer/devcontainer.json` passes `/dev/dri` with `OLLAMA_VULKAN=1`
- Model source, revision, quantization, checksum: not downloaded; candidate checked via Hugging Face API as `unsloth/Qwen3.6-35B-A3B-GGUF` at SHA `a483e9e6cbd595906af30beda3187c2663a1118c`, gated `false`; no local checksum because no weight file was fetched
- Reference comparison implementation: current orchestrator implementation in this repository; target-model runtime comparison not executed because Qwen3.6-35B-A3B weights are absent
- Detailed evidence: `evaluation/qwen3.6-35b-a3b/host-gpu-probe-2026-05-08.md`, `evaluation/qwen3.6-35b-a3b/docker-gpu-runtime-2026-05-08.md`, and `evaluation/qwen3.6-35b-a3b/model-authorization-request-2026-05-09.md`

## Setup and startup

- Model acquisition command(s): none executed. The model download was stopped before transfer because practical GGUF files are large:
  - `Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf`: 10,756,586,464 bytes (~10.0 GiB)
  - `Qwen3.6-35B-A3B-UD-IQ2_M.gguf`: 11,522,702,304 bytes (~10.7 GiB)
  - `Qwen3.6-35B-A3B-UD-IQ4_XS.gguf`: 17,730,509,792 bytes (~16.5 GiB)
  - `Qwen3.6-35B-A3B-MXFP4_MOE.gguf`: 21,706,144,736 bytes (~20.2 GiB)
  - `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`: 22,134,528,992 bytes (~20.6 GiB)
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
| qwen3.6-35b-a3b-local | 0/7 | not measured | not measured | not measured | not measured | Docker GPU path verified with Vulkan/RADV + Ollama, but target weights absent | not evaluated; run blocked |
| current-orchestrator | 0/7 | not measured | not measured | not measured | not measured | Docker runtime available; comparison deferred until target model is available | not evaluated; comparison blocked |

## Scenario results

| Scenario ID | Backend | First token (s) | End-to-end (s) | Input tokens | Output tokens | Tokens/sec | Resources | Verdict | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| embedded-c-review-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Docker GPU path verified; target weights absent | blocked | No local Qwen3.6-35B-A3B weights |
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

- C/C++ embedded: not evaluated; no model output generated.
- Bash: not evaluated; no model output generated.
- Python: not evaluated; no model output generated.
- JavaScript: not evaluated; no model output generated.
- Debugging/review: not evaluated; no model output generated.
- Long-context prompts: not evaluated; no model output generated.
- Structured output reliability: not evaluated; no model output generated.

## Limitations and failure patterns

- Reproducibility issues: target hardware is correct and GPU is visible through Vulkan/RADV. The official Docker runtime path is verified, but target-model inference reproducibility is blocked by missing Qwen3.6-35B-A3B weights.
- Latency/throughput issues: no startup, first-token, end-to-end, or sustained tokens/sec measurements are available. The ~14 tokens/sec target remains unverified.
- Resource pressure: host has 28 GiB RAM and 147 GiB free disk. The smallest practical GGUF checked is ~10.0 GiB; better-quality quants are ~16.5-20.6 GiB. Integrated GPU memory is shared system memory, so real runs may create significant RAM and thermal pressure.
- Quality failures: no candidate or orchestrator outputs were produced, so no coding-quality comparison is possible yet.
- Operational costs: before the target evaluation can run, someone must authorize a large model pull/download or provide a model cache/weights. The machine should be on AC power for representative measurements.

## Final recommendation

Choose exactly one:

- suitable for productive coding usage
- suitable only for limited/offline scenarios
- not viable

Selected recommendation: **not viable** for the current host state.

Rationale: The target host has a usable AMD Renoir iGPU via Vulkan/RADV, and the repository Docker/devcontainer path has been verified with Ollama Vulkan using the bundled `qwen2.5-coder:1.5b` smoke model. However, the required GPU-backed Qwen3.6-35B-A3B run cannot be executed because the target model weights are absent. Since the model download is large enough to require explicit authorization before fetching, the acceptance criteria for measured throughput, latency, resource use, quality comparison, and final productive-use viability remain blocked. The smallest fix is to choose one option from `evaluation/qwen3.6-35b-a3b/model-authorization-request-2026-05-09.md`: authorize a specific Qwen3.6-35B-A3B model pull/download, provide the target weights/cache, or explicitly narrow PR #2 to artifact/runtime readiness only. If the benchmark remains in scope, rerun this report with real measurements after the model artifact is available.
