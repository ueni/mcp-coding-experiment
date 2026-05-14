# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"

SECRET_LITERAL_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[opsu]_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"Authorization:\s*Bearer\s+(?!\$|<|\{|\.\.\.)[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"MCP_HTTP_BEARER_TOKEN\s*=\s*['\"]?(?!\$|<|\{|\.\.\.)[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
]


def test_agents_entrypoint_exists_and_points_to_canonical_docs():
    assert AGENTS.is_file(), "AGENTS.md is the required concise coding-agent entrypoint"
    text = AGENTS.read_text(encoding="utf-8")

    assert len(text.splitlines()) <= 150
    for required in [
        "task_router",
        "quality_router",
        "release_readiness",
        "workspace_transaction",
        "README.md",
        "docs/index.md",
        ".codebase-tooling-mcp/",
        "Closes #<issue>",
    ]:
        assert required in text


def test_agents_entrypoint_does_not_contain_obvious_secret_literals():
    text = AGENTS.read_text(encoding="utf-8")
    for pattern in SECRET_LITERAL_PATTERNS:
        assert pattern.search(text) is None, pattern.pattern
