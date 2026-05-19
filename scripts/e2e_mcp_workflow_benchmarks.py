# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""CLI wrapper for the offline E2E MCP workflow benchmark harness."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _main() -> int:
    from evaluation.e2e_mcp_workflows.runner import main

    return main()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
