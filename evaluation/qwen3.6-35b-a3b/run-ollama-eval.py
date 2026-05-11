#!/usr/bin/env python3
"""Run reproducible Qwen3.6 local evaluation scenarios against Ollama.

The script is dependency-free and writes a JSON artifact containing request
metadata, latency, Ollama token counters, output previews, and hygiene verdicts.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from pathlib import Path
from typing import Any

SENTINELS = ("<think>", "</think>", "<|im_start|>", "<|im_end|>", "<|endoftext|>")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def post_generate(endpoint: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - local eval endpoint
        raw = resp.read().decode("utf-8", errors="replace")
    total_s = time.monotonic() - started
    parsed = json.loads(raw)
    parsed["_end_to_end_latency_s"] = total_s
    return parsed


def verdict(output: str, requested_output_format: str = "") -> dict[str, Any]:
    findings = []
    for token in SENTINELS:
        if token in output:
            findings.append(f"leaked token: {token}")
    if requested_output_format == "json":
        try:
            json.loads(output)
        except json.JSONDecodeError as exc:
            findings.append(f"invalid json: {exc}")
    return {
        "pass": not findings,
        "findings": findings,
        "output_chars": len(output),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", type=Path, default=Path(__file__).with_name("scenarios.jsonl"))
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--model", default="qwen3.6-35b-a3b:iq1")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("latest-results.json"))
    parser.add_argument("--num-predict", type=int, default=512)
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    results: list[dict[str, Any]] = []
    for scenario in load_jsonl(args.scenarios):
        payload = {
            "model": args.model,
            "prompt": scenario["prompt"],
            "stream": False,
            "options": {
                "temperature": args.temperature,
                "num_predict": args.num_predict,
                "num_ctx": args.num_ctx,
                "stop": list(SENTINELS),
            },
        }
        try:
            response = post_generate(args.endpoint, payload, args.timeout)
            output = str(response.get("response", ""))
            eval_count = response.get("eval_count") or 0
            eval_duration = response.get("eval_duration") or 0
            tokens_per_second = (eval_count / (eval_duration / 1_000_000_000)) if eval_count and eval_duration else None
            results.append(
                {
                    "scenario_id": scenario["id"],
                    "category": scenario.get("category", ""),
                    "model": args.model,
                    "ok": True,
                    "end_to_end_latency_s": round(response["_end_to_end_latency_s"], 3),
                    "prompt_eval_count": response.get("prompt_eval_count"),
                    "eval_count": eval_count,
                    "tokens_per_second": round(tokens_per_second, 2) if tokens_per_second else None,
                    "verdict": verdict(output, scenario.get("requested_output_format", "")),
                    "output_preview": output[:800],
                }
            )
        except Exception as exc:  # pragma: no cover - operational artifact
            results.append(
                {
                    "scenario_id": scenario.get("id", "unknown"),
                    "category": scenario.get("category", ""),
                    "model": args.model,
                    "ok": False,
                    "error": repr(exc),
                }
            )

    latencies = [r["end_to_end_latency_s"] for r in results if r.get("ok") and r.get("end_to_end_latency_s") is not None]
    speeds = [r["tokens_per_second"] for r in results if r.get("ok") and r.get("tokens_per_second") is not None]
    artifact = {
        "schema": "qwen36_ollama_eval.v1",
        "model": args.model,
        "endpoint": args.endpoint,
        "scenario_count": len(results),
        "passed_hygiene_count": sum(1 for r in results if r.get("verdict", {}).get("pass")),
        "median_end_to_end_latency_s": statistics.median(latencies) if latencies else None,
        "median_tokens_per_second": statistics.median(speeds) if speeds else None,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: artifact[k] for k in artifact if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
