#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT
"""CI smoke test for the checked-in VS Code devcontainer path.

The script consumes .devcontainer/devcontainer.json and .vscode/tasks.json,
starts the already-built devcontainer image, runs the VS Code MCP workspace
health check inside the container, and exercises a bounded Ollama prompt when a
local model is available. Missing model assets are reported as an explicit skip
unless --require-model-prompt is used.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVCONTAINER_PATH = REPO_ROOT / ".devcontainer" / "devcontainer.json"
TASKS_PATH = REPO_ROOT / ".vscode" / "tasks.json"
HEALTHCHECK_TASK_LABEL = "MCP: Workspace Health Check"
SMOKE_TASK_LABEL = "Devcontainer: CI Smoke Test"
DEFAULT_IMAGE = "codebase-tooling-mcp:test"


class SmokeFailure(RuntimeError):
    """A devcontainer smoke-test assertion failed."""


def _redact_command(command: list[str]) -> str:
    redacted: list[str] = []
    sensitive_markers = ("TOKEN=", "PASSWORD=", "SECRET=", "KEY=")
    previous = ""
    for item in command:
        if previous == "-c" and "\n" in item:
            redacted.append("<inline-python>")
        elif any(marker in item.upper() for marker in sensitive_markers):
            if "=" in item:
                redacted.append(item.split("=", 1)[0] + "=<redacted>")
            else:
                redacted.append("<redacted>")
        else:
            redacted.append(item)
        previous = item
    return " ".join(redacted)


def _run(command: list[str], *, check: bool = True, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    print("+ " + _redact_command(command))
    return subprocess.run(command, check=check, text=True, **kwargs)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SmokeFailure(f"required file is missing: {path.relative_to(REPO_ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{path.relative_to(REPO_ROOT)} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SmokeFailure(f"{path.relative_to(REPO_ROOT)} must contain a JSON object")
    return data


def _devcontainer_env(config: dict[str, Any], token: str, args: argparse.Namespace) -> dict[str, str]:
    raw_env = config.get("containerEnv", {})
    if not isinstance(raw_env, dict):
        raise SmokeFailure(".devcontainer/devcontainer.json containerEnv must be an object")

    env: dict[str, str] = {}
    for key, value in raw_env.items():
        if not isinstance(value, str):
            continue
        if value == "${localEnv:MCP_HTTP_BEARER_TOKEN}":
            env[key] = token
        elif value.startswith("${localEnv:"):
            # Do not import arbitrary host env into CI output. Use only explicit,
            # documented smoke-test controls below.
            continue
        else:
            env[key] = value

    env.update(
        {
            "MCP_HTTP_BEARER_TOKEN": token,
            "MCP_TRANSPORT": "http",
            "ALLOW_MUTATIONS": "true",
            "MCP_APPLY_REPO_DEFAULTS": "true",
            "MCP_HEALTHCHECK_TOKEN_ENV": "MCP_HTTP_BEARER_TOKEN",
            "MCP_HEALTHCHECK_EXPECT_ALLOW_MUTATIONS": "true",
            "MCP_HEALTHCHECK_TIMEOUT_SECONDS": str(args.healthcheck_timeout_seconds),
            "OLLAMA_ALLOW_PULL": "true" if args.allow_ollama_pull else "false",
            "OLLAMA_STARTUP_TIMEOUT": str(args.ollama_startup_timeout_seconds),
            "LOCAL_INFER_ENDPOINT": "http://127.0.0.1:2345/api/generate",
        }
    )
    if args.model_name:
        env["MCP_SMOKE_MODEL_NAME"] = args.model_name
    return env


def _validate_devcontainer_config(config: dict[str, Any]) -> None:
    build = config.get("build")
    if not isinstance(build, dict):
        raise SmokeFailure("devcontainer build configuration is missing")
    if build.get("dockerfile") != "../source/Dockerfile":
        raise SmokeFailure("devcontainer must build ../source/Dockerfile")
    if build.get("context") != "../source":
        raise SmokeFailure("devcontainer must build with ../source context")

    ports = config.get("forwardPorts")
    if ports != [8000, 2345]:
        raise SmokeFailure("devcontainer must forward MCP port 8000 and Ollama port 2345")

    env = config.get("containerEnv", {})
    for key, expected in {
        "MCP_TRANSPORT": "http",
        "ALLOW_MUTATIONS": "true",
        "OLLAMA_HOST": "0.0.0.0:2345",
        "OLLAMA_FALLBACK_HOST": "0.0.0.0:2345",
        "LOCAL_INFER_ENDPOINT": "http://127.0.0.1:2345/api/generate",
    }.items():
        if env.get(key) != expected:
            raise SmokeFailure(f"devcontainer containerEnv.{key} must be {expected!r}")
    if env.get("MCP_HTTP_BEARER_TOKEN") != "${localEnv:MCP_HTTP_BEARER_TOKEN}":
        raise SmokeFailure("devcontainer must pass MCP_HTTP_BEARER_TOKEN from localEnv, not a committed value")


def _one_task(task_items: list[Any], label: str) -> dict[str, Any]:
    matches = [
        task
        for task in task_items
        if isinstance(task, dict) and task.get("label") == label
    ]
    if len(matches) != 1:
        raise SmokeFailure(f"expected exactly one VS Code task named {label!r}")
    return matches[0]


def _validate_python_task(label: str, task: dict[str, Any], script_path: str) -> None:
    if task.get("type") != "process" or task.get("command") != "python3":
        raise SmokeFailure(f"{label!r} must be a python3 process task")
    args = task.get("args")
    if not isinstance(args, list) or script_path not in args:
        raise SmokeFailure(f"{label!r} must execute {script_path}")


def _validate_vscode_tasks(tasks: dict[str, Any]) -> None:
    task_items = tasks.get("tasks")
    if not isinstance(task_items, list):
        raise SmokeFailure(".vscode/tasks.json must contain a tasks array")

    _validate_python_task(
        HEALTHCHECK_TASK_LABEL,
        _one_task(task_items, HEALTHCHECK_TASK_LABEL),
        "${workspaceFolder}/scripts/vscode_mcp_healthcheck.py",
    )

    smoke_task = _one_task(task_items, SMOKE_TASK_LABEL)
    _validate_python_task(
        SMOKE_TASK_LABEL,
        smoke_task,
        "${workspaceFolder}/scripts/devcontainer_smoke_test.py",
    )
    smoke_env = smoke_task.get("options", {}).get("env", {})
    if not isinstance(smoke_env, dict):
        raise SmokeFailure(f"{SMOKE_TASK_LABEL!r} must define smoke-test env controls")
    for key in {
        "TEST_IMAGE",
        "MCP_SMOKE_REQUIRE_MODEL_PROMPT",
        "MCP_SMOKE_HOST_PORT_MODE",
        "OLLAMA_ALLOW_PULL",
        "MCP_SMOKE_SERVER_STARTUP_TIMEOUT_SECONDS",
        "MCP_SMOKE_MODEL_TIMEOUT_SECONDS",
    }:
        if key not in smoke_env:
            raise SmokeFailure(f"{SMOKE_TASK_LABEL!r} must define {key}")


_WAIT_HEALTH_PY = r"""
import json, sys, time, urllib.request
url = 'http://127.0.0.1:8000/healthz'
deadline = time.time() + float(sys.argv[1])
last = ''
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            body = response.read().decode('utf-8', errors='replace')
            payload = json.loads(body)
            if response.status == 200 and payload.get('ok') is True:
                print(body)
                raise SystemExit(0)
            last = body
    except Exception as exc:
        last = str(exc)
    time.sleep(2)
