<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Qwen3.6-35B-A3B Local Coding Evaluation

Status: GPU-backed target-runtime evaluation record for `ueni/mcp-coding-experiment#1` with a checked-in current-orchestrator comparison harness and evidence. This PR still does **not** close or satisfy the full issue #1 benchmark acceptance criteria because bounded target-model quality is below the expected default-assistant bar and the current-orchestrator harness can only measure the repository task router's non-streaming, degraded `tool_fallback` path in this environment. The selected public GGUF is present/checksummed locally, Docker/Ollama can load it with Vulkan/RADV, and a full seven-scenario bounded target-model run completed on 2026-05-09 with `offloaded 41/41 layers to GPU`. The measured median `8.056` sustained tokens/sec now meets the revised approximately 7 sustained tokens/sec throughput threshold.

## Goal

Evaluate whether Qwen3.6-35B-A3B is viable as a locally hosted coding assistant for `codebase-tooling-mcp` on a Lenovo ThinkPad T14 Gen1 AMD, compared with the current orchestrator implementation.

The evaluation is practical rather than benchmark-only: measure interactive coding usefulness, latency, resource use, reproducibility, and whether the validation path can run on default GitHub-hosted GitHub Actions runners.

## Fixed inputs

| Item | Value |
| --- | --- |
| Target hardware | Lenovo ThinkPad T14 Gen1 AMD |
| Candidate model | Qwen3.6-35B-A3B |
| Reference comparison | Current orchestrator implementation in this repository |
| Expected throughput to verify | approximately 7 sustained tokens/sec |
| CI/CD target | Default GitHub-hosted GitHub Actions runner only (`ubuntu-latest`) |
| Scenario manifest | `evaluation/qwen3.6-35b-a3b/coding-scenarios.jsonl` |
| Report template | `evaluation/qwen3.6-35b-a3b/report-template.md` |
| Official local runtime | Docker image from `source/Dockerfile`, started by `.devcontainer/devcontainer.json` or equivalent `docker run` |
| Docker GPU evidence | `evaluation/qwen3.6-35b-a3b/docker-gpu-runtime-2026-05-08.md` |
| Model authorization blocker | `evaluation/qwen3.6-35b-a3b/model-authorization-request-2026-05-09.md` |
| Target model acquisition attempt | `evaluation/qwen3.6-35b-a3b/target-model-acquisition-attempt-2026-05-09.md` |
| Scenario runner harness | `evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py` |
| Target model smoke result | `evaluation/qwen3.6-35b-a3b/target-model-smoke-2026-05-09.md` and `evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-smoke-2026-05-09.json` |
| Verifier bounded expansion | `evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-verifier-bounded-2026-05-09.json` and `.log` |
| Full bounded target-model run | `evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-full-2026-05-09.json` and `.log` |
| Current-orchestrator comparison harness | `evaluation/qwen3.6-35b-a3b/run-current-orchestrator-eval.py` |
| Current-orchestrator comparison result | `evaluation/qwen3.6-35b-a3b/results/results-current-orchestrator-2026-05-09.json` and `.log` |
| Historical current-orchestrator blocker | `evaluation/qwen3.6-35b-a3b/current-orchestrator-comparison-blocker-2026-05-09.md` |

## Evaluation scope

Run every scenario in the manifest for both systems where possible:

1. local Qwen3.6-35B-A3B candidate;
2. current orchestrator implementation.

The local candidate now has a seven-scenario GPU-backed bounded run. The current orchestrator comparison is captured by `evaluation/qwen3.6-35b-a3b/run-current-orchestrator-eval.py`, which runs the same scenario manifest through the checked-in `task_router(mode="task")` path with persistence disabled and records end-to-end latency, estimated token counts, process RSS deltas, route/backend state, output previews, and coarse quality verdicts in `evaluation/qwen3.6-35b-a3b/results/results-current-orchestrator-2026-05-09.json`. The task router does not expose streaming first-token events or tokenizer counts, so those fields are explicitly marked as non-streaming/estimated instead of being fabricated.

## Official Docker runtime path

Use `source/Dockerfile` plus `.devcontainer/devcontainer.json` as the official runtime path for the local evaluation. The host may not have Ollama or llama.cpp installed directly; that is not a blocker for this plan as long as the Docker/devcontainer path is available.

The checked-in runtime path provides:

- Ollama `0.18.2` in the image;
- Vulkan/Mesa userspace packages (`libvulkan1`, `mesa-vulkan-drivers`, `vulkan-tools`);
- `/dev/dri` GPU device pass-through from the devcontainer config;
- `OLLAMA_VULKAN=1` for the bundled Ollama service;
- an Ollama API exposed on port `2345` and used by `LOCAL_INFER_ENDPOINT`.

Equivalent non-VS Code command shape for the target-model evidence adds both GPU devices and the host render/KFD groups so the non-root container user can access RADV:

```bash
docker build -t codebase-tooling-mcp:qwen-eval ./source

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
  codebase-tooling-mcp:qwen36-eval bash -lc '... ollama serve/create; python3 /repo/evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py ...'
```

Verifier confirmed the base Docker GPU path on the target AMD host: RADV/Renoir was visible, Ollama Vulkan was active, and a `qwen2.5-coder:1.5b` smoke generation offloaded `29/29` layers to GPU. That smoke test is runtime validation only; it is not a target Qwen3.6-35B-A3B result.

