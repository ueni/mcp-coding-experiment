# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.monitor_runtime_resources import bytes_to_mib, docker_run_args, parse_docker_bytes


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("512B", 512),
        ("1kB", 1000),
        ("1KiB", 1024),
        ("128MiB", 128 * 1024 * 1024),
        ("1.5GiB", int(1.5 * 1024**3)),
        (" 42 MB ", 42 * 1000**2),
    ],
)
def test_parse_docker_bytes(raw: str, expected: int) -> None:
    assert parse_docker_bytes(raw) == expected


@pytest.mark.parametrize("raw", ["", "MiB", "1XB", "1MiB2"])
def test_parse_docker_bytes_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_docker_bytes(raw)


def test_bytes_to_mib_rounds_for_human_baseline() -> None:
    assert bytes_to_mib(1572864) == 1.5


def test_docker_run_args_disable_runtime_pulls_and_ollama() -> None:
    class Args:
        image = "codebase-tooling-mcp:test"
        container_name = "monitor-test"
        host_port = 18000
        env = ["EXTRA=value"]

    args = docker_run_args(Args())

    assert args[:2] == ["docker", "run"]
    assert "codebase-tooling-mcp:test" == args[-1]
    assert "OLLAMA_ENABLED=false" in args
    assert "OLLAMA_ALLOW_PULL=false" in args
    assert "EXTRA=value" in args


def test_dockerfile_copies_runtime_source_package_helpers() -> None:
    dockerfile = Path("source/Dockerfile").read_text()

    assert "COPY --chown=app:app server.py ./" in dockerfile
    assert "tool_output_schemas.py version_metadata.py ./source/" in dockerfile
