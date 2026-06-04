#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Print the offline HTTP/MCP contract parity report as JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from source.http_mcp_contract_parity import (  # noqa: E402
    DEFAULT_CONTRACT_PATH,
    generate_http_mcp_contract_parity_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-path", default=str(DEFAULT_CONTRACT_PATH))
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--include-passes", action="store_true")
    args = parser.parse_args()

    report = generate_http_mcp_contract_parity_report(
        args.contract_path,
        repo_root=Path(args.repo_root),
        include_passes=args.include_passes,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
