#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Narrated PR Generator.

Produces a high-signal PR packet from git range data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], check=check)


def top_changed_files(
    base: str, head: str, limit: int = 15
) -> list[tuple[str, int, int]]:
    cp = git("diff", "--numstat", f"{base}..{head}")
    rows = []
    for line in cp.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        ins, dele, path = parts
        try:
            i = int(ins)
            d = int(dele)
        except ValueError:
            i = 0
            d = 0
        rows.append((path, i, d))
    rows.sort(key=lambda x: x[1] + x[2], reverse=True)
    return rows[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate narrated PR packet")
    parser.add_argument("--base", default="HEAD~1")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--output", default=".build/reports/PR_PACKET.md")
    args = parser.parse_args()

    base = args.base
    head = args.head

    commits = git("log", "--oneline", "--decorate", f"{base}..{head}").stdout.strip()
    if not commits:
        commits = "<no commits in range>"

    stat = (
        git("diff", "--shortstat", f"{base}..{head}").stdout.strip()
        or "No file changes"
    )
    files = top_changed_files(base, head)

    changed_list = git("diff", "--name-only", f"{base}..{head}").stdout.splitlines()
    risky = [p for p in changed_list if p.startswith("source/server.py")]

    lines = [
        "# PR Packet",
        "",
        f"Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}",
        f"Range: `{base}..{head}`",
        "",
        "## Intent",
        "",
        "- Summarize and review the proposed changes in this range.",
        "- Highlight risk, validation signals, and rollback guidance.",
        "",
        "## Change Summary",
        "",
        f"- Diff stats: {stat}",
        "",
        "## Commit Timeline",
        "",
        "```text",
        commits,
        "```",
        "",
        "## Architecture Impact",
        "",
    ]

    if files:
        lines.append("- Highest churn files:")
        for path, ins, dele in files:
            lines.append(f"  - `{path}` (+{ins}/-{dele})")
    else:
        lines.append("- No file churn detected")

    lines.extend(
        [
            "",
            "## Risk Hotspots",
            "",
            (
                "- Core server/runtime files changed"
                if risky
                else "- No core runtime hotspot files detected"
            ),
            "- Verify backward compatibility for MCP tool arguments and defaults",
            "- Verify transport behavior (`http` vs `stdio`) remains stable",
            "",
            "## Reviewer Checklist",
            "",
            "- [ ] Tool behavior compatibility checked",
            "- [ ] Error handling and path safety verified",
            "- [ ] Docs updated for any interface or workflow change",
            "- [ ] Rollback plan validated",
            "",
            "## Validation Commands",
            "",
            "```bash",
            "python -m py_compile source/server.py",
            "python -m py_compile source/labs/*.py",
            "git diff --name-only " + f"{base}..{head}",
            "```",
            "",
            "## Rollback Plan",
            "",
            f"- Revert range: `git revert --no-commit {base}..{head}`",
            f"- Hard reset fallback (last resort): `git reset --hard {base}`",
            "",
            "## Open Questions",
            "",
            "- Are any newly added scripts expected to be production-critical?",
            "- Should these workflows be exposed as first-class MCP tools?",
        ]
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
