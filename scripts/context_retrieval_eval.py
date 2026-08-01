# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Offline ContextBench-style regression evaluator for task routing context retrieval."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

FIXTURE_SET_SCHEMA = "context_retrieval_fixture_set.v1"
REPORT_SCHEMA = "context_retrieval_regression_report.v1"
SEARCH_FIXTURE_SET_SCHEMA = "context_search_benchmark_fixture_set.v1"
SEARCH_REPORT_SCHEMA = "context_search_benchmark_report.v1"
DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "context_retrieval_task_routing.json"
)
DEFAULT_SEARCH_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "context_retrieval_search_benchmark.json"
)
DEFAULT_THRESHOLDS = {
    "min_mean_recall": 0.8,
    "min_mean_efficiency": 0.55,
    "min_top_workflow_card_accuracy": 0.8,
}
SEARCH_MODES = ("grep", "vector", "hybrid")
OUTPUT_PROFILES = ("inline_text", "resource_link")

RouteFn = Callable[..., dict[str, Any]]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _round_metric(value: float) -> float:
    return round(float(value), 4)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9_]+", text.lower())
        if len(token) >= 3
    ]


def _anchor_key(anchor: Any) -> str:
    if isinstance(anchor, str):
        raw = anchor.strip()
        if not raw:
            raise ValueError("gold_context_anchors must not contain empty strings")
        if ":" in raw:
            return raw
        return f"workflow_card:{raw}"
    if isinstance(anchor, dict):
        anchor_type = str(anchor.get("type") or "workflow_card").strip()
        anchor_id = str(anchor.get("id") or "").strip()
        if not anchor_type or not anchor_id:
            raise ValueError("gold context anchor objects require non-empty type and id")
        return f"{anchor_type}:{anchor_id}"
    raise ValueError("gold context anchors must be strings or objects")


def _display_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(_repo_root()))
    except ValueError:
        return str(resolved)


def _load_task_router() -> RouteFn:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from source import server

    return server.task_router


def load_fixture_set(path: str | Path = DEFAULT_FIXTURE_PATH) -> dict[str, Any]:
    fixture_path = Path(path)
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("fixture set must be a JSON object")
    if payload.get("schema") != FIXTURE_SET_SCHEMA:
        raise ValueError(f"fixture set schema must be {FIXTURE_SET_SCHEMA}")
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("fixture set requires at least one fixture")
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise ValueError("each fixture must be a JSON object")
        for field in (
            "id",
            "coverage",
            "task",
            "prompt",
            "gold_context_anchors",
            "expected_top_workflow_card",
        ):
            if field not in fixture:
                raise ValueError(f"fixture missing required field: {field}")
        if not str(fixture.get("id") or "").strip():
            raise ValueError("fixture id must not be empty")
        if not str(fixture.get("coverage") or "").strip():
            raise ValueError(f"fixture {fixture.get('id')} coverage must not be empty")
        if not str(fixture.get("task") or "").strip():
            raise ValueError(f"fixture {fixture.get('id')} task must not be empty")
        if not str(fixture.get("prompt") or "").strip():
            raise ValueError(f"fixture {fixture.get('id')} prompt must not be empty")
        if not str(fixture.get("expected_top_workflow_card") or "").strip():
            raise ValueError(
                f"fixture {fixture.get('id')} expected_top_workflow_card must not be empty"
            )
        anchors = fixture.get("gold_context_anchors")
        if not isinstance(anchors, list) or not anchors:
            raise ValueError(
                f"fixture {fixture.get('id')} requires at least one gold context anchor"
            )
        [_anchor_key(anchor) for anchor in anchors]
    return payload


def _retrieved_workflow_card_anchors(selection: dict[str, Any]) -> list[str]:
    anchors: list[str] = []
    for match in selection.get("matches", []):
        if not isinstance(match, dict):
            continue
        card_id = str(match.get("id") or "").strip()
        if card_id:
            anchors.append(f"workflow_card:{card_id}")
    return anchors


def _metric_block(gold_anchors: list[str], retrieved_anchors: list[str]) -> dict[str, Any]:
    gold = set(gold_anchors)
    retrieved = set(retrieved_anchors)
    hits = sorted(gold.intersection(retrieved))
    ranks = {anchor: index + 1 for index, anchor in enumerate(retrieved_anchors)}

    recall = len(hits) / len(gold) if gold else 1.0
    precision = len(hits) / len(retrieved) if retrieved else (1.0 if not gold else 0.0)
    efficiency = (
        sum(1.0 / ranks[anchor] for anchor in gold if anchor in ranks) / len(gold)
        if gold
        else 1.0
    )
    return {
        "recall": _round_metric(recall),
        "precision": _round_metric(precision),
        "efficiency": _round_metric(efficiency),
        "gold_hit_count": len(hits),
        "retrieved_count": len(retrieved_anchors),
        "hits": hits,
        "misses": sorted(gold.difference(retrieved)),
        "extras": sorted(retrieved.difference(gold)),
    }


def evaluate_fixture(
    fixture: dict[str, Any],
    *,
    route_fn: RouteFn,
    default_top_k: int,
    top_k_override: int | None = None,
) -> dict[str, Any]:
    top_k = (
        top_k_override
        if top_k_override is not None
        else int(fixture.get("top_k") or default_top_k)
    )
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    selection = route_fn(
        mode="workflow_select",
        prompt=str(fixture["prompt"]),
        top_k=top_k,
        execution_mode=str(fixture.get("execution_mode") or "auto"),
    )
    retrieved_anchors = _retrieved_workflow_card_anchors(selection)
    gold_anchors = [_anchor_key(anchor) for anchor in fixture["gold_context_anchors"]]
    top_workflow_card = None
    matches = selection.get("matches", [])
    if matches and isinstance(matches[0], dict):
        top_workflow_card = matches[0].get("id")
    expected_top = fixture.get("expected_top_workflow_card")
    metrics = _metric_block(gold_anchors, retrieved_anchors)

    return {
        "id": fixture["id"],
        "coverage": fixture.get("coverage", fixture.get("task")),
        "task": fixture["task"],
        "prompt": fixture["prompt"],
        "top_k": top_k,
        "execution_mode": selection.get("execution_mode"),
        "gold_context_anchors": gold_anchors,
        "retrieved_context_anchors": retrieved_anchors,
        "top_workflow_card": top_workflow_card,
        "expected_top_workflow_card": expected_top,
        "top_workflow_card_match": bool(expected_top and top_workflow_card == expected_top),
        "metrics": metrics,
    }


def _load_search_fixture_set(
    path: str | Path = DEFAULT_SEARCH_FIXTURE_PATH,
) -> dict[str, Any]:
    fixture_path = Path(path)
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("search fixture set must be a JSON object")
    if payload.get("schema") != SEARCH_FIXTURE_SET_SCHEMA:
        raise ValueError(f"search fixture set schema must be {SEARCH_FIXTURE_SET_SCHEMA}")
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("search fixture set requires at least one fixture")
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise ValueError("each search fixture must be a JSON object")
        for field in ("id", "question", "evidence"):
            if field not in fixture:
                raise ValueError(f"search fixture missing required field: {field}")
        if not str(fixture.get("id") or "").strip():
            raise ValueError("search fixture id must not be empty")
        if not str(fixture.get("question") or "").strip():
            raise ValueError(
                f"search fixture {fixture.get('id')} question must not be empty"
            )
        evidence = fixture.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"search fixture {fixture.get('id')} requires evidence")
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError("evidence items must be JSON objects")
            if not str(item.get("path") or "").strip():
                raise ValueError("evidence item path must not be empty")
            anchors = item.get("anchors")
            if not isinstance(anchors, list) or not anchors:
                raise ValueError(f"evidence item {item.get('path')} requires anchors")
            for anchor in anchors:
                if not str(anchor or "").strip():
                    raise ValueError("evidence anchors must not be empty")
        for distractor in fixture.get("distractors", []):
            if not isinstance(distractor, dict):
                raise ValueError("distractors must be JSON objects")
            if not str(distractor.get("id") or "").strip():
                raise ValueError("distractor id must not be empty")
            if not str(distractor.get("text") or "").strip():
                raise ValueError("distractor text must not be empty")
    return payload


def load_search_fixture_set(
    path: str | Path = DEFAULT_SEARCH_FIXTURE_PATH,
) -> dict[str, Any]:
    """Load offline grep/vector benchmark fixtures."""

    return _load_search_fixture_set(path)


def _line_snippet(lines: list[str], line_index: int, *, radius: int = 1) -> str:
    start = max(0, line_index - radius)
    end = min(len(lines), line_index + radius + 1)
    return "\n".join(line.rstrip() for line in lines[start:end]).strip()


def _evidence_candidates(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    root = _repo_root()
    for item in fixture["evidence"]:
        rel_path = str(item["path"])
        full_path = root / rel_path
        try:
            lines = full_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for anchor in item["anchors"]:
            anchor_text = str(anchor)
            matching_index = next(
                (index for index, line in enumerate(lines) if anchor_text in line),
                None,
            )
            if matching_index is None:
                raise ValueError(f"anchor {anchor_text!r} not found in {rel_path}")
            candidates.append(
                {
                    "anchor": f"file:{rel_path}:{anchor_text}",
                    "kind": "evidence",
                    "path": rel_path,
                    "line": matching_index + 1,
                    "text": _line_snippet(lines, matching_index),
                }
            )
    return candidates


def _distractor_candidates(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "anchor": f"distractor:{fixture['id']}:{distractor['id']}",
            "kind": "distractor",
            "path": None,
            "line": None,
            "text": str(distractor["text"]),
        }
        for distractor in fixture.get("distractors", [])
    ]


def _vector_score(query_tokens: list[str], text_tokens: list[str]) -> float:
    if not query_tokens or not text_tokens:
        return 0.0
    query = Counter(query_tokens)
    text = Counter(text_tokens)
    dot = sum(query[token] * text.get(token, 0) for token in query)
    query_norm = math.sqrt(sum(count * count for count in query.values()))
    text_norm = math.sqrt(sum(count * count for count in text.values()))
    if not query_norm or not text_norm:
        return 0.0
    return dot / (query_norm * text_norm)


def _score_candidate(question: str, candidate: dict[str, Any], mode: str) -> float:
    query_tokens = _tokens(question)
    text = f"{candidate.get('path') or ''}\n{candidate['text']}"
    text_tokens = _tokens(text)
    grep_score = sum(text_tokens.count(token) for token in query_tokens)
    grep_norm = grep_score / max(1, len(query_tokens))
    vector_score = _vector_score(query_tokens, text_tokens)
    if mode == "grep":
        return float(grep_score)
    if mode == "vector":
        return vector_score
    if mode == "hybrid":
        return grep_norm + vector_score
    raise ValueError(f"unsupported search mode: {mode}")


def _render_search_result(candidate: dict[str, Any], profile: str) -> str:
    if profile == "inline_text":
        location = candidate["path"] or candidate["anchor"]
        line = f":{candidate['line']}" if candidate.get("line") else ""
        return f"{location}{line}\n{candidate['text']}"
    if profile == "resource_link":
        if candidate.get("path"):
            return f"repo://file/{candidate['path']}#L{candidate['line']}"
        return f"benchmark://distractor/{candidate['anchor']}"
    raise ValueError(f"unsupported output profile: {profile}")


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4)) if text else 0


def _evaluate_search_run(
    fixture: dict[str, Any],
    *,
    mode: str,
    output_profile: str,
    budget: int,
    include_distractors: bool,
) -> dict[str, Any]:
    if budget < 1:
        raise ValueError("budget must be >= 1")
    candidates = _evidence_candidates(fixture)
    if include_distractors:
        candidates.extend(_distractor_candidates(fixture))
    scored = []
    for index, candidate in enumerate(candidates):
        score = _score_candidate(str(fixture["question"]), candidate, mode)
        scored.append((score, candidate["kind"] != "evidence", index, candidate))
    ranked = [
        candidate
        for score, _is_distractor, _index, candidate in sorted(
            scored, key=lambda row: (-row[0], row[1], row[2])
        )
        if score > 0
    ][:budget]
    gold_anchors = [
        candidate["anchor"]
        for candidate in candidates
        if candidate["kind"] == "evidence"
    ]
    retrieved_anchors = [candidate["anchor"] for candidate in ranked]
    metrics = _metric_block(gold_anchors, retrieved_anchors)
    rendered = [_render_search_result(candidate, output_profile) for candidate in ranked]
    output_chars = sum(len(item) for item in rendered)
    return {
        "mode": mode,
        "output_profile": output_profile,
        "budget": budget,
        "include_distractors": include_distractors,
        "gold_evidence_anchors": gold_anchors,
        "retrieved_context_anchors": retrieved_anchors,
        "metrics": metrics,
        "output_estimates": {
            "returned_items": len(ranked),
            "output_chars": output_chars,
            "estimated_tokens": _estimate_tokens("\n".join(rendered)),
        },
    }


def _search_recommendation(runs: list[dict[str, Any]]) -> dict[str, Any]:
    robust_runs = [run for run in runs if run["include_distractors"]]
    if not robust_runs:
        robust_runs = runs
    best = max(
        robust_runs,
        key=lambda run: (
            run["metrics"]["recall"],
            run["metrics"]["precision"],
            run["metrics"]["efficiency"],
            -run["output_estimates"]["estimated_tokens"],
        ),
    )
    return {
        "default_router_change": False,
        "recommended_search_mode": best["mode"],
        "recommended_output_profile": best["output_profile"],
        "rationale": (
            "Advisory only: choose the strongest distractor-robust recall/precision "
            "within the equal retrieval budget, then prefer smaller output estimates."
        ),
    }


def evaluate_search_benchmark(
    fixture_path: str | Path = DEFAULT_SEARCH_FIXTURE_PATH,
    *,
    budget_override: int | None = None,
) -> dict[str, Any]:
    fixture_set = load_search_fixture_set(fixture_path)
    budget = int(budget_override or fixture_set.get("budget") or 2)
    if budget < 1:
        raise ValueError("budget must be >= 1")
    results = []
    for fixture in fixture_set["fixtures"]:
        runs = [
            _evaluate_search_run(
                fixture,
                mode=mode,
                output_profile=output_profile,
                budget=budget,
                include_distractors=include_distractors,
            )
            for include_distractors in (False, True)
            for mode in SEARCH_MODES
            for output_profile in OUTPUT_PROFILES
        ]
        clean = [run for run in runs if not run["include_distractors"]]
        noisy = [run for run in runs if run["include_distractors"]]
        results.append(
            {
                "id": fixture["id"],
                "question": fixture["question"],
                "runs": runs,
                "distractor_delta": {
                    "mean_recall": _round_metric(
                        _mean([run["metrics"]["recall"] for run in noisy])
                        - _mean([run["metrics"]["recall"] for run in clean])
                    ),
                    "mean_precision": _round_metric(
                        _mean([run["metrics"]["precision"] for run in noisy])
                        - _mean([run["metrics"]["precision"] for run in clean])
                    ),
                    "mean_efficiency": _round_metric(
                        _mean([run["metrics"]["efficiency"] for run in noisy])
                        - _mean([run["metrics"]["efficiency"] for run in clean])
                    ),
                },
                "recommendation": _search_recommendation(runs),
            }
        )
    noisy_runs = [
        run
        for result in results
        for run in result["runs"]
        if run["include_distractors"]
    ]
    return {
        "schema": SEARCH_REPORT_SCHEMA,
        "fixture_schema": fixture_set["schema"],
        "target": fixture_set.get("target", "offline grep/vector/hybrid context search"),
        "fixture_path": _display_path(fixture_path),
        "budget": budget,
        "summary": {
            "fixture_count": len(results),
            "search_modes": list(SEARCH_MODES),
            "output_profiles": list(OUTPUT_PROFILES),
            "mean_distractor_recall": _round_metric(
                _mean([run["metrics"]["recall"] for run in noisy_runs])
            ),
            "mean_distractor_precision": _round_metric(
                _mean([run["metrics"]["precision"] for run in noisy_runs])
            ),
            "mean_distractor_estimated_tokens": _round_metric(
                _mean(
                    [
                        run["output_estimates"]["estimated_tokens"]
                        for run in noisy_runs
                    ]
                )
            ),
            "default_router_change": False,
        },
        "results": results,
    }


def evaluate_context_retrieval(
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
    *,
    route_fn: RouteFn | None = None,
    top_k_override: int | None = None,
) -> dict[str, Any]:
    fixture_set = load_fixture_set(fixture_path)
    route = route_fn or _load_task_router()
    default_top_k = int(fixture_set.get("default_top_k") or 3)
    results = [
        evaluate_fixture(
            fixture,
            route_fn=route,
            default_top_k=default_top_k,
            top_k_override=top_k_override,
        )
        for fixture in fixture_set["fixtures"]
    ]

    recalls = [float(result["metrics"]["recall"]) for result in results]
    precisions = [float(result["metrics"]["precision"]) for result in results]
    efficiencies = [float(result["metrics"]["efficiency"]) for result in results]
    top_matches = [1.0 if result["top_workflow_card_match"] else 0.0 for result in results]
    thresholds = {**DEFAULT_THRESHOLDS, **fixture_set.get("thresholds", {})}
    mean_recall = _round_metric(_mean(recalls))
    mean_precision = _round_metric(_mean(precisions))
    mean_efficiency = _round_metric(_mean(efficiencies))
    top_accuracy = _round_metric(_mean(top_matches))
    passed_thresholds = (
        mean_recall >= float(thresholds["min_mean_recall"])
        and mean_efficiency >= float(thresholds["min_mean_efficiency"])
        and top_accuracy >= float(thresholds["min_top_workflow_card_accuracy"])
    )

    return {
        "schema": REPORT_SCHEMA,
        "fixture_schema": fixture_set["schema"],
        "target": fixture_set.get("target", "task_router(mode='workflow_select')"),
        "fixture_path": _display_path(fixture_path),
        "summary": {
            "fixture_count": len(results),
            "mean_recall": mean_recall,
            "mean_precision": mean_precision,
            "mean_efficiency": mean_efficiency,
            "top_workflow_card_accuracy": top_accuracy,
            "passed_thresholds": passed_thresholds,
        },
        "thresholds": thresholds,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate deterministic task-router context retrieval fixtures."
    )
    parser.add_argument(
        "--fixtures",
        default=str(DEFAULT_FIXTURE_PATH),
        help="Path to a context_retrieval_fixture_set.v1 JSON file.",
    )
    parser.add_argument(
        "--search-benchmark",
        action="store_true",
        help="Run the offline grep/vector/hybrid context-search benchmark mode.",
    )
    parser.add_argument(
        "--search-fixtures",
        default=str(DEFAULT_SEARCH_FIXTURE_PATH),
        help="Path to a context_search_benchmark_fixture_set.v1 JSON file.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Override fixture/default top_k, or search budget in benchmark mode.",
    )
    parser.add_argument(
        "--fail-on-threshold",
        action="store_true",
        help="Exit non-zero when summary thresholds are not met.",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON output indent.")
    args = parser.parse_args(argv)

    if args.search_benchmark:
        report = evaluate_search_benchmark(
            args.search_fixtures, budget_override=args.top_k
        )
        print(json.dumps(report, indent=args.indent, sort_keys=True))
        return 0

    report = evaluate_context_retrieval(args.fixtures, top_k_override=args.top_k)
    print(json.dumps(report, indent=args.indent, sort_keys=True))
    if args.fail_on_threshold and not report["summary"]["passed_thresholds"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
