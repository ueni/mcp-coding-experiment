<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Historical Current-Orchestrator Comparison Blocker: Qwen3.6-35B-A3B Evaluation

Date: 2026-05-09

This note records the blocker that previously left PR #2 without a current-orchestrator comparison result for issue #1. It is now a historical blocker artifact: the PR includes `evaluation/qwen3.6-35b-a3b/run-current-orchestrator-eval.py` plus `evaluation/qwen3.6-35b-a3b/results/results-current-orchestrator-2026-05-09.json` as the checked-in comparison evidence.

## Current status

- The local Qwen3.6-35B-A3B Docker/Ollama run completed all seven scenario categories with GPU offload and a median `8.056` sustained tokens/sec.
- That median now meets the revised approximately `7` sustained tokens/sec throughput threshold.
- The current-orchestrator comparison must not be inferred from the local Qwen run; it is now measured separately by the checked-in current-orchestrator harness.
- The new harness records `current-orchestrator` as `7/7` harness invocations rather than the prior `0/7` and `not measured` placeholder.

## What was originally checked

Repository inspection found no checked-in harness that maps `evaluation/qwen3.6-35b-a3b/coding-scenarios.jsonl` to the current orchestrator while producing the same measurement schema used by `evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py`.

Relevant implementation details:

- `source/server.py` exposes the public orchestrator entrypoint through `task_router`.
- `task_router(mode="coding_infer")` routes through `local_infer` and returns `task_router.coding_infer.v1` with an `infer` payload.
- `local_infer` returns model output and degraded/fallback state, but not first-token latency, input token count, output token count, Ollama eval durations, sustained tokens/sec, or per-scenario resource notes.
- `_local_infer_via_endpoint` calls the Ollama `/api/generate` endpoint with `stream: false` and returns only text-like fields from the response, discarding timing/token metadata if the backend supplies it.
- The Qwen Docker/Ollama harness measures the local model directly and writes the required latency/token/throughput fields, but it does not exercise the repository's task router/orchestrator flow.

Because of that contract mismatch, the comparison harness now records the fields the task router can support without overstatement:

- end-to-end latency from `time.perf_counter`;
- estimated input/output token counts from whitespace word count x1.3, explicitly marked as estimates;
- estimated output tokens/sec for the non-streaming fallback text, explicitly not equivalent to Ollama eval throughput;
- process `ru_maxrss` delta for the harness invocation, not whole-host peak RAM;
- first-token latency as `null` because the route is non-streaming;
- route/backend/degraded state, output preview, failure diagnosis, workflow benchmark, and coarse quality verdict.

## Remaining interpretation limits

The current-orchestrator evidence is real repository-task-router evidence, but it exercised the degraded `tool_fallback`/unavailable local inference path available in the Docker evaluation environment. It should not be treated as a full hosted/default-assistant model comparison unless a stakeholder provides the exact production/current-orchestrator endpoint or credentials and approves measuring that path.

Safe follow-up options if exact production parity is required:

1. Provide the intended production/current-orchestrator endpoint and authorization model so the harness can call it.
2. Extend `task_router`/`local_infer` to expose streaming first-token events and backend tokenizer counts.
3. Explicitly accept the current checked-in comparison as the issue #1 repository-orchestrator baseline.
