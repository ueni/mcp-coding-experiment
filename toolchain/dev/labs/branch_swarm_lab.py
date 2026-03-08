#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Branch Swarm Benchmark Lab.

Runs multiple implementation strategies on dedicated branches and benchmarks each.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], check=check)


def sh(cmd: str) -> tuple[int, str, str, float]:
    start = dt.datetime.now(dt.timezone.utc)
    proc = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    end = dt.datetime.now(dt.timezone.utc)
    return (
        proc.returncode,
        proc.stdout.strip(),
        proc.stderr.strip(),
        (end - start).total_seconds(),
    )


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    return data


def ensure_clean_worktree(allow_dirty: bool) -> None:
    status = git("status", "--porcelain").stdout.strip()
    if status and not allow_dirty:
        raise RuntimeError("working tree is dirty; run with --allow-dirty or clean it")


def parse_metric(stdout: str) -> float:
    # Accept either a plain float on stdout or trailing float token.
    text = stdout.strip()
    if not text:
        return float("inf")
    try:
        return float(text)
    except ValueError:
        tokens = text.replace(",", " ").split()
        for tok in reversed(tokens):
            try:
                return float(tok)
            except ValueError:
                continue
    return float("inf")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run branch swarm benchmark lab")
    parser.add_argument("--config", default=".config/dev/labs/branch_swarm_lab.json")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--keep-branches", action="store_true")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    base_ref = str(cfg.get("base_ref", "HEAD"))
    report_path = Path(
        str(cfg.get("report_path", ".build/reports/BRANCH_SWARM_REPORT.md"))
    )
    strategies = cfg.get("strategies", [])

    if not isinstance(strategies, list) or not strategies:
        raise ValueError("strategies must be a non-empty list")

    ensure_clean_worktree(args.allow_dirty)
    original = git("branch", "--show-current").stdout.strip() or "HEAD"

    rows: list[dict[str, Any]] = []

    try:
        for strategy in strategies:
            if not isinstance(strategy, dict):
                raise ValueError("strategy entries must be objects")
            name = str(strategy.get("name", "unnamed"))
            branch = str(
                strategy.get("branch", f"swarm/{name.lower().replace(' ', '-')}")
            ).strip()
            setup = strategy.get("setup", [])
            benchmark = strategy.get("benchmark", [])
            quality = strategy.get("quality", [])

            for key, val in [
                ("setup", setup),
                ("benchmark", benchmark),
                ("quality", quality),
            ]:
                if not isinstance(val, list) or not all(
                    isinstance(x, str) for x in val
                ):
                    raise ValueError(f"{name}: {key} must be list[str]")

            git("checkout", "-B", branch, base_ref)

            setup_ok = True
            step_logs: list[str] = []
            for cmd in setup:
                rc, out, err, secs = sh(cmd)
                step_logs.append(f"setup | {cmd} | rc={rc} | {secs:.3f}s")
                if rc != 0:
                    setup_ok = False
                    if err:
                        step_logs.append(f"stderr: {err}")
                    break

            quality_pass = 0
            quality_total = len(quality)
            for cmd in quality if setup_ok else []:
                rc, out, err, secs = sh(cmd)
                step_logs.append(f"quality | {cmd} | rc={rc} | {secs:.3f}s")
                if rc == 0:
                    quality_pass += 1
                elif err:
                    step_logs.append(f"stderr: {err}")

            metrics: list[float] = []
            for cmd in benchmark if setup_ok else []:
                rc, out, err, secs = sh(cmd)
                step_logs.append(
                    f"benchmark | {cmd} | rc={rc} | {secs:.3f}s | out={out}"
                )
                if rc == 0:
                    metrics.append(parse_metric(out))
                else:
                    metrics.append(float("inf"))
                    if err:
                        step_logs.append(f"stderr: {err}")

            primary_metric = min(metrics) if metrics else float("inf")
            score = (quality_pass * 100) - int(
                primary_metric if primary_metric != float("inf") else 100000
            )

            rows.append(
                {
                    "name": name,
                    "branch": branch,
                    "setup_ok": setup_ok,
                    "quality_pass": quality_pass,
                    "quality_total": quality_total,
                    "primary_metric": primary_metric,
                    "score": score,
                    "logs": step_logs,
                }
            )

            if not args.keep_branches:
                git("checkout", original)
                git("branch", "-D", branch, check=False)
    finally:
        git("checkout", original, check=False)

    ranking = sorted(rows, key=lambda r: r["score"], reverse=True)

    lines = [
        "# Branch Swarm Benchmark Report",
        "",
        f"Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}",
        f"Base ref: `{base_ref}`",
        "",
        "## Leaderboard",
        "",
        "| Rank | Strategy | Score | Quality | Primary Metric (lower is better) |",
        "|---:|---|---:|---:|---:|",
    ]

    for idx, row in enumerate(ranking, start=1):
        pm = (
            "inf"
            if row["primary_metric"] == float("inf")
            else f"{row['primary_metric']:.3f}"
        )
        lines.append(
            f"| {idx} | {row['name']} | {row['score']} | {row['quality_pass']}/{row['quality_total']} | {pm} |"
        )

    for row in ranking:
        lines.extend(["", f"## {row['name']}", ""])
        lines.append(f"- Branch: `{row['branch']}`")
        lines.append(f"- Setup OK: `{str(row['setup_ok']).lower()}`")
        lines.append(f"- Quality: `{row['quality_pass']}/{row['quality_total']}`")
        pm = (
            "inf"
            if row["primary_metric"] == float("inf")
            else f"{row['primary_metric']:.3f}"
        )
        lines.append(f"- Primary metric: `{pm}`")
        lines.append("")
        lines.append("```text")
        lines.extend(row["logs"])
        lines.append("```")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
