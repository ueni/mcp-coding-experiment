# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Offline end-to-end MCP workflow benchmark harness."""

__all__ = [
    "DEFAULT_FIXTURE_DIR",
    "REPORT_SCHEMA",
    "TASK_SCHEMA",
    "BenchmarkError",
    "load_task_fixtures",
    "run_benchmark_suite",
]


def __getattr__(name):
    if name in __all__:
        from . import runner

        return getattr(runner, name)
    raise AttributeError(name)
