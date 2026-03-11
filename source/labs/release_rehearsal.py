#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Release rehearsal engine for this repository.

This script creates a temporary rehearsal branch, runs checks, synthesizes a changelog,
and writes a release rehearsal report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shlex
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
    seconds = (end - start).total_seconds()
    return proc.returncode, proc.stdout, proc.stderr, seconds


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


def synthesize_changelog(log_from: str, log_to: str) -> str:
    try:
        cp = git("log", "--oneline", "--decorate", f"{log_from}..{log_to}")
    except subprocess.CalledProcessError:
        cp = git("log", "--oneline", "--decorate", log_to)
    lines = [line for line in cp.stdout.splitlines() if line.strip()]
    if not lines:
        return "- No commits in selected range"
    return "\n".join(f"- {line}" for line in lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a release rehearsal")
    parser.add_argument(
        "--config",
        default=".config/labs/release_rehearsal.json",
        help="Path to rehearsal JSON config",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow running with a dirty git worktree",
    )
    parser.add_argument(
        "--keep-branch",
        action="store_true",
        help="Keep the generated rehearsal branch",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    checks = cfg.get("checks", [])
    artifacts = cfg.get("artifacts", [])
    target_branch = str(cfg.get("target_branch", "master"))
    changelog_from = str(cfg.get("changelog_from", "HEAD~5"))
    report_path = Path(
        str(cfg.get("report_path", ".build/reports/RELEASE_REHEARSAL.md"))
    )

    if not isinstance(checks, list) or not all(isinstance(c, str) for c in checks):
        raise ValueError("checks must be a list of shell command strings")

    ensure_clean_worktree(args.allow_dirty)

    original_branch = git("branch", "--show-current").stdout.strip() or "HEAD"
    head = git("rev-parse", "HEAD").stdout.strip()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rehearsal_branch = f"rehearsal/{target_branch}/{stamp}"

    results: list[dict[str, Any]] = []
    created_branch = False
    all_ok = False

    try:
        git("checkout", "-b", rehearsal_branch)
        created_branch = True

        for command in checks:
            rc, out, err, seconds = run_shell(command)
            results.append(
                {
                    "command": command,
                    "ok": rc == 0,
                    "exit_code": rc,
                    "seconds": round(seconds, 3),
                    "stdout": out.strip(),
                    "stderr": err.strip(),
                }
            )

        changelog = synthesize_changelog(changelog_from, "HEAD")
        checkpoint_tag = f"rehearsal-checkpoint-{stamp}"
        all_ok = all(item["ok"] for item in results) if results else True

        report_lines = [
            "# Release Rehearsal Report",
            "",
            f"Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}",
            f"Target branch: `{target_branch}`",
            f"Original branch: `{original_branch}`",
            f"Original HEAD: `{head}`",
            f"Rehearsal branch: `{rehearsal_branch}`",
            f"Result: `{'PASS' if all_ok else 'FAIL'}`",
            "",
            "## Release Checklist",
            "",
            "- Changelog synthesized",
            "- Preflight checks executed",
            "- Rollback checkpoint proposed",
            "",
            "## Rollback Plan",
            "",
            f"- Suggested checkpoint tag: `{checkpoint_tag}`",
            f"- Return to original state: `git checkout {shlex.quote(original_branch)}`",
            f"- Reset to pre-rehearsal commit: `git reset --hard {head}` (only if needed)",
            "",
            "## Changelog (Rehearsed)",
            "",
            changelog,
            "",
            "## Check Results",
            "",
            "| Command | Status | Exit | Seconds |",
            "|---|---|---:|---:|",
        ]

        for item in results:
            status = "PASS" if item["ok"] else "FAIL"
            report_lines.append(
                f"| `{item['command']}` | {status} | {item['exit_code']} | {item['seconds']} |"
            )

        if artifacts:
            report_lines.extend(["", "## Artifact Presence", ""])
            for rel in artifacts:
                exists = Path(rel).exists()
                report_lines.append(f"- `{rel}`: `{'found' if exists else 'missing'}`")

        report_lines.extend(["", "## Command Logs", ""])
        for idx, item in enumerate(results, start=1):
            report_lines.extend(
                [
                    f"### {idx}. `{item['command']}`",
                    "",
                    "```text",
                    item["stdout"] or "<no stdout>",
                    "```",
                    "",
                ]
            )
            if item["stderr"]:
                report_lines.extend(
                    [
                        "stderr:",
                        "```text",
                        item["stderr"],
                        "```",
                        "",
                    ]
                )

        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            "\n".join(report_lines).rstrip() + "\n", encoding="utf-8"
        )

    finally:
        # Return the user to the original branch when possible.
        git("checkout", original_branch, check=False)
        if created_branch and (not args.keep_branch):
            git("branch", "-D", rehearsal_branch, check=False)

    print(f"Wrote {report_path}")
    return 0 if all_ok else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - top-level CLI guard
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