print('server did not become healthy: ' + last, file=sys.stderr)
raise SystemExit(1)
"""

_MODEL_PROMPT_PY = r"""
import json, os, sys, urllib.error, urllib.request
base = os.getenv('MCP_HEALTHCHECK_OLLAMA_URL', 'http://127.0.0.1:2345').rstrip('/')
require = os.getenv('MCP_SMOKE_REQUIRE_MODEL_PROMPT', 'false').lower() in {'1','true','yes','on'}
model = os.getenv('MCP_SMOKE_MODEL_NAME', '').strip()
timeout = float(os.getenv('MCP_SMOKE_MODEL_TIMEOUT_SECONDS', '30'))
try:
    with urllib.request.urlopen(base + '/api/tags', timeout=5) as response:
        tags = json.loads(response.read().decode('utf-8'))
except Exception as exc:
    print(f'MODEL_PROMPT_SKIP: Ollama tags endpoint unavailable: {exc}')
    raise SystemExit(1 if require else 0)
models = [item.get('name') for item in tags.get('models', []) if item.get('name')]
if model and model not in models:
    print(f'MODEL_PROMPT_SKIP: requested model {model!r} is not installed; installed={models!r}')
    raise SystemExit(1 if require else 0)
if not model:
    if not models:
        print('MODEL_PROMPT_SKIP: no local Ollama models are installed in this image/container; build with preload_ollama_models or set OLLAMA_ALLOW_PULL=true only for an explicit local run')
        raise SystemExit(1 if require else 0)
    preferred_model = os.getenv('CODING_AGENT_MODEL', '').strip()
    model = preferred_model if preferred_model in models else models[0]
