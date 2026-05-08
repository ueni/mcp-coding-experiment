<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Qwen3.6-35B-A3B Local Evaluation Report

This file is the canonical report for issue #1. It records the 2026-05-08 target-host attempt and the exact blocker. No model-quality or throughput numbers are fabricated.

## Run metadata

- Date: 2026-05-08
- Evaluator: Builder
- Repository commit: `172ec50` (`builder/qwen36-local-eval-plan` before this blocker-report update)
- Target hardware observed: Lenovo ThinkPad T14 Gen1 AMD / `user-thinkpad-t14`
- Hardware deviation from Lenovo ThinkPad T14 Gen1 AMD, if any: none observed
- Power profile / AC or battery: `performance`; AC offline; battery discharging at 64%
- OS/kernel: `Linux user-thinkpad-t14 7.0.0-15-generic #15-Ubuntu SMP PREEMPT_DYNAMIC Wed Apr 22 16:06:43 UTC 2026 x86_64 GNU/Linux`
- Backend/runtime: not available for model inference; Vulkan/RADV GPU runtime detected, but Ollama, llama.cpp CLI/server, `llama_cpp`, PyTorch, ROCm, and OpenCL GPU runtimes were unavailable
- Model source, revision, quantization, checksum: not downloaded; candidate checked via Hugging Face API as `unsloth/Qwen3.6-35B-A3B-GGUF` at SHA `a483e9e6cbd595906af30beda3187c2663a1118c`, gated `false`; no local checksum because no weight file was fetched
- Reference comparison implementation: current orchestrator implementation in this repository; runtime comparison not executed because the local model run was blocked and the local orchestrator model runtime (`ollama`) was also absent
- Detailed evidence: `evaluation/qwen3.6-35b-a3b/host-gpu-probe-2026-05-08.md`

## Setup and startup

- Model acquisition command(s): none executed. The model download was stopped before transfer because practical GGUF files are large:
  - `Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf`: 10,756,586,464 bytes (~10.0 GiB)
  - `Qwen3.6-35B-A3B-UD-IQ2_M.gguf`: 11,522,702,304 bytes (~10.7 GiB)
  - `Qwen3.6-35B-A3B-UD-IQ4_XS.gguf`: 17,730,509,792 bytes (~16.5 GiB)
  - `Qwen3.6-35B-A3B-MXFP4_MOE.gguf`: 21,706,144,736 bytes (~20.2 GiB)
  - `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`: 22,134,528,992 bytes (~20.6 GiB)
- Server startup command: not executed; no GPU-capable inference backend was installed (`ollama`, `llama-server`, `llama-cli`, `llamafile` absent)
- Startup time: not measured; startup blocked before server launch
- Disk footprint: no additional model/runtime footprint created; repo filesystem had 147 GiB available at probe time
- Notes: CPU fallback was intentionally not attempted because ueni clarified that GPU must be used

## Aggregate results

| Backend | Scenarios completed | Median first-token latency (s) | Median end-to-end latency (s) | Sustained tokens/sec | Peak RAM (MB) | GPU/VRAM | Overall quality |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| qwen3.6-35b-a3b-local | 0/7 | not measured | not measured | not measured | not measured | AMD Renoir iGPU detected via Vulkan/RADV; no GPU inference backend/model weights available | not evaluated; run blocked |
| current-orchestrator | 0/7 | not measured | not measured | not measured | not measured | not applicable to blocked run; local Ollama runtime absent | not evaluated; comparison blocked |

## Scenario results

| Scenario ID | Backend | First token (s) | End-to-end (s) | Input tokens | Output tokens | Tokens/sec | Resources | Verdict | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| embedded-c-review-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Vulkan GPU detected; no model/runtime | blocked | No GPU-backed inference backend installed; no local model weights |
| embedded-c-review-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Ollama absent | blocked | Comparison not meaningful without candidate run and local orchestrator runtime |
| bash-hardening-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Vulkan GPU detected; no model/runtime | blocked | Same blocker |
| bash-hardening-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Ollama absent | blocked | Same blocker |
| python-refactor-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Vulkan GPU detected; no model/runtime | blocked | Same blocker |
| python-refactor-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Ollama absent | blocked | Same blocker |
| javascript-async-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Vulkan GPU detected; no model/runtime | blocked | Same blocker |
| javascript-async-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Ollama absent | blocked | Same blocker |
| debug-review-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Vulkan GPU detected; no model/runtime | blocked | Same blocker |
| debug-review-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Ollama absent | blocked | Same blocker |
| long-context-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Vulkan GPU detected; no model/runtime | blocked | Same blocker |
| long-context-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Ollama absent | blocked | Same blocker |
| structured-json-001 | qwen3.6-35b-a3b-local | n/a | n/a | n/a | n/a | n/a | Vulkan GPU detected; no model/runtime | blocked | Same blocker |
| structured-json-001 | current-orchestrator | n/a | n/a | n/a | n/a | n/a | Ollama absent | blocked | Same blocker |

## Quality notes

- C/C++ embedded: not evaluated; no model output generated.
- Bash: not evaluated; no model output generated.
- Python: not evaluated; no model output generated.
- JavaScript: not evaluated; no model output generated.
- Debugging/review: not evaluated; no model output generated.
- Long-context prompts: not evaluated; no model output generated.
- Structured output reliability: not evaluated; no model output generated.

## Limitations and failure patterns

- Reproducibility issues: target hardware is correct and GPU is visible through Vulkan/RADV, but inference reproducibility is blocked by missing runtime and missing local weights.
- Latency/throughput issues: no startup, first-token, end-to-end, or sustained tokens/sec measurements are available. The ~14 tokens/sec target remains unverified.
- Resource pressure: host has 28 GiB RAM and 147 GiB free disk. The smallest practical GGUF checked is ~10.0 GiB; better-quality quants are ~16.5-20.6 GiB. Integrated GPU memory is shared system memory, so real runs may create significant RAM and thermal pressure.
- Quality failures: no candidate or orchestrator outputs were produced, so no coding-quality comparison is possible yet.
- Operational costs: before evaluation can run, someone must authorize a large model download and install/build a Vulkan-capable inference backend. The machine should be on AC power for representative measurements.

## Final recommendation

Choose exactly one:

- suitable for productive coding usage
- suitable only for limited/offline scenarios
- not viable

Selected recommendation: **not viable** for the current host state.

Rationale: The target host has a usable AMD Renoir iGPU via Vulkan/RADV, but the required GPU-backed Qwen3.6-35B-A3B run cannot be executed from the current environment because no GPU-capable inference backend and no local model weights are present. Since GPU use is mandatory and the model download is large enough to require explicit authorization before fetching, the acceptance criteria for measured throughput, latency, resource use, quality comparison, and final productive-use viability remain blocked. The smallest fix is to authorize a specific GGUF quant download and provide/build a Vulkan-capable llama.cpp or equivalent runtime, then rerun this report with real measurements.
