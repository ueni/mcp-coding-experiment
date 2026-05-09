#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT
"""Run the Qwen3.6 local evaluation scenarios against an Ollama endpoint.

This harness is intentionally dependency-free so it can run inside the repository
Docker/devcontainer image. It streams Ollama responses to measure first-token
latency, total latency, prompt/eval token counts, and Ollama's reported eval
throughput for each scenario.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path


def load_scenarios(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def post_stream(url: str, payload: dict, timeout: int) -> tuple[str, dict, float, float]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    first_chunk_at: float | None = None
    chunks: list[str] = []
    final: dict = {}
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw_line in response:
            if not raw_line.strip():
                continue
            now = time.monotonic()
            if first_chunk_at is None:
                first_chunk_at = now
            event = json.loads(raw_line)
            if event.get("response"):
                chunks.append(event["response"])
            if event.get("done"):
                final = event
                break
    ended = time.monotonic()
    return "".join(chunks), final, (first_chunk_at or ended) - started, ended - started


def verdict_for_output(scenario: dict, output: str) -> str:
    text = output.lower()
    checks = scenario.get("quality_checks", [])
    if scenario["category"] == "structured_output":
        try:
            parsed = json.loads(output)
            if isinstance(parsed.get("findings"), list) and "summary" in parsed:
                return "pass"
        except json.JSONDecodeError:
            return "fail"
        return "partial"
    hits = sum(1 for check in checks if any(word in text for word in check.lower().split() if len(word) > 5))
    if hits >= max(1, len(checks) // 2):
        return "pass"
    return "partial" if output.strip() else "fail"


def run(args: argparse.Namespace) -> dict:
    scenarios = load_scenarios(args.scenarios)
    results = []
    for scenario in scenarios:
        prompt = scenario["prompt"]
        payload = {
            "model": args.model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": args.temperature,
                "num_predict": args.num_predict,
                "num_ctx": args.num_ctx,
            },
        }
        try:
            output, final, first_s, total_s = post_stream(args.endpoint, payload, args.timeout)
            eval_count = int(final.get("eval_count") or 0)
            eval_duration_ns = int(final.get("eval_duration") or 0)
            tokens_per_sec = eval_count / (eval_duration_ns / 1_000_000_000) if eval_count and eval_duration_ns else None
            result = {
                "scenario_id": scenario["id"],
                "category": scenario["category"],
                "requested_output_format": scenario["requested_output_format"],
                "backend": args.backend,
                "model": args.model,
                "first_token_latency_s": round(first_s, 3),
                "end_to_end_latency_s": round(total_s, 3),
                "input_tokens": final.get("prompt_eval_count"),
                "output_tokens": final.get("eval_count"),
                "sustained_tokens_per_sec": round(tokens_per_sec, 3) if tokens_per_sec is not None else None,
                "load_duration_s": round((final.get("load_duration") or 0) / 1_000_000_000, 3),
                "prompt_eval_duration_s": round((final.get("prompt_eval_duration") or 0) / 1_000_000_000, 3),
                "eval_duration_s": round(eval_duration_ns / 1_000_000_000, 3),
                "verdict": verdict_for_output(scenario, output),
                "error": None,
                "output_preview": output[:1200],
            }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            result = {
                "scenario_id": scenario["id"],
                "category": scenario["category"],
                "requested_output_format": scenario["requested_output_format"],
                "backend": args.backend,
                "model": args.model,
                "error": repr(exc),
                "verdict": "blocked",
            }
        results.append(result)

    measured_tps = [r["sustained_tokens_per_sec"] for r in results if r.get("sustained_tokens_per_sec") is not None]
    aggregate = {
        "backend": args.backend,
        "model": args.model,
        "scenario_count": len(results),
        "completed": sum(1 for r in results if not r.get("error")),
        "median_tokens_per_sec": round(statistics.median(measured_tps), 3) if measured_tps else None,
        "median_first_token_latency_s": round(statistics.median(r["first_token_latency_s"] for r in results if "first_token_latency_s" in r), 3) if any("first_token_latency_s" in r for r in results) else None,
        "median_end_to_end_latency_s": round(statistics.median(r["end_to_end_latency_s"] for r in results if "end_to_end_latency_s" in r), 3) if any("end_to_end_latency_s" in r for r in results) else None,
    }
    return {"aggregate": aggregate, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--model", required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--num-predict", type=int, default=160)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    data = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data["aggregate"], indent=2))


if __name__ == "__main__":
    main()
