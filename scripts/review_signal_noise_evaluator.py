#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Run the offline code-review signal/noise fixture evaluator."""

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

    from source.review_signal_noise_evaluator import (  # noqa: PLC0415
        DEFAULT_FIXTURE_DIR,
        evaluate_review_fixtures,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture-dir",
        default=str(DEFAULT_FIXTURE_DIR),
        help="fixture pack directory (default: tests/fixtures/review_evaluation)",
    )
    parser.add_argument(
        "--actual-output-dir",
        default=None,
        help="optional directory with <fixture-id>.json review outputs to score",
    )
    parser.add_argument(
        "--min-precision",
        type=float,
        default=None,
        help="override the fixture pack minimum precision threshold",
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        default=None,
        help="override the fixture pack minimum recall threshold",
    )
    parser.add_argument(
        "--max-spurious-findings",
        type=int,
        default=None,
        help="override the maximum allowed spurious findings threshold",
    )
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="print the structured result but exit 0 even when thresholds fail",
    )
    args = parser.parse_args()

    thresholds = {
        "min_precision": args.min_precision,
        "min_recall": args.min_recall,
        "max_spurious_findings": args.max_spurious_findings,
    }
    result = evaluate_review_fixtures(
        args.fixture_dir,
        actual_output_dir=args.actual_output_dir,
        thresholds=thresholds,
        repo_root=root,
    )
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
    return 0 if result["ok"] or args.no_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
