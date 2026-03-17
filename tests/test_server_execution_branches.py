# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import subprocess
import sys
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class ServerExecutionCoverageTest(ServerToolsTestBase):
    def test_command_runner_success(self):
        proc = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="ok\n",
            stderr="",
        )
        with patch.object(self.server, "_validate_safe_command", return_value=None), patch.object(
            self.server.subprocess,
            "run",
            return_value=proc,
        ):
            out = self.server.command_runner(
                command=["git", "status"],
                cwd=".",
                timeout_seconds=5,
                max_output_chars=40,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["cwd"], ".")
        self.assertEqual(out["stdout"], "ok\n")

    def test_command_runner_nonzero_exit_records_failure(self):
        proc = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=2,
            stdout="",
            stderr="bad flag",
        )
        with patch.object(self.server, "_validate_safe_command", return_value=None), patch.object(
            self.server.subprocess,
            "run",
            return_value=proc,
        ):
            out = self.server.command_runner(command=["git", "status"], timeout_seconds=5)
        self.assertFalse(out["ok"])
        self.assertEqual(out["exit_code"], 2)
        self.assertIn("bad flag", out["stderr"])

    def test_command_runner_timeout_branch(self):
        timeout = subprocess.TimeoutExpired(
            cmd=["git", "status"],
            timeout=5,
            output="partial stdout",
            stderr="slow stderr",
        )
        with patch.object(self.server, "_validate_safe_command", return_value=None), patch.object(
            self.server.subprocess,
            "run",
            side_effect=timeout,
        ):
            out = self.server.command_runner(command=["git", "status"], timeout_seconds=5)
        self.assertFalse(out["ok"])
        self.assertTrue(out["timeout"])
        self.assertEqual(out["exit_code"], None)
        self.assertIn("partial stdout", out["stdout"])
        self.assertIn("slow stderr", out["stderr"])

    def test_command_runner_missing_binary_branch(self):
        with patch.object(self.server, "_validate_safe_command", return_value=None), patch.object(
            self.server.subprocess,
            "run",
            side_effect=FileNotFoundError("missing executable"),
        ):
            out = self.server.command_runner(command=["git", "status"], timeout_seconds=5)
        self.assertFalse(out["ok"])
        self.assertFalse(out["timeout"])
        self.assertEqual(out["exit_code"], None)
        self.assertIn("missing executable", out["stderr"])

    def test_command_runner_non_whitelisted_returns_manual_request(self):
        with patch.object(self.server.subprocess, "run") as run_mock:
            out = self.server.command_runner(command=["python", "script.py"], timeout_seconds=5)
        run_mock.assert_not_called()
        self.assertTrue(out["ok"])
        self.assertFalse(out["timeout"])
        self.assertTrue(out["manual_execution_required"])
        self.assertEqual(out["exit_code"], None)
        self.assertEqual(out["suggested_command"], "python script.py")
        self.assertEqual(out["approval_request"]["action"], "manual_command_execution")
        self.assertEqual(out["approval_request"]["status"], "pending")
        self.assertIn("approval requested", out["message"].lower())
        self.assertIn("command not allowed: python", out["stderr"])

        listed = self.server.human_approval_points(mode="list")
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["items"][0]["approval_id"], out["approval_request"]["approval_id"])

    def test_command_runner_env_wrapper_runs_safe_command(self):
        proc = subprocess.CompletedProcess(
            args=["env", "MODE=test", "git", "status"],
            returncode=0,
            stdout="wrapped ok\n",
            stderr="",
        )
        with patch.object(self.server.subprocess, "run", return_value=proc) as run_mock:
            out = self.server.command_runner(
                command=["env", "MODE=test", "git", "status"],
                timeout_seconds=5,
            )
        run_mock.assert_called_once()
        self.assertTrue(out["ok"])
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["stdout"], "wrapped ok\n")

    def test_command_runner_env_wrapper_preserves_manual_request_for_non_whitelisted_command(self):
        with patch.object(self.server.subprocess, "run") as run_mock:
            out = self.server.command_runner(
                command=["env", "MODE=test", "python", "script.py"],
                timeout_seconds=5,
            )
        run_mock.assert_not_called()
        self.assertTrue(out["ok"])
        self.assertTrue(out["manual_execution_required"])
        self.assertEqual(out["suggested_command"], "env MODE=test python script.py")
        self.assertIn("command not allowed: python", out["stderr"])

    def test_command_runner_runs_after_matching_manual_request_is_approved(self):
        pending = self.server.command_runner(command=["python", "script.py"], timeout_seconds=5)
        self.server.human_approval_points(
            mode="resolve",
            approval_id=pending["approval_request"]["approval_id"],
            approved=True,
        )
        proc = subprocess.CompletedProcess(
            args=["python", "script.py"],
            returncode=0,
            stdout="ran after approval\n",
            stderr="",
        )
        with patch.object(self.server.subprocess, "run", return_value=proc) as run_mock:
            out = self.server.command_runner(command=["python", "script.py"], timeout_seconds=5)
        run_mock.assert_called_once()
        self.assertTrue(out["ok"])
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["stdout"], "ran after approval\n")
        self.assertNotIn("manual_execution_required", out)

    def test_self_test_pytest_runner_repo_target(self):
        proc = subprocess.CompletedProcess(
            args=["pytest", "-q", "-x", "tests/test_sample.py"],
            returncode=0,
            stdout="1 passed\n",
            stderr="",
        )
        with patch.object(self.server.subprocess, "run", return_value=proc):
            out = self.server.self_test(
                runner="pytest",
                target="repo:tests/test_sample.py",
                verbose=False,
                fail_fast=True,
                timeout_seconds=60,
            )
        self.assertEqual(out["schema"], "self_test.v1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["runner"], "pytest")
        self.assertEqual(out["execution_root"], str(self.repo_path))
        self.assertEqual(out["resolved_target"], "tests/test_sample.py")
        self.assertEqual(out["command"][-1], "tests/test_sample.py")

    def test_self_test_timeout_and_missing_runner(self):
        timeout = subprocess.TimeoutExpired(
            cmd=["pytest", "-q", "tests"],
            timeout=5,
            output="partial",
            stderr="still running",
        )
        with patch.object(self.server.subprocess, "run", side_effect=timeout):
            timed = self.server.self_test(
                runner="pytest",
                target="repo:tests",
                verbose=False,
                timeout_seconds=5,
            )
        self.assertFalse(timed["ok"])
        self.assertTrue(timed["timeout"])
        self.assertIn("partial", timed["stdout"])

        with patch.object(self.server.subprocess, "run", side_effect=FileNotFoundError("pytest missing")):
            missing = self.server.self_test(
                runner="pytest",
                target="repo:tests",
                verbose=False,
                timeout_seconds=5,
            )
        self.assertFalse(missing["ok"])
        self.assertFalse(missing["timeout"])
        self.assertIn("pytest missing", missing["stderr"])

        with self.assertRaises(ValueError):
            self.server.self_test(runner="bad-runner")

    def test_flaky_test_detector_mixed_runs_and_history(self):
        failing = subprocess.CompletedProcess(
            args=["pytest", "-q", "tests"],
            returncode=1,
            stdout="tests/test_sample.py::test_alpha FAILED\n",
            stderr="",
        )
        passing = subprocess.CompletedProcess(
            args=["pytest", "-q", "tests"],
            returncode=0,
            stdout="1 passed\n",
            stderr="",
        )
        timed_out = subprocess.TimeoutExpired(
            cmd=["pytest", "-q", "tests"],
            timeout=5,
            output="partial",
            stderr="still running",
        )
        with patch.object(
            self.server.subprocess,
            "run",
            side_effect=[failing, passing, timed_out],
        ):
            out = self.server.flaky_test_detector(
                runner="pytest",
                target="tests",
                runs=3,
                timeout_seconds=5,
                update_history=True,
            )
        self.assertEqual(out["schema"], "flaky_test_detector.v1")
        self.assertEqual(len(out["run_results"]), 3)
        flaky_ids = {row["test"] for row in out["flaky_tests"]}
        self.assertIn("<unknown>", flaky_ids)
        self.assertIn("<timeout>", flaky_ids)
        self.assertEqual(out["consistently_failing_tests"], [])
        self.assertEqual(out["run_results"][0]["failed_tests"], ["<unknown>"])
        self.assertTrue((self.repo_path / out["history_path"]).is_file())

    def test_flaky_test_detector_missing_runner_branch(self):
        with patch.object(self.server.subprocess, "run", side_effect=FileNotFoundError("pytest missing")):
            out = self.server.flaky_test_detector(
                runner="pytest",
                target="tests",
                runs=2,
                timeout_seconds=5,
                update_history=False,
            )
        self.assertFalse(out["ok"])
        self.assertIn("pytest missing", out["error"])

    def test_repo_info_git_and_non_git_paths(self):
        repo = self.server.repo_info()
        self.assertTrue(repo["repo_exists"])
        self.assertTrue(repo["is_git_repo"])
        self.assertIn("current_branch", repo)
        self.assertIn("head", repo)

        with patch.object(self.server, "_is_git_repo", return_value=False), patch.object(
            self.server,
            "_docker_cli_status",
            return_value={"available": False, "reachable": False},
        ):
            no_git = self.server.repo_info()
        self.assertFalse(no_git["is_git_repo"])
        self.assertNotIn("current_branch", no_git)
        self.assertEqual(no_git["docker"]["available"], False)

    def test_write_move_git_log_git_restore(self):
        written = self.server.write_file(path="notes/todo.txt", content="alpha\n", overwrite=True)
        self.assertEqual(written["path"], "notes/todo.txt")
        self.assertFalse(written["existed_before"])
        with self.assertRaises(FileExistsError):
            self.server.write_file(path="notes/todo.txt", content="beta\n", overwrite=False)
        with self.assertRaises(IsADirectoryError):
            self.server.write_file(path="notes", content="x")

        moved = self.server.move_path(source="notes/todo.txt", destination="notes/done.txt")
        self.assertEqual(moved["destination"], "notes/done.txt")
        self.assertTrue((self.repo_path / "notes" / "done.txt").is_file())

        self.write_repo_text("notes/existing.txt", "dest\n")
        overwritten = self.server.move_path(
            source="notes/done.txt",
            destination="notes/existing.txt",
            overwrite=True,
        )
        self.assertEqual(overwritten["destination"], "notes/existing.txt")
        self.assertIn("alpha", (self.repo_path / "notes" / "existing.txt").read_text(encoding="utf-8"))

        self.write_repo_text("src/sample.py", "import os\n\n\ndef alpha(x):\n    return x + 2\n")
        self.server.git_add(paths=["src/sample.py"])
        unstaged = self.server.git_restore(paths=["src/sample.py"], staged=True)
        self.assertTrue(unstaged["staged"])
        self.assertIn("src/sample.py", unstaged["restored"])
        reverted = self.server.git_restore(paths=["src/sample.py"], staged=False)
        self.assertFalse(reverted["staged"])
        self.assertIn("return x + 1", (self.repo_path / "src" / "sample.py").read_text(encoding="utf-8"))

        self.write_repo_text("README.md", "# Updated\n")
        self.commit_all("docs: update readme")
        log = self.server.git_log(limit=1)
        self.assertIn("docs: update readme", log)
        with self.assertRaises(ValueError):
            self.server.git_log(limit=0)

    def test_read_snippet_and_read_batch_error_paths(self):
        snippet = self.server.read_snippet(
            path="src/sample.py",
            start_line=1,
            end_line=3,
            context_after=1,
            output_profile="normal",
        )
        self.assertEqual(snippet["path"], "src/sample.py")
        self.assertEqual(snippet["requested_start_line"], 1)
        self.assertIn("def alpha", snippet["content"])

        batch = self.server.read_batch(
            requests=[
                {"path": "src/sample.py", "start_line": 1, "end_line": 2},
                {"path": "missing.py", "start_line": 1, "end_line": 1},
                {"path": "src/sample.py", "start": 1, "end": 1},
            ],
            output_profile="normal",
        )
        self.assertEqual(batch["count"], 1)
        self.assertEqual(batch["error_count"], 2)
        self.assertEqual(batch["snippets"][0]["path"], "src/sample.py")
        self.assertEqual(batch["errors"][0]["path"], "missing.py")

        with self.assertRaises(ValueError):
            self.server.read_batch(
                requests=[{"path": "src/sample.py", "start_line": 1, "end_line": 1}] * 2,
                max_items=1,
            )

    def test_api_surface_snapshot_write_check_and_missing(self):
        first_symbols = [
            {"path": "src/sample.py", "name": "alpha", "kind": "function"},
            {"path": "src/sample.py", "name": "_private", "kind": "function"},
        ]
        with patch.object(self.server, "symbol_index", return_value=first_symbols):
            written = self.server.api_surface_snapshot(
                path="src",
                snapshot_path=".build/api_snapshot.json",
                mode="write",
                include_private=False,
            )
        self.assertEqual(written["mode"], "write")
        self.assertEqual(written["symbol_count"], 1)
        self.assertTrue((self.repo_path / ".build" / "api_snapshot.json").is_file())

        second_symbols = [
            {"path": "src/sample.py", "name": "beta", "kind": "function"},
        ]
        with patch.object(self.server, "symbol_index", return_value=second_symbols):
            checked = self.server.api_surface_snapshot(
                path="src",
                snapshot_path=".build/api_snapshot.json",
                mode="check",
                include_private=False,
            )
        self.assertEqual(checked["removed_count"], 1)
        self.assertEqual(checked["added_count"], 1)
        self.assertEqual(checked["removed"][0]["name"], "alpha")
        self.assertEqual(checked["added"][0]["name"], "beta")

        with self.assertRaises(FileNotFoundError):
            self.server.api_surface_snapshot(snapshot_path=".build/missing.json", mode="check")

    def test_apply_unified_diff_check_and_apply(self):
        sample_path = self.repo_path / "src" / "sample.py"
        original = sample_path.read_text(encoding="utf-8")
        updated = original.replace("return x + 1", "return x + 3")
        sample_path.write_text(updated, encoding="utf-8")
        diff_text = subprocess.run(
            ["git", "-C", str(self.repo_path), "diff", "--", "src/sample.py"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        sample_path.write_text(original, encoding="utf-8")

        checked = self.server.apply_unified_diff(diff_text=diff_text, check_only=True)
        self.assertTrue(checked["ok"])
        applied = self.server.apply_unified_diff(diff_text=diff_text, check_only=False)
        self.assertTrue(applied["ok"])
        self.assertIn("return x + 3", sample_path.read_text(encoding="utf-8"))

    def test_coding_checks_and_coding_pip_internal_branches(self):
        ok_proc = subprocess.CompletedProcess(args=["cmd"], returncode=0, stdout="ok", stderr="")
        with patch.object(self.server.subprocess, "run", side_effect=[ok_proc, ok_proc]):
            checks = self.server._coding_checks(
                profile="quick",
                target="src/sample.py",
                timeout_seconds=10,
                python_executable=sys.executable,
            )
        self.assertTrue(checks["ok"])
        self.assertIn("memory_trace", checks)
        self.assertEqual(len(checks["steps"]), 2)

        timeout = subprocess.TimeoutExpired(
            cmd=[sys.executable, "-m", "pip", "install", "pytest"],
            timeout=5,
            output="partial out",
            stderr="partial err",
        )
        with patch.object(self.server.subprocess, "run", side_effect=timeout):
            pip_timed = self.server._coding_pip_install(
                packages=["pytest"],
                timeout_seconds=5,
                python_executable=sys.executable,
            )
        self.assertFalse(pip_timed["ok"])
        self.assertTrue(pip_timed["timeout"])

        pip_proc = subprocess.CompletedProcess(
            args=[sys.executable, "-m", "pip", "install", "pytest"],
            returncode=0,
            stdout="installed",
            stderr="",
        )
        with patch.object(self.server.subprocess, "run", return_value=pip_proc):
            pip_ok = self.server._coding_pip_install(
                packages=["pytest"],
                upgrade=True,
                timeout_seconds=5,
                python_executable=sys.executable,
            )
        self.assertTrue(pip_ok["ok"])
        self.assertIn("--upgrade", pip_ok["command"])

        with self.assertRaises(ValueError):
            self.server._coding_checks(profile="bad")
        with self.assertRaises(ValueError):
            self.server._coding_pip_install(packages=["", "pytest"], python_executable=sys.executable)
