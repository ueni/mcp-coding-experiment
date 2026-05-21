#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""CLI for read-only AGENTS.md context-health linting."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module():
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from source.agents_context_lint import (
        analyze_agents_context,
        evaluate_context_effectiveness,
    )

    return analyze_agents_context, evaluate_context_effectiveness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only AGENTS.md minimal-context lint and routing effectiveness checks."
    )
    parser.add_argument("--repo-root", default=str(_repo_root()), help="Repository root to inspect.")
    parser.add_argument(
        "--context-file",
        action="append",
        dest="context_files",
        default=None,
        help="Repository-relative context file to inspect. Defaults to AGENTS.md.",
    )
    parser.add_argument("--max-bytes", type=int, default=12_000, help="Advisory byte budget.")
    parser.add_argument(
        "--max-estimated-tokens", type=int, default=3_000, help="Advisory token budget."
    )
    parser.add_argument(
        "--include-regression",
        action="store_true",
        help="Also run task-router workflow-selection effectiveness fixtures.",
    )
    parser.add_argument(
        "--regression-fixtures",
        default=str(_repo_root() / "tests" / "fixtures" / "agents_context_minimal_routing.json"),
        help="Path to agents_context_effectiveness_fixture_set.v1 fixtures.",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit non-zero for lint warnings/errors. Default is advisory success unless errors are present.",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit non-zero when regression thresholds fail.",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON indentation.")
    args = parser.parse_args(argv)

    analyze_agents_context, evaluate_context_effectiveness = _load_module()
    report = analyze_agents_context(
        args.repo_root,
        context_files=args.context_files or ("AGENTS.md",),
        max_bytes=args.max_bytes,
        max_estimated_tokens=args.max_estimated_tokens,
    )
    if args.include_regression:
        report["effectiveness_regression"] = evaluate_context_effectiveness(
            args.regression_fixtures,
            repo_root=args.repo_root,
        )
    print(json.dumps(report, indent=args.indent, sort_keys=True))

    if not report.get("ok", False):
        return 1
    if args.fail_on_findings and report.get("status") != "clean":
        return 1
    regression = report.get("effectiveness_regression")
    if (
        args.fail_on_regression
        and isinstance(regression, dict)
        and not regression.get("summary", {}).get("passed_thresholds", False)
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
