<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Qwen3.6-35B-A3B Blocked Evaluation Artifact

Date: 2026-05-09
PR: #2
Issue: #1

## Blocker

This is a historical authorization-blocker artifact for the Qwen3.6-35B-A3B run required by issue #1. It is retained to document model acquisition provenance; later checked-in smoke and bounded result artifacts supersede it as the current evaluation state.

The Docker/devcontainer runtime path is verified, but the actual Qwen3.6-35B-A3B benchmark cannot be completed until a specific target model artifact is authorized or supplied.

The verified runtime is `source/Dockerfile` with `.devcontainer/devcontainer.json` or an equivalent Docker invocation that passes `--device=/dev/dri` and sets `OLLAMA_VULKAN=1`.

No large Qwen3.6-35B-A3B weights file has been downloaded in this PR. This is an external/non-code blocker: changing repository docs, tests, or Docker configuration cannot produce valid throughput, latency, resource, or quality-comparison measurements without the target weights.

## Requested decision

Authorize exactly one of the following before the target benchmark is run:

1. Download a specific GGUF artifact from `unsloth/Qwen3.6-35B-A3B-GGUF` and record its revision plus checksum; or
2. Provide a local model cache/weight file path for Qwen3.6-35B-A3B; or
3. Declare the full target-model benchmark out of scope for PR #2 and keep this PR as an evaluation-artifact/runtime-readiness change only.

## Candidate artifacts observed before download

Repository checked: `unsloth/Qwen3.6-35B-A3B-GGUF`
Revision checked: `a483e9e6cbd595906af30beda3187c2663a1118c`

| File | Size |
| --- | ---: |
| `Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf` | 10,756,586,464 bytes (~10.0 GiB) |
| `Qwen3.6-35B-A3B-UD-IQ2_M.gguf` | 11,522,702,304 bytes (~10.7 GiB) |
| `Qwen3.6-35B-A3B-UD-IQ4_XS.gguf` | 17,730,509,792 bytes (~16.5 GiB) |
| `Qwen3.6-35B-A3B-MXFP4_MOE.gguf` | 21,706,144,736 bytes (~20.2 GiB) |
| `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` | 22,134,528,992 bytes (~20.6 GiB) |

## Benchmark work remaining after authorization

After the model artifact is authorized/provided, run the seven scenario categories from `evaluation/qwen3.6-35b-a3b/coding-scenarios.jsonl` inside the verified Docker runtime. The checked-in `evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py` harness can collect Ollama first-token latency, end-to-end latency, token counts, and sustained tokens/sec into JSON for the report. Then update `evaluation/qwen3.6-35b-a3b/report-template.md` with:

- model source, revision, quantization, checksum, and startup command;
- first-token latency, end-to-end latency, sustained tokens/sec, input/output tokens;
- RAM, CPU, storage, GPU/VRAM notes;
- current-orchestrator comparison outputs and quality/usability judgments;
- known limitations, operational costs, and final viability recommendation.

Historical status: this blocker applied before the selected GGUF was available locally and before the issue #1 acceptance criteria could be evaluated with real target-model output. The current PR state includes real target-model smoke and bounded run outputs; use the canonical evaluation doc and report template for the current issue #1 status.
