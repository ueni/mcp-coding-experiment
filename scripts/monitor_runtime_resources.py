#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT
"""Lightweight Docker image size and startup RAM monitor.

The script uses only the Docker CLI and Python standard library. It does not
install packages or access the network, so it is safe to run during offline
runtime/bootstrap validation once the image already exists locally.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Sequence

_BYTES_UNITS = {
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
    "tb": 1000**4,
    "tib": 1024**4,
}


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str


def run_command(args: Sequence[str], *, timeout: float = 30) -> CommandResult:
    completed = subprocess.run(
        list(args),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        rendered = " ".join(shlex.quote(arg) for arg in args)
        raise RuntimeError(
            f"command failed with exit {completed.returncode}: {rendered}\n"
            f"stdout: {completed.stdout.strip()}\n"
            f"stderr: {completed.stderr.strip()}"
        )
    return CommandResult(completed.stdout, completed.stderr)


def parse_docker_bytes(raw: str) -> int:
    """Parse Docker human-readable byte values such as '128MiB' or '1.2GB'."""
    value = raw.strip().replace(" ", "")
    if not value:
        raise ValueError("empty byte value")
    number = ""
    unit = ""
    for char in value:
        if char.isdigit() or char == ".":
            if unit:
                raise ValueError(f"invalid byte value: {raw!r}")
            number += char
        else:
            unit += char
    if not number:
        raise ValueError(f"missing numeric byte value: {raw!r}")
    multiplier = _BYTES_UNITS.get(unit.lower() or "b")
    if multiplier is None:
        raise ValueError(f"unsupported byte unit in {raw!r}")
    return int(float(number) * multiplier)


def bytes_to_mib(value: int) -> float:
    return round(value / (1024**2), 2)


def image_size_bytes(image: str) -> int:
    result = run_command(["docker", "image", "inspect", image, "--format", "{{.Size}}"])
    return int(result.stdout.strip())


def container_rootfs_size_bytes(container: str) -> int | None:
    result = run_command(
        ["docker", "container", "inspect", "--size", container, "--format", "{{.SizeRootFs}}"]
    )
    value = result.stdout.strip()
    if not value or value == "<no value>":
        return None
    return int(value)


def container_memory_usage_bytes(container: str) -> int:
    result = run_command(
        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
        timeout=15,
    )
    first_field = result.stdout.strip().split("/")[0].strip()
    return parse_docker_bytes(first_field)


def wait_for_health(base_url: str, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = "health endpoint was not checked"
    url = f"{base_url.rstrip('/')}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                body = response.read().decode("utf-8", errors="replace")
                payload = json.loads(body)
                if response.status == 200 and isinstance(payload, dict) and payload.get("ok") is True:
                    return payload
                last_error = f"unexpected health response {response.status}: {body[:200]}"
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(1)
    raise TimeoutError(f"timed out waiting for {url}: {last_error}")


def docker_run_args(args: argparse.Namespace) -> list[str]:
    run_args = [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        args.container_name,
        "--publish",
        f"127.0.0.1:{args.host_port}:8000",
        "--env",
        "MCP_TRANSPORT=http",
        "--env",
        "ALLOW_MUTATIONS=false",
        "--env",
        "OLLAMA_ENABLED=false",
        "--env",
        "OLLAMA_ALLOW_PULL=false",
        "--env",
        "REPO_PATH=/repo",
    ]
    for env_pair in args.env:
        run_args.extend(["--env", env_pair])
    run_args.extend([args.image])
    return run_args


def collect_metrics(args: argparse.Namespace) -> dict[str, Any]:
    started = False
    image_bytes = image_size_bytes(args.image)
    try:
        run_command(docker_run_args(args), timeout=30)
        started = True
        health = wait_for_health(f"http://127.0.0.1:{args.host_port}", timeout_seconds=args.timeout_seconds)
        memory_bytes = container_memory_usage_bytes(args.container_name)
        rootfs_bytes = container_rootfs_size_bytes(args.container_name)
        return {
            "image": args.image,
            "container_name": args.container_name,
            "image_size_bytes": image_bytes,
            "image_size_mib": bytes_to_mib(image_bytes),
            "container_rootfs_size_bytes": rootfs_bytes,
            "container_rootfs_size_mib": bytes_to_mib(rootfs_bytes) if rootfs_bytes is not None else None,
            "startup_memory_bytes": memory_bytes,
            "startup_memory_mib": bytes_to_mib(memory_bytes),
            "health_ok": health.get("ok") is True,
            "transport": health.get("transport"),
            "ollama_running": (health.get("ollama") or {}).get("running"),
            "offline_runtime_pull_allowed": False,
        }
    finally:
        if started:
            subprocess.run(
                ["docker", "rm", "-f", args.container_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure local Docker image size and health-check startup RAM usage."
    )
    parser.add_argument("--image", default=os.getenv("TEST_IMAGE", "codebase-tooling-mcp:test"))
    parser.add_argument(
        "--container-name",
        default=f"codebase-tooling-mcp-monitor-{os.getpid()}",
    )
    parser.add_argument("--host-port", type=int, default=int(os.getenv("MCP_MONITOR_HOST_PORT", "18000")))
    parser.add_argument("--timeout-seconds", type=float, default=45)
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="additional docker run environment variable; may be repeated",
    )
    parser.add_argument("--json", action="store_true", help="print JSON only")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    metrics = collect_metrics(args)
    if args.json:
        print(json.dumps(metrics, indent=2, sort_keys=True))
    else:
        print("Docker resource baseline:")
        print(f"  image: {metrics['image']}")
        print(f"  image size: {metrics['image_size_mib']} MiB ({metrics['image_size_bytes']} bytes)")
        if metrics["container_rootfs_size_mib"] is not None:
            print(
                "  container rootfs size: "
                f"{metrics['container_rootfs_size_mib']} MiB ({metrics['container_rootfs_size_bytes']} bytes)"
            )
        print(
            "  startup memory after /healthz: "
            f"{metrics['startup_memory_mib']} MiB ({metrics['startup_memory_bytes']} bytes)"
        )
        print(f"  health ok: {metrics['health_ok']}")
        print(f"  ollama running: {metrics['ollama_running']} (OLLAMA_ENABLED=false for offline baseline)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI should print a concise failure.
        print(f"resource monitoring failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
