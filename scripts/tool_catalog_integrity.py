#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Check or refresh the checked-in public MCP tool-catalog baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_server():
    root = _repo_root()
    sys.path.insert(0, str(root))
    from source import server  # noqa: PLC0415

    return server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="fail if the live catalog differs from the checked-in baseline")
    action.add_argument("--write", action="store_true", help="rewrite source/tool_catalog_baseline.json from the live catalog")
    action.add_argument("--print-current", action="store_true", help="print the live canonical catalog JSON")
    args = parser.parse_args()

    server = _load_server()
    if args.write:
        catalog = server._current_tool_catalog_baseline()
        from source.tool_catalog_integrity import write_baseline  # noqa: PLC0415

        write_baseline(catalog)
        print(
            "PASS wrote source/tool_catalog_baseline.json "
            f"digest={catalog['whole_catalog_digest']} tools={catalog['tool_count']}"
        )
        return 0
    if args.print_current:
        catalog = server._current_tool_catalog_baseline()
        print(json.dumps(catalog, indent=2, sort_keys=True, ensure_ascii=True))
        return 0

    report = server.tool_catalog_integrity()
    drift = report.get("drift", {}).get("summary", {})
    lint = report.get("lint", {})
    print(
        f"{'PASS' if report.get('ok') else 'FAIL'} status={report.get('status')} "
        f"digest={report.get('current', {}).get('whole_catalog_digest', '')} "
        f"tools={report.get('current', {}).get('tool_count', 0)} "
        f"drift={drift} lint_findings={lint.get('finding_count', 0)}"
    )
    if not report.get("ok"):
        print(json.dumps(report.get("drift", {}), indent=2, sort_keys=True, ensure_ascii=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