The earlier target-model CPU-only attempt is historical evidence, not the current result: the direct Docker command mounted `/dev/dri` but did not add the render device group for the `app` user, so Ollama reported `offloaded 0/41 layers to GPU` and measured 5.584 tokens/sec on one smoke prompt. After adding `/dev/kfd` and the host render/KFD `--group-add` values, Qwen3.6-35B-A3B loaded the Vulkan backend and offloaded `41/41` layers to GPU. Current committed target-model evidence is:

- one-scenario smoke: median `7.929` tokens/sec, `partial`, GPU-backed;
- Verifier bounded two-scenario expansion: `2/2` completed, median `7.997` tokens/sec, GPU-backed;
- full bounded seven-scenario run: `7/7` completed, median `8.056` tokens/sec, median first-token latency `3.307`s, median end-to-end latency `13.337`s, GPU-backed.

## Reproducible setup

Record the exact backend used to obtain and serve the model. The recommended local route is the repository Docker image's Ollama local inference endpoint so the same harness can call both local model and orchestrator-like interfaces.

Minimum setup record:

```bash
uname -a
lscpu
free -h
df -h .
python --version
```

If GPU acceleration is used, also record:

```bash
ls -l /dev/dri /dev/kfd || true
vulkaninfo --summary || true
```

Model acquisition must record one of:

- Hugging Face repository/revision and quantization file/checksum;
- Ollama model tag and `ollama show` output;
- llama.cpp or vLLM command, image tag, model file, and checksum.

Do not commit downloaded model weights to this repository. The Qwen3.6-35B-A3B weights remain local-only evidence and are not committed.

## Startup and inference procedure

Record startup with timestamps and the exact command, for example:

```bash
/usr/bin/time -v <serve-command>
```

For each scenario, capture:

- prompt ID;
- backend name (`qwen3.6-35b-a3b-local` or `current-orchestrator`);
- model identifier and quantization;
- first-token latency in seconds;
- end-to-end latency in seconds;
- input tokens;
- output tokens;
- sustained output tokens/sec;
- peak RSS/RAM;
- CPU utilization notes;
- GPU/VRAM notes if applicable;
- output file or transcript path;
- pass/fail/partial judgment and reviewer notes.

Use the report template for final results. `evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py` runs the scenario manifest against the Docker Ollama endpoint and writes machine-readable latency/throughput results. `evaluation/qwen3.6-35b-a3b/run-current-orchestrator-eval.py` runs the same manifest through the repository's current task orchestrator; its first-token latency is `null` because the task router is non-streaming, and its token counts are approximate because no tokenizer counts are exposed.

## Scenario set

The manifest covers all required categories:

- C/C++ embedded code;
- Bash scripting;
- Python;
- JavaScript;
- debugging and code review;
- long-context technical prompts;
- structured output reliability.

Each JSONL row contains `id`, `category`, `title`, `prompt`, `expected_observations`, `quality_checks`, and `measurement_fields`.

## CI/CD validation on GitHub-hosted runners

Default GitHub-hosted runners cannot run the local 35B-class model reproducibly, so CI validates the evaluation artifacts rather than executing model inference.

The workflow `.github/workflows/qwen-evaluation-artifacts.yml` runs on `ubuntu-latest` and verifies:

- the scenario manifest is valid JSONL;
- required scenario categories are present;
- each scenario carries measurement fields needed for latency, throughput, resource usage, and quality comparison;
- this documentation links the canonical manifest and report template;
- committed GPU-backed result JSON covers all seven scenarios and does not contradict the current Vulkan offload evidence;
- committed current-orchestrator result JSON covers the same seven scenarios with explicit non-streaming/estimated measurement notes.

This satisfies the GitHub-hosted CI path without depending on self-hosted hardware, private model caches, or non-default runners.

## Viability decision rule

Final recommendation must be one of:

- `suitable for productive coding usage`;
- `suitable only for limited/offline scenarios`;
- `not viable`.

Use this minimum bar:

- **Productive**: setup reproducible, common prompts feel interactive, sustained throughput is close to or above the 7 tokens/sec expectation, resource use leaves the laptop usable, quality is competitive with the current orchestrator for most categories, and structured output is reliable.
- **Limited/offline**: setup works and privacy/offline value is high, but latency, resource use, context limits, or quality make it a fallback rather than the default.
- **Not viable**: setup is not reproducible on the target hardware, throughput/latency is below practical use, resource use destabilizes the laptop, or quality is materially worse than the current orchestrator.

## Current recommendation

Selected recommendation: **suitable only for limited/offline scenarios**.

Rationale: the GPU-backed Docker/Ollama path is reproducible on the target laptop for the selected IQ1_M GGUF, and measured throughput exceeds the revised approximately 7 sustained tokens/sec expectation (`8.056` median tokens/sec in the seven-scenario bounded run). Quality is mixed under the 80-token cap: several scenarios pass, but C/C++ embedded and debugging are partial and strict structured JSON failed. The current-orchestrator harness completed all seven scenarios through the non-streaming `task_router` path, but it exercised a degraded `tool_fallback`/unavailable local inference path rather than a full default assistant model. This is not sufficient to recommend replacing the repository's current assistant/orchestrator path.

## Known limitations to document during execution

- Any deviation from Lenovo ThinkPad T14 Gen1 AMD hardware.
- Quantization compromises required to fit RAM/VRAM.
- Thermal throttling, battery state, power profile, or swap use.
- Network dependency during model acquisition.
- Prompt failures, hallucinated APIs, incomplete patches, malformed JSON, or unsafe shell suggestions.
- Operational cost: setup time, disk footprint, power/thermal load, and maintenance effort.
