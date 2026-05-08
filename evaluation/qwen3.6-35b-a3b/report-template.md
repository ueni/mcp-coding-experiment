<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Qwen3.6-35B-A3B Local Evaluation Report

## Run metadata

- Date:
- Evaluator:
- Repository commit:
- Target hardware observed:
- Hardware deviation from Lenovo ThinkPad T14 Gen1 AMD, if any:
- Power profile / AC or battery:
- OS/kernel:
- Backend/runtime:
- Model source, revision, quantization, checksum:
- Reference comparison implementation:

## Setup and startup

- Model acquisition command(s):
- Server startup command:
- Startup time:
- Disk footprint:
- Notes:

## Aggregate results

| Backend | Scenarios completed | Median first-token latency (s) | Median end-to-end latency (s) | Sustained tokens/sec | Peak RAM (MB) | GPU/VRAM | Overall quality |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| qwen3.6-35b-a3b-local |  |  |  |  |  |  |  |
| current-orchestrator |  |  |  |  |  |  |  |

## Scenario results

| Scenario ID | Backend | First token (s) | End-to-end (s) | Input tokens | Output tokens | Tokens/sec | Resources | Verdict | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| embedded-c-review-001 | qwen3.6-35b-a3b-local |  |  |  |  |  |  |  |  |
| embedded-c-review-001 | current-orchestrator |  |  |  |  |  |  |  |  |
| bash-hardening-001 | qwen3.6-35b-a3b-local |  |  |  |  |  |  |  |  |
| bash-hardening-001 | current-orchestrator |  |  |  |  |  |  |  |  |
| python-refactor-001 | qwen3.6-35b-a3b-local |  |  |  |  |  |  |  |  |
| python-refactor-001 | current-orchestrator |  |  |  |  |  |  |  |  |
| javascript-async-001 | qwen3.6-35b-a3b-local |  |  |  |  |  |  |  |  |
| javascript-async-001 | current-orchestrator |  |  |  |  |  |  |  |  |
| debug-review-001 | qwen3.6-35b-a3b-local |  |  |  |  |  |  |  |  |
| debug-review-001 | current-orchestrator |  |  |  |  |  |  |  |  |
| long-context-001 | qwen3.6-35b-a3b-local |  |  |  |  |  |  |  |  |
| long-context-001 | current-orchestrator |  |  |  |  |  |  |  |  |
| structured-json-001 | qwen3.6-35b-a3b-local |  |  |  |  |  |  |  |  |
| structured-json-001 | current-orchestrator |  |  |  |  |  |  |  |  |

## Quality notes

- C/C++ embedded:
- Bash:
- Python:
- JavaScript:
- Debugging/review:
- Long-context prompts:
- Structured output reliability:

## Limitations and failure patterns

- Reproducibility issues:
- Latency/throughput issues:
- Resource pressure:
- Quality failures:
- Operational costs:

## Final recommendation

Choose exactly one:

- suitable for productive coding usage
- suitable only for limited/offline scenarios
- not viable

Rationale:
