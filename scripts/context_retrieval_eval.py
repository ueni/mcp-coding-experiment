# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Offline ContextBench-style regression evaluator for task routing context retrieval."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

FIXTURE_SET_SCHEMA = "context_retrieval_fixture_set.v1"
REPORT_SCHEMA = "context_retrieval_regression_report.v1"
DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "context_retrieval_task_routing.json"
)
DEFAULT_THRESHOLDS = {
    "min_mean_recall": 0.8,
    "min_mean_efficiency": 0.55,
    "min_top_workflow_card_accuracy": 0.8,
}

RouteFn = Callable[..., dict[str, Any]]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _round_metric(value: float) -> float:
    return round(float(value), 4)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


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
        for field in ("id", "task", "prompt", "gold_context_anchors"):
            if field not in fixture:
                raise ValueError(f"fixture missing required field: {field}")
        if not str(fixture.get("id") or "").strip():
            raise ValueError("fixture id must not be empty")
        if not str(fixture.get("task") or "").strip():
            raise ValueError(f"fixture {fixture.get('id')} task must not be empty")
        if not str(fixture.get("prompt") or "").strip():
            raise ValueError(f"fixture {fixture.get('id')} prompt must not be empty")
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
    top_k = top_k_override if top_k_override is not None else int(fixture.get("top_k") or default_top_k)
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
        "--top-k",
        type=int,
        default=None,
        help="Override fixture/default top_k for smoke experiments.",
    )
    parser.add_argument(
        "--fail-on-threshold",
        action="store_true",
        help="Exit non-zero when summary thresholds are not met.",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON output indent.")
    args = parser.parse_args(argv)

    report = evaluate_context_retrieval(args.fixtures, top_k_override=args.top_k)
    print(json.dumps(report, indent=args.indent, sort_keys=True))
    if args.fail_on_threshold and not report["summary"]["passed_thresholds"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
