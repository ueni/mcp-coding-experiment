#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT
"""Audit Docker build download-cache invariants.

The check is intentionally static and fast enough for unit tests/CI. It verifies
that network-facing Dockerfile download paths are routed through explicit
BuildKit cache mounts and helper functions so an early Dockerfile edit does not
force fresh downloads when the cache already contains the resource.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCKERFILE = PROJECT_ROOT / "source" / "Dockerfile"

REQUIRED_CACHE_IDS = {
    "codebase-tooling-apt-cache",
    "codebase-tooling-apt-lists",
    "codebase-tooling-build-downloads",
    "codebase-tooling-pip",
    "codebase-tooling-pip-wheelhouse",
    "codebase-tooling-ollama-binary",
    "codebase-tooling-ollama-models",
    "codebase-tooling-vscode-vsix",
}

REQUIRED_SNIPPETS = {
    "offline_arg": "ARG MCP_BUILD_OFFLINE=false",
    "refresh_arg": "ARG MCP_REFRESH_BUILD_DOWNLOAD_CACHE=false",
    "helper_copy": "COPY build-download-cache.sh /usr/local/bin/build-download-cache.sh",
    "generic_cached_download": "build_cache_download",
    "resumable_cached_download": "--continue-at -",
    "bounded_download_retries": "BUILD_CACHE_DOWNLOAD_RETRIES",
    "pip_wheelhouse_helper": "build_cache_pip_install",
    "pip_no_index_helper": "--no-index --find-links",
    "offline_model_guard": "missing from the BuildKit cache while MCP_BUILD_OFFLINE=true",
    "apt_no_download": "apt_install_args=(--no-download)",
}

EXTERNAL_URL = re.compile(r"https?://(?!127\.0\.0\.1|localhost)")
CACHE_ID = re.compile(r"id=(codebase-tooling-[A-Za-z0-9_.-]+)")
PIP_REQUIREMENTS_INSTALL = re.compile(r"\bpip\s+install\b.*\s-r\s+requirements", re.IGNORECASE)
PIP_DOWNLOAD = re.compile(r"\bpip\s+download\b", re.IGNORECASE)


def _line_in_function(lines: list[str], index: int, name: str) -> bool:
    """Return whether line index is inside a simple shell function body."""
    start = None
    for candidate in range(index, -1, -1):
        if f"{name}()" in lines[candidate]:
            start = candidate
            break
        if lines[candidate].lstrip().startswith("RUN ") and candidate != index:
            break
    if start is None:
        return False
    for candidate in range(start + 1, index + 1):
        if lines[candidate].strip() == "}" or lines[candidate].strip() == "}; \\":
            return False
    return True


def audit_dockerfile(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    helper_path = path.with_name("build-download-cache.sh")
    if not helper_path.exists():
        helper_path = PROJECT_ROOT / "source" / "build-download-cache.sh"
    helper_text = helper_path.read_text(encoding="utf-8") if helper_path.exists() else ""
    contract_text = text + "\n" + helper_text
    lines = text.splitlines()
    cache_ids = sorted(set(CACHE_ID.findall(text)))
    problems: list[str] = []

    missing_cache_ids = sorted(REQUIRED_CACHE_IDS.difference(cache_ids))
    for cache_id in missing_cache_ids:
        problems.append(f"missing required BuildKit cache id: {cache_id}")

    for label, snippet in REQUIRED_SNIPPETS.items():
        if snippet not in contract_text:
            problems.append(f"missing required cache contract snippet ({label}): {snippet}")

    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if "curl" in stripped and EXTERNAL_URL.search(stripped):
            allowed = (
                "build_cache_download" in stripped
                or "build_cache_url_exists" in stripped
                or _line_in_function(lines, line_no - 1, "build_cache_download")
                or _line_in_function(lines, line_no - 1, "build_cache_url_exists")
            )
            if not allowed:
                problems.append(f"line {line_no}: external curl is not routed through the cached download helper")
        if PIP_REQUIREMENTS_INSTALL.search(stripped) or PIP_DOWNLOAD.search(stripped):
            problems.append(f"line {line_no}: raw pip requirement download/install bypasses build_cache_pip_install")
        if "ollama pull" in stripped:
            window = "\n".join(lines[max(0, line_no - 12) : line_no + 1])
            if "MCP_BUILD_OFFLINE" not in window:
                problems.append(f"line {line_no}: ollama pull is missing a nearby MCP_BUILD_OFFLINE guard")

    return {
        "schema": "codebase_tooling_mcp.build_download_cache_check.v1",
        "ok": not problems,
        "dockerfile": str(path),
        "cache_ids": cache_ids,
        "required_cache_ids": sorted(REQUIRED_CACHE_IDS),
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, default=DEFAULT_DOCKERFILE)
    parser.add_argument("--compact", action="store_true", help="Emit one-line JSON")
    args = parser.parse_args(argv)

    result = audit_dockerfile(args.dockerfile)
    if args.compact:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
