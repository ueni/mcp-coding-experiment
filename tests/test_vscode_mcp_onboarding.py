# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HEALTHCHECK_SCRIPT = REPO_ROOT / "scripts" / "vscode_mcp_healthcheck.py"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "devcontainer_smoke_test.py"
SERVER_SCRIPT = REPO_ROOT / "source" / "server.py"


def _load_server_module():
    spec = importlib.util.spec_from_file_location("dev_server_for_prompts", SERVER_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_healthcheck_module():
    spec = importlib.util.spec_from_file_location("vscode_mcp_healthcheck", HEALTHCHECK_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module



def test_vscode_discovers_curated_mcp_workflow_prompts():
    server = _load_server_module()

    prompts = asyncio.run(server.mcp.list_prompts())
    prompt_by_name = {prompt.name: prompt for prompt in prompts}

    expected = {
        "review_changed_files",
        "release_readiness_check",
        "security_triage",
        "devcontainer_health_check",
        "snapshot_before_refactor",
    }
    assert expected.issubset(prompt_by_name)
    assert len(expected) == 5

    for name in expected:
        prompt = prompt_by_name[name]
        assert prompt.description
        assert prompt.title


def test_vscode_workflow_prompt_content_routes_through_safe_existing_gates():
    server = _load_server_module()

    result = asyncio.run(
        server.mcp.get_prompt(
            "release_readiness_check",
            {"base_ref": "origin/main", "head_ref": "HEAD", "summary_mode": "quick"},
        )
    )
    text = result.messages[0].content.text

    assert "task_router" in text
    assert "release_readiness" in text
    assert "required_tool_chain" in text
    assert "Do not bypass failing gates" in text
    assert "Do not mutate files" in text


def test_vscode_devcontainer_prompt_redacts_auth_secret_guidance():
    server = _load_server_module()

    result = asyncio.run(server.mcp.get_prompt("devcontainer_health_check"))
    text = result.messages[0].content.text

    assert "VS Code/Copilot" in text
    assert "Never echo bearer token values" in text
    assert "/healthz" in text
    assert "/mcp" in text


def test_snapshot_prompt_uses_public_workspace_transaction_snapshot_flow():
    server = _load_server_module()

    result = asyncio.run(
        server.mcp.get_prompt("snapshot_before_refactor", {"refactor_goal": "extract parser"})
    )
    text = result.messages[0].content.text

    assert "workspace_transaction(mode='snapshot')" in text
    assert "workspace_transaction(mode='restore')" in text
    assert "mutation_router(mode='snapshot')" not in text


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
    assert HEALTHCHECK_SCRIPT.exists()


def test_docker_run_task_passes_http_bearer_token_without_literal_secret():
    tasks = json.loads((REPO_ROOT / ".vscode" / "tasks.json").read_text(encoding="utf-8"))
    task = next(task for task in tasks["tasks"] if task["label"] == "Docker: Run Container")

    args = task["args"]
    token_index = args.index("MCP_HTTP_BEARER_TOKEN")
    assert args[token_index - 1] == "-e"
    assert "MCP_HTTP_BEARER_TOKEN=" not in args


def test_vscode_devcontainer_smoke_task_points_at_checked_in_script():
    tasks = json.loads((REPO_ROOT / ".vscode" / "tasks.json").read_text(encoding="utf-8"))
    task = next(task for task in tasks["tasks"] if task["label"] == "Devcontainer: CI Smoke Test")

    assert task["type"] == "process"
    assert task["command"] == "python3"
    assert "${workspaceFolder}/scripts/devcontainer_smoke_test.py" in task["args"]
    assert task["options"]["env"]["OLLAMA_ALLOW_PULL"] == "false"
    assert SMOKE_SCRIPT.exists()


def test_devcontainer_smoke_script_uses_safe_model_prompt_defaults():
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "OLLAMA_ALLOW_PULL" in script
    assert "MCP_SMOKE_REQUIRE_MODEL_PROMPT" in script
    assert "MODEL_PROMPT_SKIP: no local Ollama models" in script
    assert "num_predict" in script
    assert "OLLAMA_ALLOW_PULL" in script
    assert "false" in script


def test_healthcheck_requires_unauthenticated_mcp_auth_rejection():
    healthcheck = _load_healthcheck_module()

    assert healthcheck._is_expected_unauth_mcp_rejection(401, "missing bearer token")
    assert healthcheck._is_expected_unauth_mcp_rejection(
        403,
        "HTTP auth is enabled but MCP_HTTP_BEARER_TOKEN is not configured",
    )
    assert not healthcheck._is_expected_unauth_mcp_rejection(200, "ok")
    assert not healthcheck._is_expected_unauth_mcp_rejection(404, "not found")


def test_healthcheck_authorization_state_fails_when_unauthenticated_request_succeeds(monkeypatch):
    healthcheck = _load_healthcheck_module()
    monkeypatch.setattr(healthcheck, "TOKEN", "secret-token")
    monkeypatch.setattr(healthcheck, "TOKEN_ENV", "MCP_HTTP_BEARER_TOKEN")
    monkeypatch.setattr(healthcheck, "_port_open", lambda _host, _port: True)

    def fake_request_json(url):
        if url.endswith("/healthz"):
            return (
                200,
                {
                    "ok": True,
                    "transport": "http",
                    "allow_mutations": True,
                    "server": {"http_mode": True},
                    "ollama": {"running": True, "configured_port": 2345, "configured_port_listening": True},
                },
                "",
            )
        return 200, {}, ""

    request_statuses = iter([(200, "ok"), (405, "method not allowed")])
    monkeypatch.setattr(healthcheck, "_request_json", fake_request_json)
    monkeypatch.setattr(healthcheck, "_request_status", lambda *_args, **_kwargs: next(request_statuses))

    auth_check = next(check for check in healthcheck.run_checks() if check.name == "HTTP authorization state")

    assert auth_check.ok is False
    assert "unexpected response" in auth_check.detail


def test_healthcheck_authorization_state_fails_when_token_request_has_auth_error(monkeypatch):
    healthcheck = _load_healthcheck_module()
    monkeypatch.setattr(healthcheck, "TOKEN", "secret-token")
    monkeypatch.setattr(healthcheck, "TOKEN_ENV", "MCP_HTTP_BEARER_TOKEN")
    monkeypatch.setattr(healthcheck, "_port_open", lambda _host, _port: True)

    def fake_request_json(url):
        if url.endswith("/healthz"):
            return (
                200,
                {
                    "ok": True,
                    "transport": "http",
                    "allow_mutations": True,
                    "server": {"http_mode": True},
                    "ollama": {"running": True, "configured_port": 2345, "configured_port_listening": True},
                },
                "",
            )
        return 200, {}, ""

    request_statuses = iter([(401, "missing bearer token"), (403, "invalid bearer token")])
    monkeypatch.setattr(healthcheck, "_request_json", fake_request_json)
    monkeypatch.setattr(healthcheck, "_request_status", lambda *_args, **_kwargs: next(request_statuses))

    auth_check = next(check for check in healthcheck.run_checks() if check.name == "HTTP authorization state")

    assert auth_check.ok is False
    assert "with $MCP_HTTP_BEARER_TOKEN=403" in auth_check.detail
    assert "Continue Settings > Secrets" in auth_check.remediation
    assert "${{ secrets.MCP_HTTP_BEARER_TOKEN }}" in auth_check.remediation


def test_healthcheck_authorization_state_reports_continue_secret_sources_when_token_missing(monkeypatch):
    healthcheck = _load_healthcheck_module()
    monkeypatch.setattr(healthcheck, "TOKEN", "")
    monkeypatch.setattr(healthcheck, "TOKEN_ENV", "MCP_HTTP_BEARER_TOKEN")
    monkeypatch.setattr(healthcheck, "_port_open", lambda _host, _port: True)

    def fake_request_json(url):
        if url.endswith("/healthz"):
            return (
                200,
                {
                    "ok": True,
                    "transport": "http",
                    "allow_mutations": True,
                    "server": {"http_mode": True},
                    "ollama": {"running": True, "configured_port": 2345, "configured_port_listening": True},
                },
                "",
            )
        return 200, {}, ""

    monkeypatch.setattr(healthcheck, "_request_json", fake_request_json)
    monkeypatch.setattr(healthcheck, "_request_status", lambda *_args, **_kwargs: (403, "MCP_HTTP_BEARER_TOKEN is not configured"))

    auth_check = next(check for check in healthcheck.run_checks() if check.name == "HTTP authorization state")

    assert auth_check.ok is False
    assert "no $MCP_HTTP_BEARER_TOKEN set" in auth_check.detail
    assert "Continue Settings > Secrets" in auth_check.remediation
    assert "workspace .continue/.env" in auth_check.remediation
    assert "${{ secrets.MCP_HTTP_BEARER_TOKEN }}" in auth_check.remediation


def test_healthcheck_authorization_state_passes_only_for_rejection_then_endpoint_reached(monkeypatch):
    healthcheck = _load_healthcheck_module()
    monkeypatch.setattr(healthcheck, "TOKEN", "secret-token")
    monkeypatch.setattr(healthcheck, "TOKEN_ENV", "MCP_HTTP_BEARER_TOKEN")
    monkeypatch.setattr(healthcheck, "_port_open", lambda _host, _port: True)

    def fake_request_json(url):
        if url.endswith("/healthz"):
            return (
                200,
                {
                    "ok": True,
                    "transport": "http",
                    "allow_mutations": True,
                    "server": {"http_mode": True},
                    "ollama": {"running": True, "configured_port": 2345, "configured_port_listening": True},
                },
                "",
            )
        return 200, {}, ""

    request_statuses = iter([(401, "missing bearer token"), (405, "method not allowed")])
    monkeypatch.setattr(healthcheck, "_request_json", fake_request_json)
    monkeypatch.setattr(healthcheck, "_request_status", lambda *_args, **_kwargs: next(request_statuses))

    auth_check = next(check for check in healthcheck.run_checks() if check.name == "HTTP authorization state")

    assert auth_check.ok is True
    assert "expected auth rejection" in auth_check.detail


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


def test_devcontainer_exit137_diagnostics_script_captures_required_evidence():
    script = (REPO_ROOT / "scripts" / "devcontainer_exit137_diagnostics.sh").read_text(
        encoding="utf-8"
    )

    required_snippets = [
        "docker inspect",
        "State.OOMKilled",
        "State.ExitCode",
        "/sys/fs/cgroup/memory.current",
        "/sys/fs/cgroup/memory.peak",
        "/sys/fs/cgroup/memory.events",
        "ps -eo pid,ppid,user,stat,%mem,%cpu,rss,vsz,comm,args --sort=-rss",
        "free -h",
        "swapon --show",
        "dmesg -T",
        "journalctl -k",
        "docker logs --tail",
        "docker events --since 24h",
    ]
    for snippet in required_snippets:
        assert snippet in script


def test_troubleshooting_documents_devcontainer_exit137_collection_and_remediation():
    troubleshooting = (REPO_ROOT / "docs" / "troubleshooting.md").read_text(encoding="utf-8")

    assert "VS Code Server attach fails with exit code 137" in troubleshooting
    assert "scripts/devcontainer_exit137_diagnostics.sh" in troubleshooting
    assert "State.OOMKilled" in troubleshooting
    assert "memory.current" in troubleshooting
    assert "memory.peak" in troubleshooting
    assert "memory.events" in troubleshooting
    assert "Process list sorted by RSS" in troubleshooting
    assert "32GB T14-class" in troubleshooting
    assert "python /app/server.py" in troubleshooting
