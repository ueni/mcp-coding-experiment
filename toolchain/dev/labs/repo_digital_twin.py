#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Repo Digital Twin generator.

Captures repository metadata, structure, hotspots, and drift markers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], check=check)


def collect_tree(max_files: int) -> list[str]:
    cp = git("ls-files")
    files = [line.strip() for line in cp.stdout.splitlines() if line.strip()]
    return files[:max_files]


def hotspots(limit: int) -> list[dict[str, Any]]:
    cp = git("log", "--name-only", "--pretty=format:")
    counter: Counter[str] = Counter()
    for line in cp.stdout.splitlines():
        path = line.strip()
        if path:
            counter[path] += 1
    out: list[dict[str, Any]] = []
    for path, count in counter.most_common(limit):
        out.append({"path": path, "touches": count})
    return out


def dependency_hints(files: list[str]) -> dict[str, list[str]]:
    hints: dict[str, list[str]] = {}
    groups = {
        "python": [f for f in files if f.endswith(".py")],
        "docker": [
            f
            for f in files
            if "Dockerfile" in f or f.endswith(".yml") or f.endswith(".yaml")
        ],
        "docs": [f for f in files if f.lower().endswith(".md")],
    }
    for key, vals in groups.items():
        hints[key] = vals[:50]
    return hints


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate repo digital twin")
    parser.add_argument("--json", default=".build/reports/REPO_DIGITAL_TWIN.json")
    parser.add_argument("--md", default=".build/reports/REPO_DIGITAL_TWIN.md")
    parser.add_argument("--max-files", type=int, default=1000)
    parser.add_argument("--hotspot-limit", type=int, default=20)
    args = parser.parse_args()

    head = git("rev-parse", "HEAD").stdout.strip()
    branch = git("branch", "--show-current").stdout.strip() or "HEAD"
    dirty = bool(git("status", "--porcelain").stdout.strip())

    files = collect_tree(args.max_files)
    hs = hotspots(args.hotspot_limit)

    twin = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repo": {
            "branch": branch,
            "head": head,
            "dirty": dirty,
        },
        "inventory": {
            "tracked_file_count": len(files),
            "files": files,
        },
        "hotspots": hs,
        "dependency_hints": dependency_hints(files),
        "drift_markers": {
            "has_xray": Path("MCP_XRAY.md").exists(),
            "has_drift": Path("MCP_DRIFT.md").exists(),
            "has_gatekeeper_report": Path("POLICY_GATEKEEPER.md").exists(),
        },
    }

    json_path = Path(args.json)
    md_path = Path(args.md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(twin, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Repo Digital Twin",
        "",
        f"Generated: {twin['generated_at']}",
        f"Branch: `{branch}`",
        f"HEAD: `{head}`",
        f"Dirty: `{str(dirty).lower()}`",
        "",
        "## Inventory",
        "",
        f"- Tracked files: `{len(files)}`",
        "",
        "## Hotspots",
        "",
    ]
    if hs:
        for item in hs:
            lines.append(f"- `{item['path']}` touched `{item['touches']}` times")
    else:
        lines.append("- No hotspot data available")

    lines.extend(
        [
            "",
            "## Drift Markers",
            "",
            f"- `MCP_XRAY.md`: `{str(twin['drift_markers']['has_xray']).lower()}`",
            f"- `MCP_DRIFT.md`: `{str(twin['drift_markers']['has_drift']).lower()}`",
            f"- `POLICY_GATEKEEPER.md`: `{str(twin['drift_markers']['has_gatekeeper_report']).lower()}`",
            "",
            "JSON twin path:",
            f"- `{args.json}`",
        ]
    )

    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {args.json} and {args.md}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
