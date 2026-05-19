# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

"""Offline-safe end-to-end MCP workflow benchmark runner.

The checked-in suite is intentionally deterministic: fixtures create disposable
mini repositories, the direct baseline executes declared file/search/test actions,
and the report stores only aggregate metrics plus bounded status evidence. Raw
agent transcripts, command output, host paths, and secrets are not persisted.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

TASK_SCHEMA = "mcp_e2e_workflow_benchmark_task.v1"
REPORT_SCHEMA = "mcp_e2e_workflow_benchmark_report.v1"
DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parent / "tasks"
DEFAULT_REPORT_STEM = "E2E_MCP_WORKFLOW_BENCHMARKS"
_ARTIFACT_SCOPE = "artifact"
_REPO_SCOPE = "repo"
_SAFE_COMMANDS = {"python", "python3"}
_IGNORED_TREE_PARTS = {".git", "__pycache__", ".pytest_cache"}


class BenchmarkError(ValueError):
    """Raised for invalid fixtures or unsafe benchmark declarations."""


def run_benchmark_suite(
    fixture_dir: str | Path = DEFAULT_FIXTURE_DIR,
    *,
    task_ids: Sequence[str] | None = None,
    runner: str = "direct",
    agent_command: Sequence[str] | None = None,
    report_dir: str | Path | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run the benchmark fixture pack and return a redacted summary report."""

    root = Path(repo_root).resolve() if repo_root is not None else repository_root()
    fixtures = load_task_fixtures(fixture_dir, repo_root=root)
    selected_ids = set(task_ids or [])
    if selected_ids:
        fixtures = [fixture for fixture in fixtures if fixture["id"] in selected_ids]
    missing_ids = selected_ids.difference(str(fixture["id"]) for fixture in fixtures)
    if missing_ids:
        raise BenchmarkError(f"unknown task ids: {', '.join(sorted(missing_ids))}")
    if not fixtures:
        raise BenchmarkError("no benchmark fixtures selected")

    started = time.perf_counter()
    results = []
    for fixture in fixtures:
        if runner == "direct":
            result = DirectBaselineRunner(fixture).run()
        elif runner in {"online-cloud-assisted", "offline-onboard-only"}:
            result = run_agent_hook(
                fixture,
                runner=runner,
                agent_command=agent_command,
            )
        else:
            raise BenchmarkError(f"unsupported runner profile: {runner}")
        results.append(result)

    elapsed = time.perf_counter() - started
    summary = _suite_summary(results, elapsed)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "ok": summary["failed"] == 0,
        "generated_by": "evaluation.e2e_mcp_workflows.runner",
        "runner": runner,
        "fixture_schema": TASK_SCHEMA,
        "fixture_count": len(results),
        "summary": summary,
        "tasks": results,
        "self_optimization_inputs": {
            "report_kind": "mcp_e2e_workflow_benchmark",
            "schema": REPORT_SCHEMA,
            "safe_for_local_retention": True,
            "raw_transcripts_persisted": False,
            "repo_external_paths_persisted": False,
            "aggregate_metrics": {
                "elapsed_seconds": summary["elapsed_seconds"],
                "tool_calls": summary["tool_calls"],
                "estimated_tokens": summary["estimated_tokens"],
                "retries": summary["retries"],
                "rework_count": summary["rework_count"],
                "safety_gates_required": summary["safety_gates_required"],
                "safety_gates_satisfied": summary["safety_gates_satisfied"],
                "snapshots_created": summary["snapshots_created"],
                "rollbacks_restored": summary["rollbacks_restored"],
                "test_gate_passed": summary["test_gate_passed"],
            },
        },
        "retention_policy": {
            "stores_raw_transcripts": False,
            "stores_command_output": False,
            "stores_secrets": False,
            "stores_host_absolute_paths": False,
            "paths_are_fixture_relative": True,
        },
    }
    if report_dir is not None:
        report["report_paths"] = write_report_files(report, report_dir, repo_root=root)
    return report


def repository_root() -> Path:
    """Return this repository root from the checked-in evaluation module path."""

    return Path(__file__).resolve().parents[2]


