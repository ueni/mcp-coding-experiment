#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Refactor tournament runner.

Each strategy runs on its own branch, executes mutate/eval commands, and receives a score.
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


def run_shell(cmd: str) -> tuple[int, str, str, float]:
    start = dt.datetime.now(dt.timezone.utc)
    proc = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    end = dt.datetime.now(dt.timezone.utc)
    return proc.returncode, proc.stdout, proc.stderr, (end - start).total_seconds()


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], check=check)


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    return data


def ensure_clean_worktree(allow_dirty: bool) -> None:
    status = git("status", "--porcelain").stdout.strip()
    if status and not allow_dirty:
        raise RuntimeError(
            "working tree is dirty; commit/stash first or run with --allow-dirty"
        )


def changed_stats(base_ref: str) -> tuple[int, int, int]:
    cp = git("diff", "--shortstat", base_ref)
    text = cp.stdout.strip()
    files = insertions = deletions = 0
    if text:
        for part in text.split(","):
            token = part.strip()
            if "file changed" in token or "files changed" in token:
                files = int(token.split()[0])
            elif "insertion" in token or "insertions" in token:
                insertions = int(token.split()[0])
            elif "deletion" in token or "deletions" in token:
                deletions = int(token.split()[0])
    return files, insertions, deletions


def delta_stats(
    before: tuple[int, int, int], after: tuple[int, int, int]
) -> tuple[int, int, int]:
    before_files, before_insertions, before_deletions = before
    after_files, after_insertions, after_deletions = after
    file_delta = after_files - before_files
    if file_delta < 0:
        file_delta = after_files
    return (
        file_delta,
        max(0, after_insertions - before_insertions),
        max(0, after_deletions - before_deletions),
    )


def score_strategy(
    checks: list[dict[str, Any]], files: int, insertions: int, deletions: int
) -> int:
    pass_count = sum(1 for c in checks if c["ok"])
    fail_count = len(checks) - pass_count
    churn = insertions + deletions
    # Bias toward passing checks, then lower churn.
    return (pass_count * 100) - (fail_count * 120) - files - (churn // 10)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a refactor tournament")
    parser.add_argument(
        "--config",
        default=".config/labs/refactor_tournament.json",
        help="Path to tournament JSON config",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow running with a dirty worktree",
    )
    parser.add_argument(
        "--keep-branches",
        action="store_true",
        help="Keep strategy branches after run",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    base_ref = str(cfg.get("base_ref", "HEAD"))
    report_path = Path(
        str(cfg.get("report_path", ".codebase-tooling-mcp/reports/REFACTOR_TOURNAMENT.md"))
    )
    strategies = cfg.get("strategies", [])
    if not isinstance(strategies, list) or not strategies:
        raise ValueError("strategies must be a non-empty list")

    ensure_clean_worktree(args.allow_dirty)

    original_branch = git("branch", "--show-current").stdout.strip() or "HEAD"

    summary: list[dict[str, Any]] = []

    try:
        for strategy in strategies:
            if not isinstance(strategy, dict):
                raise ValueError("each strategy must be an object")

            name = str(strategy.get("name", "unnamed"))
            branch = str(
                strategy.get("branch", f"tournament/{name.lower().replace(' ', '-')}")
            ).strip()
            mutate = strategy.get("mutate", [])
            checks = strategy.get("checks", [])
            allowed_mutate_exit_codes = strategy.get("allowed_mutate_exit_codes", [0])

            if not isinstance(mutate, list) or not all(
                isinstance(c, str) for c in mutate
            ):
                raise ValueError(f"strategy '{name}' has invalid mutate list")
            if not isinstance(checks, list) or not all(
                isinstance(c, str) for c in checks
            ):
                raise ValueError(f"strategy '{name}' has invalid checks list")
            if not isinstance(allowed_mutate_exit_codes, list) or not all(
                isinstance(code, int) for code in allowed_mutate_exit_codes
            ):
                raise ValueError(
                    f"strategy '{name}' has invalid allowed_mutate_exit_codes"
                )

            git("checkout", "-B", branch, base_ref)
            baseline_stats = changed_stats(base_ref)

            mutate_results: list[dict[str, Any]] = []
            for cmd in mutate:
                rc, out, err, seconds = run_shell(cmd)
                ok = rc in allowed_mutate_exit_codes
                mutate_results.append(
                    {
                        "command": cmd,
                        "ok": ok,
                        "exit_code": rc,
                        "seconds": round(seconds, 3),
                        "stdout": out.strip(),
                        "stderr": err.strip(),
                    }
                )
                if not ok:
                    break

            check_results: list[dict[str, Any]] = []
            if all(item["ok"] for item in mutate_results):
                for cmd in checks:
                    rc, out, err, seconds = run_shell(cmd)
                    check_results.append(
                        {
                            "command": cmd,
                            "ok": rc == 0,
                            "exit_code": rc,
                            "seconds": round(seconds, 3),
                            "stdout": out.strip(),
                            "stderr": err.strip(),
                        }
                    )

            files, insertions, deletions = delta_stats(
                baseline_stats, changed_stats(base_ref)
            )
            score = score_strategy(check_results, files, insertions, deletions)
            summary.append(
                {
                    "name": name,
                    "branch": branch,
                    "mutate": mutate_results,
                    "checks": check_results,
                    "files": files,
                    "insertions": insertions,
                    "deletions": deletions,
                    "score": score,
                    "pass_count": sum(1 for c in check_results if c["ok"]),
                }
            )

            if not args.keep_branches:
                git("checkout", original_branch)
                git("branch", "-D", branch, check=False)

    finally:
        git("checkout", original_branch, check=False)

    ranking = sorted(summary, key=lambda item: item["score"], reverse=True)

    lines = [
        "# Refactor Tournament Report",
        "",
        f"Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}",
        f"Base ref: `{base_ref}`",
        f"Original branch: `{original_branch}`",
        "",
        "## Leaderboard",
        "",
        "| Rank | Strategy | Branch | Score | Passed Checks | Churn (+/-) |",
        "|---:|---|---|---:|---:|---:|",
    ]

    for idx, item in enumerate(ranking, start=1):
        churn = item["insertions"] + item["deletions"]
        lines.append(
            f"| {idx} | {item['name']} | `{item['branch']}` | {item['score']} | {item['pass_count']} | {churn} |"
        )

    for item in ranking:
        lines.extend(
            [
                "",
                f"## {item['name']}",
                "",
                f"- Branch: `{item['branch']}`",
                f"- Score: `{item['score']}`",
                f"- Files changed: `{item['files']}`",
                f"- Insertions: `{item['insertions']}`",
                f"- Deletions: `{item['deletions']}`",
                "",
                "### Mutate Steps",
                "",
            ]
        )

        if not item["mutate"]:
            lines.append("- None")
        else:
            for step in item["mutate"]:
                status = "PASS" if step["ok"] else "FAIL"
                lines.append(
                    f"- `{step['command']}` -> {status} (exit={step['exit_code']}, {step['seconds']}s)"
                )

        lines.extend(["", "### Check Steps", ""])
        if not item["checks"]:
            lines.append("- None")
        else:
            for check in item["checks"]:
                status = "PASS" if check["ok"] else "FAIL"
                lines.append(
                    f"- `{check['command']}` -> {status} (exit={check['exit_code']}, {check['seconds']}s)"
                )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - top-level CLI guard
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
