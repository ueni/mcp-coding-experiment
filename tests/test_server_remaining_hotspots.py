# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class _PipeStdout:
    def __init__(self):
        self.closed = False

    def fileno(self):
        return 0

    def close(self):
        self.closed = True


class _PipeProc:
    def __init__(self):
        self.stdin = SimpleNamespace(write=lambda data: None, flush=lambda: None, close=lambda: None)
        self.stdout = _PipeStdout()
        self._poll = None

    def poll(self):
        return self._poll

    def terminate(self):
        self._poll = 0

    def wait(self, timeout=None):
        del timeout
        self._poll = 0
        return 0


class _FakeTsNode:
    def __init__(self, node_type, start, end, children=None):
        self.type = node_type
        self.start_point = start
        self.end_point = end
        self.children = children or []


class _FakeTsParser:
    def __init__(self, root):
        self._root = root

    def parse(self, _source):
        return SimpleNamespace(root_node=self._root)


class _CtxResp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ServerRemainingHotspotsTest(ServerToolsTestBase):
    def test_terminal_support_session_pipe_start_and_repo_index_validations(self):
        proc = _PipeProc()
        with patch.object(self.server.pty, "openpty", side_effect=OSError("no pty")), patch.object(
            self.server.subprocess,
            "Popen",
            return_value=proc,
        ), patch.object(self.server.os, "set_blocking", return_value=None), patch.object(
            self.server,
            "_terminal_read_available",
            return_value="boot",
        ):
            started = self.server.terminal_support_session(
                mode="start",
                command=["cat"],
                cwd=".",
                include_output=True,
            )
        self.assertEqual(started["backend"], "pipe")
        self.assertEqual(started["output"], "boot")

        listed = self.server.terminal_support_session(mode="list")
        self.assertEqual(listed["schema"], "terminal_support_session.v1")
        self.assertGreaterEqual(len(listed["sessions"]), 1)

        with self.assertRaises(ValueError):
            self.server.repo_index_daemon(mode="bad")
        with self.assertRaises(ValueError):
            self.server.repo_index_daemon(mode="refresh", max_files=0)
        with self.assertRaises(ValueError):
            self.server.repo_index_daemon(mode="refresh", summary_mode="bad")
        with self.assertRaises(FileNotFoundError):
            self.server.repo_index_daemon(mode="read")

    def test_self_test_unittest_dir_and_literal_target_branches(self):
        ok_proc = subprocess.CompletedProcess(args=[sys.executable], returncode=0, stdout="ok", stderr="")
        with patch.object(self.server.subprocess, "run", return_value=ok_proc):
            dir_out = self.server.self_test(
                runner="unittest",
                target="repo:tests",
                verbose=False,
                fail_fast=True,
                timeout_seconds=5,
            )
            literal_out = self.server.self_test(
                runner="unittest",
                target="repo:not_a_real_module",
                verbose=True,
                fail_fast=False,
                timeout_seconds=5,
            )
        self.assertTrue(dir_out["ok"])
        self.assertIn("discover", dir_out["command"])
        self.assertEqual(literal_out["command"][-1], "not_a_real_module")
        with self.assertRaises(ValueError):
            self.server.self_test(timeout_seconds=0)

    def test_grep_and_replace_more_branches(self):
        self.write_repo_text("docs/dup.txt", "alpha alpha\n")
        file_matches = self.server.grep(
            pattern="alpha",
            path="docs/dup.txt",
            dedupe=False,
            output_profile="compact",
        )
        handled = self.server.grep(
            pattern="alpha",
            path="docs/dup.txt",
            store_result=True,
            output_profile="compact",
        )
        self.assertEqual(len(file_matches), 2)
        self.assertEqual(handled[0]["schema"], "grep.result_handle.v1")

        root_replace = self.server.replace_in_files(
            path="docs/dup.txt",
            pattern="alpha",
            replacement="beta",
            recursive=False,
            dry_run=False,
            regex=False,
            max_files=1,
            max_replacements=5,
        )
        self.assertEqual(root_replace["path"], "docs/dup.txt")
        self.assertEqual(root_replace["files_changed_count"], 1)
        with self.assertRaises(ValueError):
            self.server.replace_in_files(path="docs/dup.txt", pattern="(", replacement="x", regex=True)
        with self.assertRaises(ValueError):
            self.server.replace_in_files(path="docs/dup.txt", pattern="x", replacement="y", max_files=0)

    def test_local_endpoint_embed_diagram_and_image_branches(self):
        for key in ("text", "output", "completion"):
            body = ("{\"" + key + "\": \"value\"}").encode("utf-8")
            with patch.object(self.server, "_urlopen_with_host_certs", return_value=_CtxResp(body)):
                value = self.server._local_infer_via_endpoint(
                    prompt="p",
                    model="m",
                    max_tokens=5,
                    temperature=0.1,
                )
            self.assertEqual(value, "value")

        with patch.object(self.server, "LOCAL_EMBED_BACKEND", "hash"):
            backend, vectors = self.server._local_embed_vectors(["alpha"], backend="auto", normalize=False)
        self.assertEqual(backend, "hash")
        self.assertEqual(len(vectors), 1)

        with patch.object(self.server, "dependency_map", return_value={"edges": [{"from": "a.py", "to": "b.py"}, {"from": "b.py", "to": "c.py"}] }):
            flow = self.server.diagram_from_code(path="src", diagram_type="flowchart", max_nodes=1, output_profile="normal")
        self.assertIn("flowchart LR", flow["mermaid"])
        with self.assertRaises(FileNotFoundError):
            self.server.diagram_from_code(path="missing", diagram_type="flowchart")

        with patch.object(self.server, "Image", None), patch.object(self.server, "pytesseract", None):
            with self.assertRaises(RuntimeError):
                self.server.image_interpret(image_path="docs/a.md", mode="ocr")

    def test_reader_error_paths_and_query_helpers(self):
        pdf_path = self.write_repo_text("files/sample.pdf", "x")
        xls_path = self.write_repo_text("files/sample.xls", "x")
        ppt_path = self.write_repo_text("files/sample.ppt", "binary")

        with patch.object(self.server, "PdfReader", None):
            with self.assertRaises(RuntimeError):
                self.server._read_pdf_text(pdf_path, max_pages=1)
        with patch.object(self.server, "xlrd", None):
            with self.assertRaises(RuntimeError):
                self.server._read_xls_text(xls_path, max_rows_per_sheet=1)
        with patch.object(self.server.shutil, "which", return_value=None):
            slides, meta, warnings = self.server._read_ppt_legacy_text(ppt_path, max_slides=1, max_chars_per_slide=20)
        self.assertGreaterEqual(len(warnings), 1)
        self.assertEqual(meta["slides_read"], len(slides))

        self.assertEqual(self.server._parse_query_path(""), [])
        with self.assertRaises(ValueError):
            self.server._parse_query_path("items[abc]")
        with self.assertRaises(ValueError):
            self.server._query_value({"a": 1}, "missing")

    def test_tree_sitter_fast_path_commit_lint_and_smart_fix_remaining(self):
        root = _FakeTsNode(
            "module",
            (0, 0),
            (2, 0),
            children=[_FakeTsNode("function_definition", (0, 0), (1, 0))],
        )
        with patch.object(self.server, "_ts_get_parser", return_value=_FakeTsParser(root)):
            nodes = self.server._tree_sitter_parse_nodes(
                source="def alpha():\n    pass\n",
                language="python",
                node_types=["function_definition"],
                max_nodes=10,
            )
        self.assertEqual(nodes[0]["type"], "function_definition")

        with patch.object(
            self.server,
            "token_budget_guard",
            return_value={"max_output_chars": 1000, "default_output_profile": "compact"},
        ), patch.object(
            self.server,
            "release_readiness",
            return_value={"ok": False},
        ), patch.object(
            self.server,
            "required_tool_chain",
            return_value={"ok": False},
        ):
            fast = self.server.fast_path_dev(
                task="check",
                refresh_index=False,
                run_readiness=True,
                enforce_tool_chain=True,
                store_result=False,
            )
        self.assertFalse(fast["ok"])

        with patch.object(self.server, "_require_git_repo", return_value=None), patch.object(
            self.server,
            "_git",
            side_effect=[
                SimpleNamespace(stdout="docs: update readme"),
                SimpleNamespace(stdout="README.md\ntests/test_sample.py\nsource/server.py\n"),
            ],
        ):
            hinted = self.server.commit_lint_tag(message="", ref="HEAD", include_diff_hints=True)
        self.assertIn("docs", hinted["tags"])
        self.assertIn("test", hinted["tags"])
        self.assertIn("infra", hinted["tags"])

        executed = self.server.smart_fix_batch(
            findings=[
                {"path": "README.md", "search": "Test", "replacement": "Demo"},
                {"path": "README.md", "search": "Missing", "replacement": "None"},
            ],
            mode="execute",
            regex=False,
            replace_all=True,
            run_validation=False,
        )
        self.assertEqual(executed["applied_count"], 1)
        self.assertEqual(executed["skipped_count"], 1)