payload = {
    'model': model,
    'messages': [{'role': 'user', 'content': 'Call the repo_status tool now with summary set to status. Do not answer in normal text.'}],
    'tools': [{
        'type': 'function',
        'function': {
            'name': 'repo_status',
            'description': 'Return a short repository status summary.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'summary': {'type': 'string', 'description': 'Short status request.'},
                },
                'required': ['summary'],
            },
        },
    }],
    'stream': True,
    'options': {'num_predict': 64, 'temperature': 0},
}
request = urllib.request.Request(
    base + '/api/chat',
    data=json.dumps(payload).encode('utf-8'),
    headers={'Content-Type': 'application/json'},
)
try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw_body = response.read().decode('utf-8')
        events = (
            [json.loads(line) for line in raw_body.splitlines() if line.strip()]
            if '\n' in raw_body
            else ([json.loads(raw_body)] if raw_body.strip() else [])
        )
except urllib.error.HTTPError as exc:
    detail = exc.read().decode('utf-8', errors='replace')[:500]
    print(f'MODEL_AGENT_SKIP: Ollama native /api/chat tool-call failed for {model!r}: HTTP {exc.code}: {detail}')
    raise SystemExit(1 if require else 0)
except Exception as exc:
    print(f'MODEL_AGENT_SKIP: Ollama native /api/chat tool-call failed for {model!r}: {exc}')
    raise SystemExit(1 if require else 0)
if not events:
    print(f'MODEL_AGENT_FAIL: Ollama native /api/chat returned no events for {model!r}')
    raise SystemExit(1)
content = ''.join(
    str(event.get('message', {}).get('content', ''))
    for event in events
    if isinstance(event, dict) and isinstance(event.get('message'), dict)
).strip()
tool_calls = []
for event in events:
    message = event.get('message') if isinstance(event, dict) else None
    event_tool_calls = message.get('tool_calls') if isinstance(message, dict) else None
    if isinstance(event_tool_calls, list):
        tool_calls.extend(event_tool_calls)
tool_names = [
    call.get('function', {}).get('name')
    for call in tool_calls
    if isinstance(call, dict) and isinstance(call.get('function'), dict)
]
if 'repo_status' not in tool_names:
    print(f'MODEL_AGENT_FAIL: Ollama native /api/chat did not return repo_status tool call for {model!r}; content_chars={len(content)} tool_calls={tool_names!r}')
    raise SystemExit(1)
