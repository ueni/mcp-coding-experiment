#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""CLI wrapper for the read-only AGENTS.md context health report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from source.agents_context_health import analyze_agents_context


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit a bounded AGENTS.md context health report.")
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory).")
    parser.add_argument("--path", default="AGENTS.md", help="Repository-relative AGENTS file path.")
    parser.add_argument("--token-budget", type=int, default=1600)
    parser.add_argument("--byte-budget", type=int, default=6000)
    parser.add_argument("--compact", action="store_true", help="Print only summary/budget/findings counts.")
    args = parser.parse_args()

    report = analyze_agents_context(
        Path(args.repo),
        path=args.path,
        token_budget=args.token_budget,
        byte_budget=args.byte_budget,
    )
    if args.compact:
        report = {
            "schema": report["schema"],
            "target": report["target"],
            "budget": report["budget"],
            "summary": report["summary"],
            "safety": report["safety"],
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("summary", {}).get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
