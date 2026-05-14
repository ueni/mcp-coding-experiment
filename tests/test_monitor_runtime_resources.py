# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.monitor_runtime_resources import (
    bytes_to_mib,
    collect_runtime_samples,
    docker_run_args,
    parse_docker_bytes,
    query_vram_usage_bytes,
)


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


def test_query_vram_usage_reports_unavailable_without_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.monitor_runtime_resources.shutil.which", lambda _name: None)

    value, status = query_vram_usage_bytes()

    assert value is None
    assert status == "unavailable: nvidia-smi not found"


def test_collect_runtime_samples_one_shot_includes_peaks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.monitor_runtime_resources.container_memory_usage_bytes",
        lambda _container: 128 * 1024**2,
    )
    monkeypatch.setattr(
        "scripts.monitor_runtime_resources.query_vram_usage_bytes",
        lambda: (512 * 1024**2, "available"),
    )

    out = collect_runtime_samples(
        "monitor-test",
        continuous=False,
        sample_interval_seconds=0.01,
        monitor_timeout_seconds=None,
    )

    assert out["monitoring_mode"] == "one_shot"
    assert out["monitor_stop_reason"] == "one_shot"
    assert out["sample_count"] == 1
    assert out["peak_memory_mib"] == 128
    assert out["peak_vram_mib"] == 512


def test_collect_runtime_samples_continuous_until_timeout_tracks_peak_ram_and_vram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_samples = iter([100 * 1024**2, 150 * 1024**2, 125 * 1024**2])
    vram_samples = iter([
        (200 * 1024**2, "available"),
        (300 * 1024**2, "available"),
        (250 * 1024**2, "available"),
    ])
    monotonic_values = iter([0, 0, 1, 2])

    monkeypatch.setattr(
        "scripts.monitor_runtime_resources.container_memory_usage_bytes",
        lambda _container: next(memory_samples),
    )
    monkeypatch.setattr(
        "scripts.monitor_runtime_resources.query_vram_usage_bytes",
        lambda: next(vram_samples),
    )
    monkeypatch.setattr("scripts.monitor_runtime_resources.container_is_running", lambda _container: True)
    monkeypatch.setattr("scripts.monitor_runtime_resources.time.monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr("scripts.monitor_runtime_resources.time.sleep", lambda _seconds: None)

    out = collect_runtime_samples(
        "monitor-test",
        continuous=True,
        sample_interval_seconds=0.01,
        monitor_timeout_seconds=2,
    )

    assert out["monitoring_mode"] == "continuous"
    assert out["monitor_stop_reason"] == "timeout"
    assert out["sample_count"] == 3
    assert out["startup_memory_mib"] == 100
    assert out["peak_memory_mib"] == 150
    assert out["peak_vram_mib"] == 300


def test_dockerfile_copies_runtime_source_package_helpers() -> None:
    dockerfile = Path("source/Dockerfile").read_text()

    assert "COPY --chown=app:app server.py ./" in dockerfile
    assert "tool_output_schemas.py version_metadata.py ./source/" in dockerfile
