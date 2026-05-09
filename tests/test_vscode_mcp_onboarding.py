# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_vscode_mcp_example_uses_secret_free_input_pattern():
    config = json.loads((REPO_ROOT / ".vscode" / "mcp.example.json").read_text(encoding="utf-8"))

    token_input = config["inputs"][0]
    assert token_input["type"] == "promptString"
    assert token_input["password"] is True
    auth_header = config["servers"]["codebase-tooling-mcp"]["headers"]["Authorization"]
    assert auth_header == "Bearer ${input:mcp-http-bearer-token}"

    serialized = json.dumps(config)
    assert "openssl rand" not in serialized
    assert "abc123" not in serialized
    assert "secret-token" not in serialized


def test_vscode_healthcheck_task_points_at_checked_in_script():
    tasks = json.loads((REPO_ROOT / ".vscode" / "tasks.json").read_text(encoding="utf-8"))
    task = next(task for task in tasks["tasks"] if task["label"] == "MCP: Workspace Health Check")

    assert task["type"] == "process"
    assert task["command"] == "python3"
    assert "${workspaceFolder}/scripts/vscode_mcp_healthcheck.py" in task["args"]
    assert (REPO_ROOT / "scripts" / "vscode_mcp_healthcheck.py").exists()


def test_devcontainer_passes_http_bearer_token_from_local_env():
    config = json.loads((REPO_ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8"))

    assert config["containerEnv"]["MCP_HTTP_BEARER_TOKEN"] == "${localEnv:MCP_HTTP_BEARER_TOKEN}"
    assert config["containerEnv"]["MCP_TRANSPORT"] == "http"


def test_setup_script_generates_token_aware_devcontainer():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        (repo_root / ".git").mkdir()
        result = subprocess.run(
            ["/bin/sh", str(REPO_ROOT / "setup-repository.sh"), "--disable-vulkan-gpu"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr.strip() or result.stdout.strip()
        config = json.loads((repo_root / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8"))

    assert config["containerEnv"]["MCP_HTTP_BEARER_TOKEN"] == "${localEnv:MCP_HTTP_BEARER_TOKEN}"
    assert "MCP_HTTP_BEARER_TOKEN" in result.stderr
