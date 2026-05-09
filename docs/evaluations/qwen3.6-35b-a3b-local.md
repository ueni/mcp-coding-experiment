<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Qwen3.6-35B-A3B Local Coding Evaluation

Status: blocked target-runtime evaluation record for `ueni/mcp-coding-experiment#1`. This PR still does **not** close or satisfy the full issue #1 benchmark acceptance criteria. The Docker runtime path is covered and the selected public GGUF is now present/checksummed locally. A one-scenario target-model smoke run completed on 2026-05-09, but Ollama loaded the CPU backend and offloaded `0/41` layers to GPU despite `/dev/dri` and `OLLAMA_VULKAN=1`. Because GPU use is required, the full benchmark and viability decision remain blocked.

## Goal

Evaluate whether Qwen3.6-35B-A3B is viable as a locally hosted coding assistant for `codebase-tooling-mcp` on a Lenovo ThinkPad T14 Gen1 AMD, compared with the current orchestrator implementation.

The evaluation is practical rather than benchmark-only: measure interactive coding usefulness, latency, resource use, reproducibility, and whether the validation path can run on default GitHub-hosted GitHub Actions runners.

## Fixed inputs

| Item | Value |
| --- | --- |
| Target hardware | Lenovo ThinkPad T14 Gen1 AMD |
| Candidate model | Qwen3.6-35B-A3B |
| Reference comparison | Current orchestrator implementation in this repository |
| Expected throughput to verify | approximately 14 sustained tokens/sec |
| CI/CD target | Default GitHub-hosted GitHub Actions runner only (`ubuntu-latest`) |
| Scenario manifest | `evaluation/qwen3.6-35b-a3b/coding-scenarios.jsonl` |
| Report template | `evaluation/qwen3.6-35b-a3b/report-template.md` |
| Official local runtime | Docker image from `source/Dockerfile`, started by `.devcontainer/devcontainer.json` or equivalent `docker run` |
| Docker GPU evidence | `evaluation/qwen3.6-35b-a3b/docker-gpu-runtime-2026-05-08.md` |
| Model authorization blocker | `evaluation/qwen3.6-35b-a3b/model-authorization-request-2026-05-09.md` |
| Target model acquisition attempt | `evaluation/qwen3.6-35b-a3b/target-model-acquisition-attempt-2026-05-09.md` |
| Scenario runner harness | `evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py` |
| Target model smoke result | `evaluation/qwen3.6-35b-a3b/target-model-smoke-2026-05-09.md` and `evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-smoke-2026-05-09.json` |

## Evaluation scope

Run every scenario in the manifest for both systems where possible:

1. local Qwen3.6-35B-A3B candidate;
2. current orchestrator implementation.

Capture the same measurements for both systems so the recommendation is based on comparable data.

## Official Docker runtime path

Use `source/Dockerfile` plus `.devcontainer/devcontainer.json` as the official runtime path for the local evaluation. The host may not have Ollama or llama.cpp installed directly; that is not a blocker for this plan as long as the Docker/devcontainer path is available.

The checked-in runtime path provides:

- Ollama `0.18.2` in the image;
- Vulkan/Mesa userspace packages (`libvulkan1`, `mesa-vulkan-drivers`, `vulkan-tools`);
- `/dev/dri` GPU device pass-through from the devcontainer config;
- `OLLAMA_VULKAN=1` for the bundled Ollama service;
- an Ollama API exposed on port `2345` and used by `LOCAL_INFER_ENDPOINT`.

Equivalent non-VS Code command shape:

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

Verifier confirmed this Docker GPU path on the target AMD host: RADV/Renoir was visible, Ollama Vulkan was active, and a `qwen2.5-coder:1.5b` smoke generation offloaded `29/29` layers to GPU. That smoke test is runtime validation only; it is not a target Qwen3.6-35B-A3B result.

The current blocker is precise: the selected target GGUF is present and importable, so Qwen3.6-35B-A3B weights are no longer the acquisition blocker for this selected quantization, but GPU acceleration did not activate for Qwen3.6-35B-A3B. On 2026-05-09, `Qwen3.6-35B-A3B-UD-IQ1_M.gguf` was present at `10047749088` bytes with SHA256 `0dc2488c89d916c5599f7c03a286cd8f37a6a75a02bc13caf41c6bac26d70c9e`. A one-scenario smoke run completed (`embedded-c-review-001`, `--num-predict 80`) with first-token latency `15.501s`, end-to-end latency `29.843s`, and `5.584` sustained output tokens/sec. Ollama logs show `load_backend: loaded CPU backend` and `offloaded 0/41 layers to GPU`, so this run does not meet the clarified GPU-backed requirement and is below the approximately 14 tokens/sec expectation. The exact smoke evidence is recorded in `evaluation/qwen3.6-35b-a3b/target-model-smoke-2026-05-09.md`.

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
ls -l /dev/dri || true
vulkaninfo --summary || true
```

Model acquisition must record one of:

- Hugging Face repository/revision and quantization file/checksum;
- Ollama model tag and `ollama show` output;
- llama.cpp or vLLM command, image tag, model file, and checksum.

Do not commit downloaded model weights to this repository.

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

Use the report template for final results. After a model artifact is authorized or provided, `evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py` can run the scenario manifest against the Docker Ollama endpoint and write machine-readable latency/throughput results.

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
- this documentation links the canonical manifest and report template.

This satisfies the GitHub-hosted CI path without depending on self-hosted hardware, private model caches, or non-default runners.

## Viability decision rule

Final recommendation must be one of:

- `suitable for productive coding usage`;
- `suitable only for limited/offline scenarios`;
- `not viable`.

Use this minimum bar:

- **Productive**: setup reproducible, common prompts feel interactive, sustained throughput is close to or above the 14 tokens/sec expectation, resource use leaves the laptop usable, quality is competitive with the current orchestrator for most categories, and structured output is reliable.
- **Limited/offline**: setup works and privacy/offline value is high, but latency, resource use, context limits, or quality make it a fallback rather than the default.
- **Not viable**: setup is not reproducible on the target hardware, throughput/latency is below practical use, resource use destabilizes the laptop, or quality is materially worse than the current orchestrator.

## Known limitations to document during execution

- Any deviation from Lenovo ThinkPad T14 Gen1 AMD hardware.
- Quantization compromises required to fit RAM/VRAM.
- Thermal throttling, battery state, power profile, or swap use.
- Network dependency during model acquisition.
- Prompt failures, hallucinated APIs, incomplete patches, malformed JSON, or unsafe shell suggestions.
- Operational cost: setup time, disk footprint, power/thermal load, and maintenance effort.
