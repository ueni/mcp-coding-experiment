#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT
"""Run the Qwen scenario set through the repository's current task orchestrator.

This is a comparison harness for issue #1. It intentionally exercises the
checked-in `task_router(mode="task")` path rather than an Ollama endpoint so the
artifact distinguishes the current repository orchestrator from the candidate
Qwen3.6 local model. The task router is non-streaming, so first-token latency is
reported as null with an explicit note; end-to-end latency, estimated token
counts, peak process RSS delta, backend, route, and coarse quality verdicts are
recorded for each scenario.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import resource
import statistics
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "source" / "server.py"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_scenarios(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def estimated_tokens(text: str) -> int:
    # Portable approximation for backends that do not expose tokenizer counts.
    return max(0, round(len(text.split()) * 1.3))


def peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes; macOS reports bytes. This harness is run on Linux
    # for the committed evidence, but keep the conversion safe if reused.
    if sys.platform == "darwin":
        return rss / (1024 * 1024)
    return rss / 1024


def verdict_for_output(scenario: dict[str, Any], output: str, ok: bool) -> str:
    if not ok or not output.strip():
        return "blocked"
    if scenario.get("category") == "structured_output":
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            return "fail"
        return "pass" if isinstance(parsed.get("findings"), list) and "summary" in parsed else "partial"
    text = output.lower()
    checks = scenario.get("quality_checks", [])
    hits = sum(
        1
        for check in checks
        if any(word in text for word in check.lower().split() if len(word) > 5)
    )
    if hits >= max(1, len(checks) // 2):
        return "pass"
    return "partial"


def load_server_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("qwen_eval_current_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load server module from {SERVER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def disable_persistence(server: ModuleType) -> None:
    def _no_persist_task_memory(**_: Any) -> dict[str, Any]:
        return {"evidence_count": 0, "disabled_for_eval": True}

    server._persist_task_memory = _no_persist_task_memory  # type: ignore[attr-defined]


def run(args: argparse.Namespace) -> dict[str, Any]:
    os.environ.setdefault("LOCAL_INFER_BACKEND", args.backend)
    server = load_server_module()
    disable_persistence(server)
    if hasattr(server, "REPO_PATH"):
        server.REPO_PATH = REPO_ROOT

    scenarios = load_scenarios(args.scenarios)
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        rss_before = peak_rss_mb()
        started = time.perf_counter()
        try:
            response = server.task_router(  # type: ignore[attr-defined]
                mode="task",
                prompt=scenario["prompt"],
                backend=args.backend,
                model=args.model,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                output_profile="compact",
                store_result=False,
                memory_session=args.memory_session,
            )
            ended = time.perf_counter()
            output = str(response.get("output", "") or "")
            ok = bool(response.get("ok", False))
            backend = str(response.get("backend", "") or args.backend)
            input_tokens = estimated_tokens(scenario["prompt"])
            output_tokens = estimated_tokens(output)
            total_s = ended - started
            result = {
                "scenario_id": scenario["id"],
                "category": scenario["category"],
                "requested_output_format": scenario["requested_output_format"],
                "backend": "current-orchestrator",
                "orchestrator_backend": backend,
                "model": str(response.get("model", "") or args.model),
                "route": response.get("route"),
                "first_token_latency_s": None,
                "first_token_latency_note": "not exposed by non-streaming task_router; end-to-end latency measured instead",
                "end_to_end_latency_s": round(total_s, 3),
                "input_tokens": input_tokens,
                "input_tokens_note": "estimated by whitespace word count x1.3; task_router does not expose tokenizer counts",
                "output_tokens": output_tokens,
                "output_tokens_note": "estimated by whitespace word count x1.3; task_router does not expose tokenizer counts",
                "sustained_tokens_per_sec": round(output_tokens / total_s, 3) if output_tokens and total_s else None,
                "peak_ram_mb": round(max(peak_rss_mb() - rss_before, 0.0), 3),
                "peak_ram_note": "process ru_maxrss delta for harness invocation, not whole-host peak RAM",
                "cpu_notes": "single-process task_router invocation measured with time.perf_counter",
                "gpu_vram_notes": "not applicable; current orchestrator harness used repository task_router path without local GPU model",
                "verdict": verdict_for_output(scenario, output, ok),
                "ok": ok,
                "error": None,
                "failure_diagnosis": response.get("failure_diagnosis"),
                "workflow_benchmark": response.get("workflow_benchmark"),
                "output_preview": output[:1200],
            }
        except Exception as exc:  # keep comparison artifact complete across all scenarios
            ended = time.perf_counter()
            result = {
                "scenario_id": scenario["id"],
                "category": scenario["category"],
                "requested_output_format": scenario["requested_output_format"],
                "backend": "current-orchestrator",
                "orchestrator_backend": args.backend,
                "model": args.model,
                "first_token_latency_s": None,
                "first_token_latency_note": "not measured because scenario errored before response",
                "end_to_end_latency_s": round(ended - started, 3),
                "input_tokens": estimated_tokens(scenario["prompt"]),
                "output_tokens": 0,
                "sustained_tokens_per_sec": None,
                "peak_ram_mb": round(max(peak_rss_mb() - rss_before, 0.0), 3),
                "cpu_notes": "scenario ended with harness/import/runtime error",
                "gpu_vram_notes": "not applicable",
                "verdict": "blocked",
                "ok": False,
                "error": repr(exc),
                "output_preview": "",
            }
        results.append(result)

    completed = [r for r in results if not r.get("error")]
    measured_tps = [r["sustained_tokens_per_sec"] for r in results if r.get("sustained_tokens_per_sec") is not None]
    measured_latency = [r["end_to_end_latency_s"] for r in completed if r.get("end_to_end_latency_s") is not None]
    peak_ram_values = [r["peak_ram_mb"] for r in results if r.get("peak_ram_mb") is not None]
    verdict_counts: dict[str, int] = {}
    for result in results:
        verdict_counts[str(result.get("verdict", "unknown"))] = verdict_counts.get(str(result.get("verdict", "unknown")), 0) + 1

    aggregate = {
        "backend": "current-orchestrator",
        "orchestrator_backend": args.backend,
        "model": args.model,
        "scenario_count": len(results),
        "completed": len(completed),
        "median_tokens_per_sec": round(statistics.median(measured_tps), 3) if measured_tps else None,
        "median_first_token_latency_s": None,
        "first_token_latency_note": "not exposed by non-streaming task_router",
        "median_end_to_end_latency_s": round(statistics.median(measured_latency), 3) if measured_latency else None,
        "max_peak_ram_mb_delta": round(max(peak_ram_values), 3) if peak_ram_values else None,
        "verdict_counts": verdict_counts,
    }
    return {"aggregate": aggregate, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", default="tool_fallback")
    parser.add_argument("--model", default="current-orchestrator")
    parser.add_argument("--max-tokens", type=int, default=80)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--memory-session", default="qwen36-current-orchestrator-eval")
    args = parser.parse_args()
    data = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data["aggregate"], indent=2))


if __name__ == "__main__":
    main()
