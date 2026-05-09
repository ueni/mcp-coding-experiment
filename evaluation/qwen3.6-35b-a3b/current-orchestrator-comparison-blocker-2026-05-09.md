<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Current-Orchestrator Comparison Blocker: Qwen3.6-35B-A3B Evaluation

Date: 2026-05-09

This note records why PR #2 still does not contain a current-orchestrator comparison result for issue #1. It is a blocker artifact, not a benchmark result.

## Current status

- The local Qwen3.6-35B-A3B Docker/Ollama run completed all seven scenario categories with GPU offload and a median `8.056` sustained tokens/sec.
- That median now meets the revised approximately `7` sustained tokens/sec throughput threshold.
- The current-orchestrator comparison remains unavailable and must not be inferred from the local Qwen run.

## What was checked

Repository inspection found no checked-in harness that maps `evaluation/qwen3.6-35b-a3b/coding-scenarios.jsonl` to the current orchestrator while producing the same measurement schema used by `evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py`.

Relevant implementation details:

- `source/server.py` exposes the public orchestrator entrypoint through `task_router`.
- `task_router(mode="coding_infer")` routes through `local_infer` and returns `task_router.coding_infer.v1` with an `infer` payload.
- `local_infer` returns model output and degraded/fallback state, but not first-token latency, input token count, output token count, Ollama eval durations, sustained tokens/sec, or per-scenario resource notes.
- `_local_infer_via_endpoint` calls the Ollama `/api/generate` endpoint with `stream: false` and returns only text-like fields from the response, discarding timing/token metadata if the backend supplies it.
- The Qwen Docker/Ollama harness measures the local model directly and writes the required latency/token/throughput fields, but it does not exercise the repository's task router/orchestrator flow.

Because of that contract mismatch, filling the `current-orchestrator` rows in the report would require new comparison-harness work or a separately approved manual comparison protocol. This PR records `current-orchestrator` as `0/7` and `not measured` rather than fabricating comparable values.

## Safe unblock options

1. Add a dedicated current-orchestrator comparison harness that:
   - loads the same scenario manifest;
   - invokes the intended current orchestrator route;
   - captures first-token latency, end-to-end latency, input/output token counts, sustained tokens/sec, resource notes, outputs/transcripts, and quality verdicts;
   - writes a JSON result file with the same aggregate/per-scenario shape as the local Qwen run.
2. Define and document a manual comparison protocol if exact latency/token parity is not required.
3. Explicitly change issue #1 acceptance criteria to remove or defer the current-orchestrator comparison.

Until one of those options is completed, the comparison acceptance criterion remains blocked even though the local Qwen throughput criterion now passes the revised threshold.