print(f'MODEL_AGENT_OK: model={model!r} content_chars={len(content)} tool_calls={tool_names!r}')
"""


def _smoke_run_args(raw_run_args: list[Any], host_port_mode: str) -> list[str]:
    """Return docker run args for smoke runs without requiring fixed host ports."""
    run_args: list[str] = []
    iterator = iter(raw_run_args)
    for run_arg in iterator:
        if run_arg in {"-p", "--publish"}:
            try:
                publish = next(iterator)
            except StopIteration as exc:
                raise SmokeFailure(
                    "devcontainer runArgs contains a publish flag without a value"
                ) from exc
            if not isinstance(publish, str):
                raise SmokeFailure("devcontainer runArgs publish value must be a string")
            if host_port_mode == "none":
                continue
            if host_port_mode == "ephemeral" and publish in {
                "127.0.0.1:8000:8000",
                "127.0.0.1:2345:2345",
            }:
                host_ip, _host_port, container_port = publish.split(":", 2)
                publish = f"{host_ip}::{container_port}"
            run_args.extend([str(run_arg), publish])
            continue
        if run_arg == "--device=/dev/dri" and not Path("/dev/dri").exists():
            print(
                "SMOKE_NOTICE: /dev/dri is unavailable on this runner; "
                "skipping devcontainer GPU device passthrough"
            )
            continue
        run_args.append(str(run_arg))
    return run_args


def _container_logs(name: str) -> None:
    try:
        _run(["docker", "logs", "--tail", "200", name], check=False)
    except Exception:  # noqa: BLE001 - best-effort diagnostics only.
        pass


def _start_container(args: argparse.Namespace, config: dict[str, Any]) -> str:
    token = secrets.token_hex(32)
    env = _devcontainer_env(config, token, args)
    container_name = args.container_name or f"codebase-tooling-mcp-smoke-{int(time.time())}-{secrets.token_hex(4)}"

    command = [
        "docker",
        "run",
        "--detach",
        "--name",
        container_name,
        "--workdir",
        str(config.get("workspaceFolder", "/repo")),
        "--volume",
        f"{REPO_ROOT}:{config.get('workspaceFolder', '/repo')}:rw",
    ]
    for key, value in sorted(env.items()):
        command.extend(["--env", f"{key}={value}"])
    raw_run_args = config.get("runArgs", [])
    if not isinstance(raw_run_args, list):
        raise SmokeFailure(".devcontainer/devcontainer.json runArgs must be a list")
    command.extend(_smoke_run_args(raw_run_args, args.host_port_mode))
    command.append(args.image)

    _run(command)
    return container_name


def _exec_container(name: str, command: list[str], *, env: dict[str, str] | None = None) -> None:
    docker_command = ["docker", "exec"]
    for key, value in sorted((env or {}).items()):
        docker_command.extend(["--env", f"{key}={value}"])
    docker_command.append(name)
    docker_command.extend(command)
    _run(docker_command)


def run_smoke(args: argparse.Namespace) -> None:
    if shutil.which("docker") is None:
        raise SmokeFailure("docker is required for the devcontainer smoke test")

    config = _load_json(DEVCONTAINER_PATH)
    tasks = _load_json(TASKS_PATH)
    _validate_devcontainer_config(config)
    _validate_vscode_tasks(tasks)

    container_name = _start_container(args, config)
    keep_container = args.keep_container
    try:
        _exec_container(container_name, ["python3", "-c", _WAIT_HEALTH_PY, str(args.server_startup_timeout_seconds)])
        _exec_container(container_name, ["python3", "--version"])
        _exec_container(container_name, ["test", "-x", "/repo/scripts/vscode_mcp_healthcheck.py"])
        _exec_container(container_name, ["python3", "/repo/scripts/vscode_mcp_healthcheck.py"])
        _exec_container(
            container_name,
            ["python3", "-c", _MODEL_PROMPT_PY],
            env={
                "MCP_SMOKE_REQUIRE_MODEL_PROMPT": "true" if args.require_model_prompt else "false",
                "MCP_SMOKE_MODEL_TIMEOUT_SECONDS": str(args.model_timeout_seconds),
                **({"MCP_SMOKE_MODEL_NAME": args.model_name} if args.model_name else {}),
            },
        )
    except Exception:
        _container_logs(container_name)
        raise
    finally:
        if not keep_container:
            _run(["docker", "rm", "-f", container_name], check=False)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=os.getenv("TEST_IMAGE", DEFAULT_IMAGE), help="pre-built devcontainer image tag")
    parser.add_argument("--container-name", default=os.getenv("MCP_SMOKE_CONTAINER_NAME", ""))
    parser.add_argument("--keep-container", action="store_true", help="leave the smoke-test container running for debugging")
    parser.add_argument("--allow-ollama-pull", action="store_true", default=os.getenv("OLLAMA_ALLOW_PULL", "false").lower() in {"1", "true", "yes", "on"}, help="allow runtime ollama pull during explicit local runs")
    parser.add_argument("--require-model-prompt", action="store_true", default=os.getenv("MCP_SMOKE_REQUIRE_MODEL_PROMPT", "false").lower() in {"1", "true", "yes", "on"}, help="fail instead of skip when no local model prompt can run")
    parser.add_argument("--model-name", default=os.getenv("MCP_SMOKE_MODEL_NAME", ""), help="specific installed Ollama model to prompt")
    parser.add_argument("--server-startup-timeout-seconds", type=int, default=int(os.getenv("MCP_SMOKE_SERVER_STARTUP_TIMEOUT_SECONDS", "90")))
    parser.add_argument("--healthcheck-timeout-seconds", type=float, default=float(os.getenv("MCP_HEALTHCHECK_TIMEOUT_SECONDS", "3")))
    parser.add_argument("--ollama-startup-timeout-seconds", type=int, default=int(os.getenv("OLLAMA_STARTUP_TIMEOUT", "30")))
    parser.add_argument("--model-timeout-seconds", type=float, default=float(os.getenv("MCP_SMOKE_MODEL_TIMEOUT_SECONDS", "30")))
    parser.add_argument(
        "--host-port-mode",
        choices=("ephemeral", "fixed", "none"),
        default=os.getenv("MCP_SMOKE_HOST_PORT_MODE", "ephemeral"),
        help=(
            "how to handle devcontainer publish runArgs during smoke runs; "
            "ephemeral keeps container ports exposed on random localhost host ports to avoid CI collisions"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        run_smoke(args)
    except (SmokeFailure, subprocess.CalledProcessError) as exc:
        print(f"SMOKE_FAIL: {exc}", file=sys.stderr)
        return 1
    print("SMOKE_OK: devcontainer, VS Code MCP health check, and model prompt path completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
