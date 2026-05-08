<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Qwen3.6-35B-A3B Local Coding Evaluation

Status: evaluation plan and reproducibility artifact for `ueni/mcp-coding-experiment#1`.

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

The remaining blocker is precise: Qwen3.6-35B-A3B weights are not present. The benchmark requires ueni to authorize a specific model pull/download or provide the target model weights/cache. Do not download large model weights without explicit approval.

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

Use the report template for final results.

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
