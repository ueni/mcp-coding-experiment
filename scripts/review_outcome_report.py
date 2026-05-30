#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Run the offline review-comment outcome report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    root = _repo_root()
    sys.path.insert(0, str(root))

    from source.review_outcome_report import (  # noqa: PLC0415
        DEFAULT_FIXTURE_DIR,
        generate_review_outcome_report,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture-dir",
        default=str(DEFAULT_FIXTURE_DIR),
        help="fixture pack directory (default: tests/fixtures/review_outcomes)",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="also write redacted JSON and Markdown exports under .codebase-tooling-mcp/reports",
    )
    args = parser.parse_args()

    result = generate_review_outcome_report(
        args.fixture_dir,
        repo_root=root,
        export=args.export,
    )
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
