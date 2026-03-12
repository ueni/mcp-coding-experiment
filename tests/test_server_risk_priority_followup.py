# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import subprocess
from pathlib import Path
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class ServerRiskPriorityFollowupTest(ServerToolsTestBase):
    def test_git_error_paths(self):
        with patch.object(self.server.subprocess, "run", side_effect=FileNotFoundError("missing git")):
            with self.assertRaisesRegex(RuntimeError, "git executable not found inside container"):
                self.server._git("status")

        stderr_fail = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=2,
            stdout="",
            stderr="fatal: bad revision\n",
        )
        with patch.object(self.server.subprocess, "run", return_value=stderr_fail):
            with self.assertRaisesRegex(RuntimeError, "fatal: bad revision"):
                self.server._git("status")

        stdout_fail = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=2,
            stdout="problem on stdout\n",
            stderr="",
        )
        with patch.object(self.server.subprocess, "run", return_value=stdout_fail):
            with self.assertRaisesRegex(RuntimeError, "problem on stdout"):
                self.server._git("status")

        empty_fail = subprocess.CompletedProcess(
            args=["git", "rev-parse", "HEAD"],
            returncode=3,
            stdout="",
            stderr="",
        )
        with patch.object(self.server.subprocess, "run", return_value=empty_fail):
            with self.assertRaisesRegex(RuntimeError, "failed with exit code 3"):
                self.server._git("rev-parse", "HEAD")

    def test_semantic_find_and_symbol_index_edge_paths(self):
        with self.assertRaises(ValueError):
            self.server.semantic_find(query="   ")
        with self.assertRaises(ValueError):
            self.server.semantic_find(query="alpha", max_results=0)
        with self.assertRaises(ValueError):
            self.server.semantic_find(query="alpha", summary_mode="bad")
        with self.assertRaises(FileNotFoundError):
            self.server.semantic_find(query="alpha", path="missing")

        with patch.object(self.server, "find_paths", return_value=["src/sample.py"]), patch.object(
            self.server,
            "symbol_index",
            return_value=[{"path": "src/sample.py", "name": "alpha", "line_start": 1}],
        ), patch.object(
            self.server,
            "grep",
            return_value=[{"path": "src/sample.py", "line": 1, "column": 1, "match": "alpha"}],
        ), patch.object(
            self.server,
            "_result_store_put",
            return_value="rid-semantic",
        ):
            compressed = self.server.semantic_find(
                query="alpha",
                path="src",
                output_profile="normal",
                compress=True,
                store_result=True,
                adaptive_limits=False,
            )
        self.assertEqual(compressed["result_id"], "rid-semantic")
        self.assertIn("results_compressed", compressed)
        self.assertNotIn("results", compressed)

        with self.assertRaises(ValueError):
            self.server.symbol_index(path="src", max_symbols=0)
        with self.assertRaises(FileNotFoundError):
            self.server.symbol_index(path="missing")

        skip_py = self.write_repo_text("src/skip.py", "def skip_me():\n    return 1\n")
        bad_py = self.write_repo_text("src/bad.py", "def bad_read():\n    return 2\n")
        good_py = self.repo_path / "src" / "sample.py"
        original_read_text = Path.read_text

        def maybe_fail_read_text(path_obj, *args, **kwargs):
            if path_obj == bad_py:
                raise OSError("unreadable")
            return original_read_text(path_obj, *args, **kwargs)

        with patch.object(self.server, "_cache_get", return_value=None), patch.object(
            self.server,
            "_iter_candidate_files",
            return_value=[skip_py, bad_py, good_py],
        ), patch.object(
            self.server,
            "_is_likely_binary",
            side_effect=lambda p, **kwargs: p == skip_py,
        ), patch.object(
            Path,
            "read_text",
            new=maybe_fail_read_text,
        ):
            symbols = self.server.symbol_index(
                path="src",
                adaptive_limits=False,
                output_profile="normal",
            )
        self.assertTrue(any(row["path"] == "src/sample.py" for row in symbols))

    def test_self_test_command_construction_paths(self):
        internal_dir = self.repo_path / "internal_selftests"
        internal_dir.mkdir(parents=True, exist_ok=True)
        (internal_dir / "test_internal.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        ok_proc = subprocess.CompletedProcess(args=["python"], returncode=0, stdout="ok", stderr="")
        seen_commands = []

        def fake_run(cmd, **kwargs):
            seen_commands.append((list(cmd), kwargs))
            return ok_proc

        with patch.object(self.server.subprocess, "run", side_effect=fake_run), patch.object(
            self.server,
            "INTERNAL_SELF_TESTS_DIR",
            internal_dir,
        ):
            internal = self.server.self_test(
                runner="unittest",
                target="tests",
                verbose=True,
                timeout_seconds=5,
                fail_fast=True,
            )
            file_target = self.server.self_test(
                runner="unittest",
                target="repo:tests/test_smoke.py",
                verbose=True,
                timeout_seconds=5,
                fail_fast=False,
            )
            dir_target = self.server.self_test(
                runner="unittest",
                target="repo:tests",
                verbose=True,
                timeout_seconds=5,
                fail_fast=False,
            )
            pytest_target = self.server.self_test(
                runner="pytest",
                target="repo:tests",
                verbose=True,
                timeout_seconds=5,
                fail_fast=False,
            )

        self.assertTrue(internal["ok"])
        self.assertEqual(internal["command"][-1], "-f")
        self.assertIn("-v", internal["command"])
        self.assertIn("-v", file_target["command"])
        self.assertIn("-v", dir_target["command"])
        self.assertEqual(pytest_target["command"][:2], ["pytest", "-v"])
        self.assertEqual(file_target["command"][-1], "-v")
        self.assertEqual(dir_target["command"][-1], "-v")

        original_resolve_repo_path = self.server._resolve_repo_path

        def maybe_none_repo_path(value):
            if value == "anything.py":
                return None
            return original_resolve_repo_path(value)

        with patch.object(self.server, "_resolve_repo_path", side_effect=maybe_none_repo_path):
            with self.assertRaisesRegex(RuntimeError, "repo target path resolution failed"):
                self.server.self_test(
                    runner="unittest",
                    target="repo:anything.py",
                    verbose=False,
                    timeout_seconds=5,
                )

    def test_code_index_router_risk_modes(self):
        with self.assertRaises(ValueError):
            self.server.code_index_router(mode="bad")

        dep_payload = {"schema": "dependency_map.v1", "ok": True}
        call_payload = {"schema": "call_graph.v1", "ok": True}
        search_payload = {"schema": "semantic_find.v1", "ok": True}

        with patch.object(self.server, "dependency_map", return_value=dep_payload) as dep_map, patch.object(
            self.server,
            "call_graph",
            return_value=call_payload,
        ) as call_graph, patch.object(
            self.server,
            "semantic_find",
            return_value=search_payload,
        ) as semantic_find:
            deps = self.server.code_index_router(
                mode="deps",
                path="src",
                include_stdlib=True,
                output_profile="normal",
                fields=["from", "to"],
                offset=1,
                limit=2,
                summary_mode="quick",
                compress=True,
                store_result=True,
            )
            calls = self.server.code_index_router(
                mode="calls",
                path="src",
                output_profile="normal",
                fields=["caller", "callee"],
                offset=2,
                limit=3,
                summary_mode="quick",
                compress=True,
                store_result=True,
            )
            search = self.server.code_index_router(
                mode="search",
                path="src",
                query="alpha",
                output_profile="normal",
                fields=["path"],
                offset=3,
                limit=4,
                summary_mode="quick",
                compress=True,
                store_result=True,
                use_local_rerank=False,
                local_rerank_top_k=7,
            )

        self.assertEqual(deps["result"], dep_payload)
        self.assertEqual(calls["result"], call_payload)
        self.assertEqual(search["result"], search_payload)
        self.assertTrue(dep_map.call_args.kwargs["include_stdlib"])
        self.assertTrue(dep_map.call_args.kwargs["compress"])
        self.assertTrue(call_graph.call_args.kwargs["compress"])
        self.assertEqual(semantic_find.call_args.kwargs["query"], "alpha")
        self.assertFalse(semantic_find.call_args.kwargs["use_local_rerank"])
        self.assertEqual(semantic_find.call_args.kwargs["local_rerank_top_k"], 7)