def load_task_fixtures(
    fixture_dir: str | Path = DEFAULT_FIXTURE_DIR,
    *,
    repo_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load and validate benchmark task JSON fixtures."""

    root = Path(repo_root).resolve() if repo_root is not None else repository_root()
    fixture_root = _resolve_path(fixture_dir, root).resolve()
    if not fixture_root.is_dir():
        raise BenchmarkError(f"fixture directory not found: {_display_path(fixture_root, root)}")
    fixtures = []
    seen_ids: set[str] = set()
    for path in sorted(fixture_root.glob("*.json")):
        fixture = _load_json(path)
        validate_fixture(fixture, path)
        fixture_id = str(fixture["id"])
        if fixture_id in seen_ids:
            raise BenchmarkError(f"duplicate fixture id {fixture_id!r}")
        seen_ids.add(fixture_id)
        fixtures.append(fixture)
    if not fixtures:
        raise BenchmarkError(f"no fixture JSON files found in {_display_path(fixture_root, root)}")
    return fixtures


def validate_fixture(fixture: Mapping[str, Any], path: Path | None = None) -> None:
    """Validate the fixture fields that are required by the harness contract."""

    label = f"{path}: " if path is not None else ""
    if fixture.get("schema") != TASK_SCHEMA:
        raise BenchmarkError(f"{label}unsupported schema {fixture.get('schema')!r}")
    for key in ("id", "prompt", "setup", "allowed", "verification", "baseline"):
        if key not in fixture:
            raise BenchmarkError(f"{label}missing required field {key!r}")
    setup = fixture["setup"]
    if not isinstance(setup, Mapping) or not isinstance(setup.get("files"), Mapping):
        raise BenchmarkError(f"{label}setup.files must be an object")
    allowed = fixture["allowed"]
    if not isinstance(allowed, Mapping) or not isinstance(allowed.get("tools"), list):
        raise BenchmarkError(f"{label}allowed.tools must be a list")
    mutations = allowed.get("mutations")
    if not isinstance(mutations, Mapping):
        raise BenchmarkError(f"{label}allowed.mutations must be an object")
    baseline = fixture["baseline"]
    if not isinstance(baseline, Mapping) or baseline.get("runner") != "direct":
        raise BenchmarkError(f"{label}baseline.runner must be 'direct'")
    actions = baseline.get("actions")
    if not isinstance(actions, list) or not actions:
        raise BenchmarkError(f"{label}baseline.actions must be a non-empty list")
    verification = fixture["verification"]
    if not isinstance(verification, Mapping):
        raise BenchmarkError(f"{label}verification must be an object")
    if not isinstance(verification.get("commands", []), list):
        raise BenchmarkError(f"{label}verification.commands must be a list")
    if not isinstance(verification.get("expected_artifacts", []), list):
        raise BenchmarkError(f"{label}verification.expected_artifacts must be a list")
    for rel_path, content in setup["files"].items():
        _validate_relative_path(str(rel_path), label=label)
        if not isinstance(content, str):
            raise BenchmarkError(f"{label}setup file {rel_path!r} content must be a string")
    for action in actions:
        if not isinstance(action, Mapping) or not isinstance(action.get("tool"), str):
            raise BenchmarkError(f"{label}each action needs a string tool field")
    for command in verification.get("commands", []):
        _validate_command_declaration(command, label=label)


class DirectBaselineRunner:
    """Execute a deterministic baseline against a disposable fixture repository."""

    def __init__(self, fixture: Mapping[str, Any]) -> None:
        self.fixture = fixture
        self.tool_events: list[dict[str, Any]] = []
        self.action_failures: list[dict[str, Any]] = []
        self.snapshots_created = 0
        self.rollbacks_restored = 0
        self.safety_gates: list[str] = []
        self.retry_count = 0
        self.write_counts: Counter[str] = Counter()
        self.test_events: list[dict[str, Any]] = []
        self.input_bytes = len(str(fixture.get("prompt", "")).encode("utf-8"))
        self.output_bytes = 0
        self._snapshot_root: Path | None = None
        self._repo_dir: Path | None = None
        self._artifact_dir: Path | None = None

    def run(self) -> dict[str, Any]:
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="mcp-e2e-bench-") as tmpdir:
            tmp_path = Path(tmpdir)
            self._repo_dir = tmp_path / "repo"
            self._artifact_dir = tmp_path / "artifacts"
            self._snapshot_root = tmp_path / "snapshots"
            self._repo_dir.mkdir(parents=True)
            self._artifact_dir.mkdir(parents=True)
            self._snapshot_root.mkdir(parents=True)
            self._write_setup_files()
            initial_hashes = _hash_tree(self._repo_dir)

            for index, action in enumerate(self.fixture["baseline"]["actions"]):
                self._execute_action(index, action)

            verification = self._run_verification_commands()
            artifact_results = self._verify_expected_artifacts()
            final_hashes = _hash_tree(self._repo_dir)
            invariant_findings = self._evaluate_invariants(initial_hashes, final_hashes)

        elapsed = time.perf_counter() - started
        metrics = self._metrics(verification, invariant_findings)
        ok = (
            not self.action_failures
            and all(command["passed"] for command in verification["commands"])
            and all(artifact["passed"] for artifact in artifact_results)
            and all(finding["status"] in {"passed", "not_applicable"} for finding in invariant_findings)
        )
        return {
            "id": self.fixture["id"],
            "title": self.fixture.get("title", self.fixture["id"]),
            "ok": ok,
            "status": "passed" if ok else "failed",
            "runner": "direct",
            "elapsed_seconds": _round_seconds(elapsed),
            "metrics": metrics,
            "safety": {
                "allowed_mutations": self.fixture["allowed"].get("mutations", {}),
                "network_allowed": bool(self.fixture["allowed"].get("network", False)),
                "gates_used": sorted(set(self.safety_gates)),
            },
            "verification": {
                "commands": verification["commands"],
                "expected_artifacts": artifact_results,
            },
            "trajectory_order_findings": [
                finding
                for finding in invariant_findings
                if finding["type"]
                in {"tool_order", "snapshot_before_mutation", "test_after_mutation"}
            ],
            "invariant_findings": invariant_findings,
            "failures": self.action_failures,
        }

    def _write_setup_files(self) -> None:
        assert self._repo_dir is not None
        files = self.fixture["setup"].get("files", {})
        for rel_path, content in files.items():
            target = _safe_join(self._repo_dir, str(rel_path))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        if self.fixture["setup"].get("git_init", False):
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=self._repo_dir,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _execute_action(self, index: int, action: Mapping[str, Any]) -> None:
        tool = str(action["tool"])
        before_input = _json_size(action)
        try:
            self._ensure_allowed_tool(tool)
            if tool == "safety_gate":
                gate = str(action.get("gate") or "unnamed")
                self.safety_gates.append(gate)
                self._record_tool(tool, index, before_input, len(gate.encode("utf-8")), "passed")
            elif tool == "read_file":
                output_size = self._read_file(action)
                self._record_tool(tool, index, before_input, output_size, "passed")
            elif tool == "search":
                output_size = self._search(action)
                self._record_tool(tool, index, before_input, output_size, "passed")
            elif tool == "write_file":
                output_size = self._write_file(action)
                self._record_tool(tool, index, before_input, output_size, "passed")
            elif tool == "write_artifact":
                output_size = self._write_artifact(action)
                self._record_tool(tool, index, before_input, output_size, "passed")
            elif tool == "snapshot":
                output_size = self._snapshot(action)
                self._record_tool(tool, index, before_input, output_size, "passed")
            elif tool == "restore_snapshot":
                output_size = self._restore_snapshot(action)
                self._record_tool(tool, index, before_input, output_size, "passed")
            elif tool == "run_command":
                command_result = self._run_declared_command(action, source="baseline")
                self._record_tool(
                    tool,
                    index,
                    before_input,
                    command_result["stdout_bytes"] + command_result["stderr_bytes"],
                    "passed" if command_result["passed"] else "failed",
                    gate=command_result.get("gate"),
                )
                if action.get("gate") == "test":
                    self.test_events.append(command_result)
                if not command_result["passed"]:
                    self.action_failures.append(
                        {
                            "action_index": index,
                            "tool": tool,
                            "id": command_result["id"],
                            "reason": "unexpected_exit_code",
                        }
                    )
            elif tool == "retry_marker":
                self.retry_count += 1
                self._record_tool(tool, index, before_input, 0, "passed")
            else:
                raise BenchmarkError(f"unsupported action tool {tool!r}")
        except Exception as exc:  # noqa: BLE001 - convert to bounded report finding.
            self.action_failures.append(
                {
                    "action_index": index,
                    "tool": tool,
                    "reason": type(exc).__name__,
                    "message": self._safe_message(str(exc)),
                }
            )
            self._record_tool(tool, index, before_input, 0, "failed")

    def _safe_message(self, value: str) -> str:
        replacements = {
            str(path): label
            for path, label in (
                (self._repo_dir, "<repo>"),
                (self._artifact_dir, "<artifacts>"),
                (self._snapshot_root, "<snapshots>"),
            )
            if path is not None
        }
        return _clip(_sanitize_text(value, replacements))

    def _read_file(self, action: Mapping[str, Any]) -> int:
        assert self._repo_dir is not None
        path = _safe_join(self._repo_dir, str(action["path"]))
        content = path.read_bytes()
        return len(content)

    def _search(self, action: Mapping[str, Any]) -> int:
        assert self._repo_dir is not None
        pattern = re.compile(str(action["pattern"]))
        globs = list(action.get("globs") or ["**/*"])
        matches = 0
        output_bytes = 0
        for path in _iter_files(self._repo_dir):
            rel_path = _relative_posix(path, self._repo_dir)
            if not any(fnmatch.fnmatch(rel_path, glob) for glob in globs):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    matches += 1
                    output_bytes += len(f"{rel_path}:{line_no}:{line}\n".encode("utf-8"))
        minimum = int(action.get("min_matches", 0))
        if matches < minimum:
            raise BenchmarkError(f"search found {matches} matches, expected at least {minimum}")
        return output_bytes

    def _write_file(self, action: Mapping[str, Any]) -> int:
        assert self._repo_dir is not None
        rel_path = str(action["path"])
        self._ensure_repo_mutation_allowed(rel_path)
        target = _safe_join(self._repo_dir, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(action.get("content", ""))
        target.write_text(content, encoding="utf-8")
        normalized = _normalize_relpath(rel_path)
        self.write_counts[normalized] += 1
        return len(content.encode("utf-8"))

    def _write_artifact(self, action: Mapping[str, Any]) -> int:
        assert self._artifact_dir is not None
        if not bool(self.fixture["allowed"].get("mutations", {}).get("artifact", True)):
            raise BenchmarkError("artifact writes are disabled for this fixture")
        target = _safe_join(self._artifact_dir, str(action["path"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(action.get("content", ""))
        target.write_text(content, encoding="utf-8")
        return len(content.encode("utf-8"))

    def _snapshot(self, action: Mapping[str, Any]) -> int:
        assert self._repo_dir is not None and self._snapshot_root is not None
        name = _snapshot_name(action)
        target = self._snapshot_root / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self._repo_dir, target, ignore=_copy_ignore)
        self.snapshots_created += 1
        return _tree_size(target)

    def _restore_snapshot(self, action: Mapping[str, Any]) -> int:
        assert self._repo_dir is not None and self._snapshot_root is not None
        name = _snapshot_name(action)
        source = self._snapshot_root / name
        if not source.is_dir():
            raise BenchmarkError(f"snapshot {name!r} does not exist")
        shutil.rmtree(self._repo_dir)
        shutil.copytree(source, self._repo_dir, ignore=_copy_ignore)
        self.rollbacks_restored += 1
        return _tree_size(self._repo_dir)

    def _run_verification_commands(self) -> dict[str, Any]:
        commands = []
        for command in self.fixture["verification"].get("commands", []):
            result = self._run_declared_command(command, source="verification")
            self.output_bytes += result["stdout_bytes"] + result["stderr_bytes"]
            commands.append(result)
            if command.get("gate") == "test":
                self.test_events.append(result)
        return {"commands": commands}

    def _verify_expected_artifacts(self) -> list[dict[str, Any]]:
        assert self._artifact_dir is not None and self._repo_dir is not None
        results = []
        for expected in self.fixture["verification"].get("expected_artifacts", []):
            scope = str(expected.get("scope", _ARTIFACT_SCOPE))
            rel_path = str(expected["path"])
            base = self._artifact_dir if scope == _ARTIFACT_SCOPE else self._repo_dir
            target = _safe_join(base, rel_path)
            text = target.read_text(encoding="utf-8") if target.is_file() else ""
            checks = []
            for needle in expected.get("must_contain", []):
                checks.append(
                    {
                        "type": "must_contain",
                        "value": str(needle),
                        "passed": str(needle) in text,
                    }
                )
            for needle in expected.get("must_not_contain", []):
                checks.append(
                    {
                        "type": "must_not_contain",
                        "value": str(needle),
                        "passed": str(needle) not in text,
                    }
                )
            passed = target.is_file() and all(check["passed"] for check in checks)
            results.append(
                {
                    "scope": scope,
                    "path": _normalize_relpath(rel_path),
                    "exists": target.is_file(),
                    "passed": passed,
                    "checks": checks,
                }
            )
        return results

    def _run_declared_command(
        self,
        command: Mapping[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        assert self._repo_dir is not None
        _validate_command_declaration(command)
        argv = list(command["argv"])
        original_binary = str(argv[0])
        if original_binary in {"python", "python3"}:
            argv[0] = sys.executable
        timeout = float(command.get("timeout_seconds", 10))
        started = time.perf_counter()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(self._repo_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        proc = subprocess.run(
            argv,
            cwd=self._repo_dir,
            check=False,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        elapsed = time.perf_counter() - started
        expected_exit_code = int(command.get("expect_exit_code", 0))
        stdout_bytes = len(proc.stdout or b"")
        stderr_bytes = len(proc.stderr or b"")
        passed = proc.returncode == expected_exit_code
        return {
            "id": str(command.get("id") or source),
            "source": source,
            "gate": str(command.get("gate") or "verification"),
            "passed": passed,
            "exit_code": proc.returncode,
            "expected_exit_code": expected_exit_code,
            "elapsed_seconds": _round_seconds(elapsed),
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
        }

    def _ensure_allowed_tool(self, tool: str) -> None:
        allowed_tools = set(str(item) for item in self.fixture["allowed"].get("tools", []))
        if tool not in allowed_tools:
            raise BenchmarkError(f"tool {tool!r} is not allowed for fixture {self.fixture['id']}")

    def _ensure_repo_mutation_allowed(self, rel_path: str) -> None:
        mutations = self.fixture["allowed"].get("mutations", {})
        if not bool(mutations.get("repo", False)):
            raise BenchmarkError("repo mutations are disabled for this fixture")
        allowed_paths = [str(pattern) for pattern in mutations.get("paths", ["**/*"])]
        normalized = _normalize_relpath(rel_path)
        if not any(fnmatch.fnmatch(normalized, pattern) for pattern in allowed_paths):
            raise BenchmarkError(f"repo mutation path {normalized!r} is not allowed")

    def _record_tool(
        self,
        tool: str,
        index: int,
        input_bytes: int,
        output_bytes: int,
        status: str,
        *,
        gate: str | None = None,
    ) -> None:
        self.input_bytes += input_bytes
        self.output_bytes += output_bytes
        event: dict[str, Any] = {
            "index": index,
            "tool": tool,
            "status": status,
        }
        if gate:
            event["gate"] = gate
        self.tool_events.append(event)

    def _evaluate_invariants(
        self,
        initial_hashes: Mapping[str, str],
        final_hashes: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        findings = []
        invariants = self.fixture.get("invariants", {})
        declared = list(invariants.get("safety", [])) + list(invariants.get("trajectory", []))
        for invariant in declared:
            inv_type = str(invariant.get("type"))
            inv_id = str(invariant.get("id") or inv_type)
            status = "passed"
            detail = "satisfied"
            if inv_type == "required_gate":
                gate = str(invariant.get("gate"))
                if gate not in self.safety_gates:
                    status = "failed"
                    detail = f"missing gate {gate}"
            elif inv_type == "required_tool":
                tool = str(invariant.get("tool"))
                if not self._has_tool(tool):
                    status = "failed"
                    detail = f"missing tool {tool}"
            elif inv_type == "forbidden_tool":
                tool = str(invariant.get("tool"))
                if self._has_tool(tool):
                    status = "failed"
                    detail = f"forbidden tool {tool} was used"
            elif inv_type == "tool_order":
                status, detail = self._tool_order_status(invariant)
            elif inv_type == "snapshot_before_mutation":
                status, detail = self._snapshot_before_mutation_status()
            elif inv_type == "test_after_mutation":
                status, detail = self._test_after_mutation_status()
            elif inv_type == "forbidden_path_mutation":
                status, detail = self._forbidden_path_mutation_status(invariant)
            elif inv_type == "no_repo_mutation":
                if dict(initial_hashes) != dict(final_hashes):
                    status = "failed"
                    detail = "repository files changed"
            elif inv_type == "no_network":
                if bool(self.fixture["allowed"].get("network", False)):
                    status = "failed"
                    detail = "fixture allows network"
            else:
                status = "failed"
                detail = f"unknown invariant type {inv_type}"
            findings.append(
                {
                    "id": inv_id,
                    "type": inv_type,
                    "status": status,
                    "detail": detail,
                }
            )
        return findings

    def _metrics(
        self,
        verification: Mapping[str, Any],
        invariant_findings: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        by_tool = Counter(event["tool"] for event in self.tool_events)
        total_bytes = self.input_bytes + self.output_bytes
        rewritten_paths = sorted(
            path for path, count in self.write_counts.items() if count > 1
        )
        required_gate_findings = [
            finding
            for finding in invariant_findings
            if finding.get("type") == "required_gate"
        ]
        satisfied_gate_findings = [
            finding
            for finding in required_gate_findings
            if finding.get("status") == "passed"
        ]
        test_commands = [command for command in self.test_events if command.get("gate") == "test"]
        if not test_commands:
            test_status = "not_available"
        elif all(command.get("passed") for command in test_commands):
            test_status = "passed"
        else:
            test_status = "failed"
        return {
            "tool_calls": {
                "total": len(self.tool_events),
                "by_tool": dict(sorted(by_tool.items())),
            },
            "approximate_volume": {
                "input_bytes": self.input_bytes,
                "output_bytes": self.output_bytes,
                "total_bytes": total_bytes,
                "estimated_tokens": max(1, round(total_bytes / 4)),
            },
            "retries_rework": {
                "retries": self.retry_count,
                "rework_count": len(rewritten_paths),
                "rewritten_paths": rewritten_paths,
            },
            "safety_gate_coverage": {
                "required": len(required_gate_findings),
                "satisfied": len(satisfied_gate_findings),
                "gates_used": sorted(set(self.safety_gates)),
            },
            "snapshot_rollback": {
                "snapshots_created": self.snapshots_created,
                "rollbacks_restored": self.rollbacks_restored,
                "used": self.snapshots_created > 0 or self.rollbacks_restored > 0,
            },
            "test_gate": {
                "status": test_status,
                "commands": len(test_commands),
                "passed": sum(1 for command in test_commands if command.get("passed")),
                "failed": sum(1 for command in test_commands if not command.get("passed")),
            },
            "trajectory_order_findings": {
                "total": sum(
                    1
                    for finding in invariant_findings
                    if finding.get("type")
                    in {"tool_order", "snapshot_before_mutation", "test_after_mutation"}
                ),
                "failed": sum(
                    1
                    for finding in invariant_findings
                    if finding.get("type")
                    in {"tool_order", "snapshot_before_mutation", "test_after_mutation"}
                    and finding.get("status") == "failed"
                ),
            },
            "verification_commands": len(verification.get("commands", [])),
        }

    def _has_tool(self, tool: str) -> bool:
        return any(event["tool"] == tool for event in self.tool_events)

    def _tool_order_status(self, invariant: Mapping[str, Any]) -> tuple[str, str]:
        target_tool = str(invariant.get("tool"))
        prerequisites = [str(item) for item in invariant.get("must_follow_any", [])]
        target_indexes = [
            event["index"] for event in self.tool_events if event["tool"] == target_tool
        ]
        if not target_indexes:
            return "not_applicable", f"tool {target_tool} was not used"
        if not prerequisites:
            return "failed", "tool_order invariant has no prerequisites"
        prerequisite_indexes = [
            event["index"]
            for event in self.tool_events
            if event["tool"] in set(prerequisites)
        ]
        if not prerequisite_indexes:
            return "failed", "no prerequisite tool was used"
        if min(target_indexes) <= min(prerequisite_indexes):
            return "failed", f"{target_tool} ran before prerequisite inspection"
        return "passed", "target tool followed prerequisite inspection"

    def _snapshot_before_mutation_status(self) -> tuple[str, str]:
        mutation_indexes = [
            event["index"] for event in self.tool_events if event["tool"] == "write_file"
        ]
        if not mutation_indexes:
            return "not_applicable", "no repo mutation occurred"
        snapshot_indexes = [
            event["index"] for event in self.tool_events if event["tool"] == "snapshot"
        ]
        if not snapshot_indexes:
            return "failed", "no snapshot was created"
        if min(snapshot_indexes) >= min(mutation_indexes):
            return "failed", "snapshot did not precede first mutation"
        return "passed", "snapshot preceded first mutation"

    def _test_after_mutation_status(self) -> tuple[str, str]:
        mutation_indexes = [
            event["index"] for event in self.tool_events if event["tool"] == "write_file"
        ]
        if not mutation_indexes:
            return "not_applicable", "no repo mutation occurred"
        test_indexes = [
            event["index"]
            for event in self.tool_events
            if event["tool"] == "run_command" and event.get("gate") == "test"
        ]
        if not test_indexes:
            return "failed", "no test command was run"
        if max(test_indexes) <= max(mutation_indexes):
            return "failed", "test command did not follow final mutation"
        return "passed", "test command followed mutation"

    def _forbidden_path_mutation_status(
        self,
        invariant: Mapping[str, Any],
    ) -> tuple[str, str]:
        patterns = [str(pattern) for pattern in invariant.get("paths", [])]
        if not patterns:
            return "failed", "no forbidden paths declared"
        touched = [
            path
            for path in self.write_counts
            if any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
        ]
        if touched:
            return "failed", f"forbidden paths changed: {', '.join(touched)}"
        return "passed", "no forbidden path mutation"


def run_agent_hook(
    fixture: Mapping[str, Any],
    *,
    runner: str,
    agent_command: Sequence[str] | None,
) -> dict[str, Any]:
    """Run a configured agent hook for non-direct profiles.

    The hook is deliberately opt-in. It receives a disposable repository path,
    artifact path, fixture path, and profile in environment variables and must
    write a sanitized ``runner_result.json`` file. This lets online/cloud and
    offline/onboard agents share the same fixture format without making network
    or model calls part of the default smoke suite.
    """

    started = time.perf_counter()
    if not agent_command:
        return {
            "id": fixture["id"],
            "title": fixture.get("title", fixture["id"]),
            "ok": False,
            "status": "hook_not_configured",
            "runner": runner,
            "elapsed_seconds": _round_seconds(time.perf_counter() - started),
            "metrics": _empty_metrics(),
            "safety": {
                "network_allowed": bool(fixture["allowed"].get("network", False)),
                "gates_used": [],
            },
            "verification": {"commands": [], "expected_artifacts": []},
            "trajectory_order_findings": [],
            "invariant_findings": [],
            "failures": [
                {
                    "reason": "agent_hook_not_configured",
                    "message": "pass --agent-command to execute this runner profile",
                }
            ],
        }
    with tempfile.TemporaryDirectory(prefix="mcp-e2e-agent-") as tmpdir:
        tmp_path = Path(tmpdir)
        repo_dir = tmp_path / "repo"
        artifact_dir = tmp_path / "artifacts"
        result_path = tmp_path / "runner_result.json"
        repo_dir.mkdir()
        artifact_dir.mkdir()
        for rel_path, content in fixture["setup"].get("files", {}).items():
            target = _safe_join(repo_dir, str(rel_path))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
        fixture_path = tmp_path / "fixture.json"
        fixture_path.write_text(json.dumps(fixture, indent=2), encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "MCP_E2E_FIXTURE_JSON": str(fixture_path),
            "MCP_E2E_WORKDIR": str(repo_dir),
            "MCP_E2E_ARTIFACT_DIR": str(artifact_dir),
            "MCP_E2E_RESULT_JSON": str(result_path),
            "MCP_AGENT_EXECUTION_MODE": runner,
        }
        proc = subprocess.run(
            list(agent_command),
            check=False,
            cwd=repo_dir,
            env=env,
            capture_output=True,
            timeout=120,
        )
        if proc.returncode != 0 or not result_path.is_file():
            return {
                "id": fixture["id"],
                "title": fixture.get("title", fixture["id"]),
                "ok": False,
                "status": "hook_failed",
                "runner": runner,
                "elapsed_seconds": _round_seconds(time.perf_counter() - started),
                "metrics": _empty_metrics(),
                "safety": {
                    "network_allowed": bool(fixture["allowed"].get("network", False)),
                    "gates_used": [],
                },
                "verification": {"commands": [], "expected_artifacts": []},
                "trajectory_order_findings": [],
                "invariant_findings": [],
                "failures": [
                    {
                        "reason": "agent_hook_failed",
                        "exit_code": proc.returncode,
                        "stdout_bytes": len(proc.stdout or b""),
                        "stderr_bytes": len(proc.stderr or b""),
                    }
                ],
            }
        result = json.loads(result_path.read_text(encoding="utf-8"))
        replacements = {
            str(repo_dir): "<repo>",
            str(artifact_dir): "<artifacts>",
            str(tmp_path): "<benchmark-workdir>",
            str(fixture_path): "<fixture-json>",
            str(result_path): "<result-json>",
        }
        safe_result = _sanitize_for_report(result, replacements)
        return _normalize_agent_result(safe_result, fixture, runner, started)


def write_report_files(
    report: Mapping[str, Any],
    report_dir: str | Path,
    *,
    repo_root: str | Path | None = None,
) -> dict[str, str]:
    """Write JSON and Markdown sibling reports and return safe display paths."""

    root = Path(repo_root).resolve() if repo_root is not None else repository_root()
    target_dir = _resolve_path(report_dir, root)
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / f"{DEFAULT_REPORT_STEM}.json"
    markdown_path = target_dir / f"{DEFAULT_REPORT_STEM}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown_summary(report), encoding="utf-8")
    return {
        "json": _display_path(json_path, root),
        "markdown": _display_path(markdown_path, root),
    }


def render_markdown_summary(report: Mapping[str, Any]) -> str:
    """Render a compact report summary with no raw command output."""

    summary = report["summary"]
    lines = [
        "# E2E MCP workflow benchmark summary",
        "",
        f"Schema: `{report['schema']}`",
        f"Runner: `{report['runner']}`",
        f"Status: {'PASS' if report['ok'] else 'FAIL'}",
        "",
        "## Aggregate metrics",
        "",
        f"- Tasks: {summary['passed']}/{summary['tasks']} passed",
        f"- Elapsed: {summary['elapsed_seconds']}s",
        f"- Tool calls: {summary['tool_calls']}",
        f"- Estimated tokens: {summary['estimated_tokens']}",
        f"- Retries: {summary['retries']}; rework count: {summary['rework_count']}",
        (
            "- Safety gates: "
            f"{summary['safety_gates_satisfied']}/{summary['safety_gates_required']} satisfied"
        ),
        (
            "- Snapshot/rollback usage: "
            f"{summary['snapshots_created']} snapshots, "
            f"{summary['rollbacks_restored']} restores"
        ),
        f"- Test gates passed: {summary['test_gate_passed']}/{summary['tasks']}",
        "",
        "## Task results",
        "",
        "| Task | Status | Tool calls | Est. tokens | Test gate | Trajectory findings |",
        "| --- | --- | ---: | ---: | --- | ---: |",
    ]
    for task in report["tasks"]:
        metrics = task["metrics"]
        lines.append(
            "| {id} | {status} | {tool_calls} | {tokens} | {test_gate} | {findings} |".format(
                id=task["id"],
                status="PASS" if task["ok"] else "FAIL",
                tool_calls=metrics["tool_calls"]["total"],
                tokens=metrics["approximate_volume"]["estimated_tokens"],
                test_gate=metrics["test_gate"]["status"],
                findings=metrics["trajectory_order_findings"]["total"],
            )
        )
    lines.extend(
        [
            "",
            "Raw transcripts, command stdout/stderr, secrets, and host absolute paths are not persisted.",
            "Use this as optimization evidence, not as the sole release gate.",
            "",
        ]
    )
    return "\n".join(lines)


def _suite_summary(results: Sequence[Mapping[str, Any]], elapsed: float) -> dict[str, Any]:
    passed = sum(1 for result in results if result.get("ok"))
    failed = len(results) - passed
    return {
        "tasks": len(results),
        "passed": passed,
        "failed": failed,
        "elapsed_seconds": _round_seconds(elapsed),
        "tool_calls": sum(result["metrics"]["tool_calls"]["total"] for result in results),
        "estimated_tokens": sum(
            result["metrics"]["approximate_volume"]["estimated_tokens"]
            for result in results
        ),
        "retries": sum(
            result["metrics"]["retries_rework"]["retries"] for result in results
        ),
        "rework_count": sum(
            result["metrics"]["retries_rework"]["rework_count"] for result in results
        ),
        "safety_gates_required": sum(
            result["metrics"]["safety_gate_coverage"]["required"] for result in results
        ),
        "safety_gates_satisfied": sum(
            result["metrics"]["safety_gate_coverage"]["satisfied"]
            for result in results
        ),
        "snapshots_created": sum(
            result["metrics"]["snapshot_rollback"]["snapshots_created"]
            for result in results
        ),
        "rollbacks_restored": sum(
            result["metrics"]["snapshot_rollback"]["rollbacks_restored"]
            for result in results
        ),
        "test_gate_passed": sum(
            1
            for result in results
            if result["metrics"]["test_gate"]["status"] == "passed"
        ),
    }


def _normalize_agent_result(
    result: Mapping[str, Any],
    fixture: Mapping[str, Any],
    runner: str,
    started: float,
) -> dict[str, Any]:
    normalized = dict(result)
    normalized.setdefault("id", fixture["id"])
    normalized.setdefault("title", fixture.get("title", fixture["id"]))
    normalized.setdefault("runner", runner)
    normalized.setdefault("elapsed_seconds", _round_seconds(time.perf_counter() - started))
    normalized.setdefault("metrics", _empty_metrics())
    normalized.setdefault("safety", {"gates_used": []})
    normalized.setdefault("verification", {"commands": [], "expected_artifacts": []})
    normalized.setdefault("trajectory_order_findings", [])
    normalized.setdefault("invariant_findings", [])
    normalized.setdefault("failures", [])
    normalized["ok"] = bool(normalized.get("ok", False))
    normalized["status"] = "passed" if normalized["ok"] else str(normalized.get("status", "failed"))
    return normalized


def _empty_metrics() -> dict[str, Any]:
    return {
        "tool_calls": {"total": 0, "by_tool": {}},
        "approximate_volume": {
            "input_bytes": 0,
            "output_bytes": 0,
            "total_bytes": 0,
            "estimated_tokens": 0,
        },
        "retries_rework": {"retries": 0, "rework_count": 0, "rewritten_paths": []},
        "safety_gate_coverage": {"required": 0, "satisfied": 0, "gates_used": []},
        "snapshot_rollback": {"snapshots_created": 0, "rollbacks_restored": 0, "used": False},
        "test_gate": {"status": "not_available", "commands": 0, "passed": 0, "failed": 0},
        "trajectory_order_findings": {"total": 0, "failed": 0},
        "verification_commands": 0,
    }


def _sanitize_for_report(
    value: Any,
    replacements: Mapping[str, str] | None = None,
) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value, replacements)
    if isinstance(value, list):
        return [_sanitize_for_report(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _sanitize_for_report(item, replacements)
            for key, item in value.items()
        }
    return value


def _sanitize_text(
    value: str,
    replacements: Mapping[str, str] | None = None,
) -> str:
    text = value
    for raw, replacement in sorted(
        (replacements or {}).items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if raw:
            text = text.replace(raw, replacement)
    text = re.sub(
        r"/tmp/mcp-e2e-(?:bench|agent)-[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.@+-]+)*",
        "<benchmark-workdir>",
        text,
    )
    text = re.sub(
        r"(?i)(bearer|token|api[_-]?key|password)=([^\s,;]+)",
        r"\1=<redacted>",
        text,
    )
    return text


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise BenchmarkError(f"{path}: expected JSON object")
    return data


def _validate_command_declaration(
    command: Mapping[str, Any],
    *,
    label: str = "",
) -> None:
    if not isinstance(command, Mapping):
        raise BenchmarkError(f"{label}command must be an object")
    argv = command.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
        raise BenchmarkError(f"{label}command.argv must be a non-empty string list")
    binary = argv[0]
    if binary not in _SAFE_COMMANDS:
        raise BenchmarkError(f"{label}unsafe command binary {binary!r}")
    if len(argv) >= 3 and argv[1] == "-m" and argv[2] in {"pip", "venv"}:
        raise BenchmarkError(f"{label}network/environment mutating python module is not allowed")


def _validate_relative_path(rel_path: str, *, label: str = "") -> None:
    path = Path(rel_path)
    if path.is_absolute() or ".." in path.parts or not rel_path or rel_path.startswith("~"):
        raise BenchmarkError(f"{label}unsafe relative path {rel_path!r}")


def _resolve_path(path: str | Path, root: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _safe_join(base: Path, rel_path: str) -> Path:
    _validate_relative_path(rel_path)
    target = (base / rel_path).resolve()
    base_resolved = base.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise BenchmarkError(f"path escapes disposable workspace: {rel_path!r}") from exc
    return target


def _normalize_relpath(rel_path: str) -> str:
    _validate_relative_path(rel_path)
    return Path(rel_path).as_posix()


def _relative_posix(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _display_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.name


def _iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _IGNORED_TREE_PARTS for part in path.relative_to(root).parts):
            continue
        yield path


def _hash_tree(root: Path) -> dict[str, str]:
    hashes = {}
    for path in _iter_files(root):
        rel_path = _relative_posix(path, root)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[rel_path] = digest
    return hashes


def _tree_size(root: Path) -> int:
    return sum(path.stat().st_size for path in _iter_files(root))


def _copy_ignore(directory: str, names: Sequence[str]) -> set[str]:
    return {name for name in names if name in _IGNORED_TREE_PARTS}


def _snapshot_name(action: Mapping[str, Any]) -> str:
    name = str(action.get("name") or "snapshot")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise BenchmarkError(f"unsafe snapshot name {name!r}")
    return name


def _json_size(value: Mapping[str, Any]) -> int:
    return len(json.dumps(value, sort_keys=True).encode("utf-8"))


def _round_seconds(value: float) -> float:
    return round(value, 4)


def _clip(value: str, limit: int = 240) -> str:
    value = value.replace("\n", " ")
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture-dir",
        default=str(DEFAULT_FIXTURE_DIR),
        help="directory containing mcp_e2e_benchmark_task.v1 JSON fixtures",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
        help="run only the named fixture id; may be supplied more than once",
    )
    parser.add_argument(
        "--runner",
        default="direct",
        choices=["direct", "online-cloud-assisted", "offline-onboard-only"],
        help="runner profile; non-direct profiles require --agent-command",
    )
    parser.add_argument(
        "--agent-command",
        nargs="+",
        help="command argv for online/offline agent hook profiles",
    )
    parser.add_argument(
        "--report-dir",
        help="optional directory for JSON/Markdown sibling report output",
    )
    parser.add_argument(
        "--fail-on-benchmark-failure",
        action="store_true",
        help="return exit code 1 when any benchmark fails",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = run_benchmark_suite(
        fixture_dir=args.fixture_dir,
        task_ids=args.task_ids,
        runner=args.runner,
        agent_command=args.agent_command,
        report_dir=args.report_dir,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.fail_on_benchmark_failure and not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
