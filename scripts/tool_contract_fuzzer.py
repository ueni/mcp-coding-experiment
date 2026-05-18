#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""CLI wrapper for deterministic MCP tool-contract behavioral fuzzing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    from source.tool_contract_fuzzer import main

    raise SystemExit(main())
