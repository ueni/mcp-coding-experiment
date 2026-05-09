# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import ast
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class _DummyImageCtx:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePtyProc:
    def __init__(self):
        self.stdin = None
        self.stdout = None
        self._poll = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True
        self._poll = 0

    def kill(self):
        self.killed = True
        self._poll = 9

    def wait(self, timeout=None):
        del timeout
        self._poll = 0
        return 0


class ServerCoverageFinalPushTest(ServerToolsTestBase):
    def test_low_level_lossless_delta_and_node_helpers(self):
        tree = ast.parse("import os\nfrom pkg import mod\nvalue = func()\n")
        import_node = tree.body[0]
        import_from_node = tree.body[1]
        call_node = tree.body[2].value
        constant_node = ast.parse("1").body[0].value

        self.assertEqual(self.server._node_display_name(import_node), "os")
        self.assertEqual(self.server._node_display_name(import_from_node), "pkg:mod")
        self.assertEqual(self.server._node_display_name(call_node), "func")
        self.assertEqual(self.server._node_display_name(constant_node), "")

        blob_path = self.repo_path / "files" / "blobs.json"
        self.assertEqual(
            self.server._lossless_blob_store_load(blob_path),
            {"schema": "lossless_blob_store.v1", "blobs": {}},
        )
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_text("{bad", encoding="utf-8")
        self.assertEqual(
            self.server._lossless_blob_store_load(blob_path),
            {"schema": "lossless_blob_store.v1", "blobs": {}},
        )
        blob_path.write_text(json.dumps({"blobs": ["x"]}), encoding="utf-8")
        self.assertEqual(
            self.server._lossless_blob_store_load(blob_path),
            {"schema": "lossless_blob_store.v1", "blobs": {}},
        )

        self.assertEqual(
            self.server._lossless_decode_key('{"$sym":"SYM"}', {"SYM": "expanded"}),
            "expanded",
        )
        self.assertEqual(self.server._lossless_decode_key("{", {}), "{")

        root = {"items": []}
        updated = self.server._delta_set_value(root, ["items", "1", "name"], "value")
        self.assertEqual(updated["items"][1]["name"], "value")
        with self.assertRaises(ValueError):
            self.server._delta_set_value(1, ["a"], "x")

        removed = self.server._delta_remove_value({"items": [1, 2, 3]}, ["items", "1"])
        self.assertEqual(removed["items"], [1, 3])
        untouched = self.server._delta_remove_value({"items": [1]}, ["items", "9"])
        self.assertEqual(untouched["items"], [1])

    def test_iter_candidate_and_document_error_branches(self):
        hidden = self.write_repo_text(".hidden.txt", "secret\n")
        self.write_repo_text("docs/visible.txt", "visible\n")
        self.write_repo_text("docs/.hidden.md", "hidden\n")
        self.write_repo_text("docs/nested/value.txt", "nested\n")

        self.assertEqual(
            list(self.server._iter_candidate_files(hidden, recursive=False, include_hidden=False)),
            [],
        )
        self.assertEqual(
            list(self.server._iter_candidate_files(hidden, recursive=False, include_hidden=True)),
            [hidden],
        )
        non_recursive = {
            str(p.relative_to(self.repo_path)).replace("\\", "/")
            for p in self.server._iter_candidate_files(self.repo_path / "docs", recursive=False)
        }
        self.assertIn("docs/visible.txt", non_recursive)
        self.assertNotIn("docs/.hidden.md", non_recursive)

        ods_path = self.write_repo_text("files/bad.ods", "not zip")
        with self.assertRaises(ValueError):
            self.server._read_opendoc_text(ods_path, ext=".ods", max_rows_per_sheet=0)
        with self.assertRaises(RuntimeError):
            self.server._read_opendoc_text(ods_path, ext=".ods", max_rows_per_sheet=1)

        missing_xml = self.repo_path / "files" / "missing-content.ods"
        with zipfile.ZipFile(missing_xml, "w") as zf:
            zf.writestr("meta.xml", "<meta />")
        with self.assertRaises(RuntimeError):
            self.server._read_opendoc_text(missing_xml, ext=".odt", max_rows_per_sheet=1)

        bad_xml = self.repo_path / "files" / "bad-content.ods"
        with zipfile.ZipFile(bad_xml, "w") as zf:
            zf.writestr("content.xml", "<broken")
        with self.assertRaises(RuntimeError):
            self.server._read_opendoc_text(bad_xml, ext=".ods", max_rows_per_sheet=1)

        odp_path = self.write_repo_text("files/bad.odp", "not zip")
        with self.assertRaises(ValueError):
            self.server._read_odp_presentation(odp_path, max_slides=0, max_chars_per_slide=10)
        with self.assertRaises(ValueError):
            self.server._read_odp_presentation(odp_path, max_slides=1, max_chars_per_slide=0)
        with self.assertRaises(RuntimeError):
            self.server._read_odp_presentation(odp_path, max_slides=1, max_chars_per_slide=10)

        missing_odp = self.repo_path / "files" / "missing-content.odp"
        with zipfile.ZipFile(missing_odp, "w") as zf:
            zf.writestr("other.xml", "<x />")
        with self.assertRaises(RuntimeError):
            self.server._read_odp_presentation(missing_odp, max_slides=1, max_chars_per_slide=10)

        bad_odp = self.repo_path / "files" / "bad-content.odp"
        with zipfile.ZipFile(bad_odp, "w") as zf:
            zf.writestr("content.xml", "<broken")
        with self.assertRaises(RuntimeError):
            self.server._read_odp_presentation(bad_odp, max_slides=1, max_chars_per_slide=10)

    def test_memory_cache_and_spdx_branches(self):
        with self.assertRaises(ValueError):
            self.server.memory_auto_compact(threshold_entries=0)
        with self.assertRaises(ValueError):
            self.server.memory_auto_compact(threshold_chars=255)
        with self.assertRaises(ValueError):
            self.server.memory_auto_compact(keep_entries=0)
        with self.assertRaises(ValueError):
            self.server.memory_auto_compact(summary_max_chars=127)

        mem_file = self.repo_path / ".codebase-tooling-mcp" / "memory" / "context_memory.json"
        mem_file.parent.mkdir(parents=True, exist_ok=True)
        mem_file.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "namespace": "keep",
                            "key": "fresh",
                            "value": {"x": 1},
                            "confidence": 1.0,
                            "source": "t",
                            "tags": [],
                            "created_at": "2026-03-12T00:00:00+00:00",
                            "updated_at": "2026-03-12T00:00:00+00:00",
                            "expires_at": None,
                        },
                        {
                            "namespace": "skip",
                            "key": "other",
                            "value": {"y": 2},
                            "confidence": 1.0,
                            "source": "t",
                            "tags": [],
                            "created_at": "2026-03-12T00:00:00+00:00",
                            "updated_at": "2026-03-12T00:00:00+00:00",
                            "expires_at": None,
                        },
                        {
                            "namespace": "keep",
                            "key": "expired",
                            "value": {"z": 3},
                            "confidence": 1.0,
                            "source": "t",
                            "tags": [],
                            "created_at": "2020-01-01T00:00:00+00:00",
                            "updated_at": "2020-01-01T00:00:00+00:00",
                            "expires_at": "2020-01-02T00:00:00+00:00",
                        },
                    ],
                    "summaries": [],
                    "decisions": [],
                }
            ),
            encoding="utf-8",
        )
        compact_none = self.server.memory_auto_compact(
            namespace="keep",
            threshold_entries=10,
            threshold_chars=99999,
        )
        self.assertFalse(compact_none["compacted"])

        with patch.object(self.server, "ALLOW_MUTATIONS", False):
            compact_disabled = self.server.memory_auto_compact(
                namespace="keep",
                threshold_entries=0 + 1,
                threshold_chars=256,
                keep_entries=1,
                summary_max_chars=128,
                drop_expired=True,
            )
        self.assertEqual(compact_disabled["reason"], "mutations_disabled")

        with self.assertRaises(ValueError):
            self.server.memory_upsert(namespace=" ", key="k", value={})
        with self.assertRaises(ValueError):
            self.server.memory_upsert(namespace="n", key="k", value={}, confidence=1.5)
        self.server.memory_upsert(namespace="n", key="k", value={"v": 1}, source="a", tags=["x"])
        updated = self.server.memory_upsert(namespace="n", key="k", value={"v": 2}, source="b", tags=["y"])
        self.assertTrue(updated["updated"])

        mem_file.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "namespace": "n1",
                            "key": "k1",
                            "value": {"a": 1},
                            "expires_at": None,
                        },
                        {
                            "namespace": "n2",
                            "key": "k2",
                            "value": {"b": 2},
                            "expires_at": None,
                        },
                        {
                            "namespace": "n1",
                            "key": "expired",
                            "value": {"c": 3},
                            "expires_at": "2020-01-01T00:00:00+00:00",
                        },
                    ],
                    "summaries": [
                        {"namespace": "n2", "focus": "skip", "summary": "x", "expires_at": None},
                        {"namespace": "n1", "focus": "expired", "summary": "y", "expires_at": "2020-01-01T00:00:00+00:00"},
                        {"namespace": "n1", "focus": "keep", "summary": "z", "expires_at": None},
                    ],
                    "decisions": [],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            self.server.memory_get(max_entries=0)
        filtered = self.server.memory_get(
            namespace="n1",
            key="k1",
            max_entries=1,
            include_expired=False,
            include_summaries=True,
        )
        self.assertEqual(filtered["count"], 1)
        self.assertEqual(filtered["summary_count"], 1)
        missed = self.server.memory_get(namespace="missing", include_summaries=False, include_effective_decisions=False)
        self.assertEqual(missed["count"], 0)

        with self.assertRaises(ValueError):
            self.server._cache_prune(max_age_minutes=0)
        cache_file = self.repo_path / ".codebase-tooling-mcp" / "cache" / "tool_cache.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "entries": {
                        "tool": {
                            "bad-row": ["not", "dict"],
                            "old": {"updated_at": "2000-01-01T00:00:00+00:00", "value": {"x": 1}},
                            "new": {"updated_at": "2999-01-01T00:00:00+00:00", "value": {"y": 1}},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        pruned = self.server._cache_prune(max_age_minutes=1)
        self.assertEqual(pruned["removed_entries"], 2)

        self.write_repo_text(
            "src/licensed.py",
            "# SPDX-License-Identifier: MIT OR Apache-2.0 AND LicenseRef-Custom\nprint('x')\n",
        )
        self.write_repo_text("src/no_spdx.py", "print('plain')\n")
        self.write_repo_text("LICENSES/ref.txt", "skip\n")
        self.write_repo_text(".codebase-tooling-mcp/generated.py", "# SPDX-License-Identifier: GPL-3.0-only\n")
        ids = self.server._collect_spdx_license_ids(path="src")
        self.assertIn("MIT", ids)
        self.assertIn("Apache-2.0", ids)
        self.assertNotIn("LicenseRef-Custom", ids)

        original_read_lines = self.server._read_lines

        def maybe_fail_read_lines(path, encoding="utf-8"):
            if path.name == "licensed.py":
                raise OSError("blocked")
            return original_read_lines(path, encoding=encoding)

        with patch.object(self.server, "_read_lines", side_effect=maybe_fail_read_lines):
            missing = self.server._collect_missing_spdx_headers(path="src")
        self.assertIn("src/no_spdx.py", missing)

    def test_build_log_hooks_lab_and_main_branches(self):
        noisy = "\n".join(
            [
                "no space left on device",
                "failed to solve with frontend dockerfile.v0: not found",
                "pull access denied",
                "error getting credentials from helper",
                "tls handshake timeout",
                "apt-get temporary failure resolving mirror",
                "permission denied /var/run/docker.sock",
                "executor failed running exit code: 127",
                "executor failed running exit code: 1",
                "failed to read dockerfile",
                "cannot connect to the docker daemon",
                "context canceled",
            ]
        )
        proposals = self.server._build_log_proposals(noisy, "")
        self.assertGreaterEqual(len(proposals), 10)
        fallback = self.server._build_log_proposals("", "")
        self.assertEqual(fallback[0]["confidence"], "low")

        with self.assertRaises(ValueError):
            self.server.lab_repo_digital_twin(max_files=0)
        with self.assertRaises(ValueError):
            self.server.lab_repo_digital_twin(hotspot_limit=0)
        with patch.object(self.server, "_run_lab_script", return_value={"ok": True}) as run_lab:
            out = self.server.lab_repo_digital_twin(json_path="docs/twin.json", markdown_path="docs/twin.md")
        self.assertTrue(out["ok"])
        self.assertEqual(run_lab.call_args[0][0], "repo_digital_twin.py")

        with patch.object(self.server, "_git", return_value=SimpleNamespace(stdout="/tmp/outside-hooks\n")):
            with self.assertRaises(ValueError):
                self.server.install_git_hooks()

        with patch.object(self.server, "_git", return_value=SimpleNamespace(stdout=".git/hooks\n")):
            with self.assertRaises(ValueError):
                self.server.install_git_hooks(install_pre_commit=False, install_pre_push=False)

        hooks_dir = self.repo_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        (hooks_dir / "pre-commit").write_text("existing\n", encoding="utf-8")
        (hooks_dir / "pre-push").write_text("existing\n", encoding="utf-8")
        with patch.object(self.server, "_git", return_value=SimpleNamespace(stdout=".git/hooks\n")):
            hooks = self.server.install_git_hooks(
                include_foss_reports=False,
                include_lab_reports=False,
                overwrite=False,
            )
        self.assertEqual(hooks["installed"], [])
        self.assertEqual(len(hooks["skipped"]), 2)

        with patch.object(self.server, "MCP_TRANSPORT", "direct"), patch.object(self.server.mcp, "run") as mcp_run:
            self.server.main()
        mcp_run.assert_called_once()

        fake_uvicorn = SimpleNamespace(run=lambda *args, **kwargs: None)
        with patch.object(self.server, "MCP_TRANSPORT", "http"), patch.dict(sys.modules, {"uvicorn": fake_uvicorn}):
            with patch.object(fake_uvicorn, "run") as uvicorn_run:
                self.server.main()
        uvicorn_run.assert_called_once()

        with patch.object(self.server, "MCP_TRANSPORT", "bad"):
            with self.assertRaises(ValueError):
                self.server.main()

    def test_find_grep_replace_and_summarize_diff_branches(self):
        self.write_repo_text("docs/dup.txt", "Alpha alpha\nAlpha\n")
        self.write_repo_text("docs/more.txt", "Alpha beta\n")
        self.write_repo_text("docs/.hidden.txt", "Alpha\n")
        (self.repo_path / "docs" / "blob.bin").write_bytes(b"\x00\xff\x00")
        self.write_repo_text("docs/bad.txt", "Alpha\n")

        with self.assertRaises(ValueError):
            self.server.find_paths(max_entries=0)
        with self.assertRaises(ValueError):
            self.server.find_paths(max_depth=-1)
        with self.assertRaises(ValueError):
            self.server.find_paths(file_type="bad")
        with self.assertRaises(FileNotFoundError):
            self.server.find_paths(path="missing")

        dirs = self.server.find_paths(path=".", file_type="dir", recursive=False, output_profile="normal")
        self.assertIn("docs/", dirs)

        with self.assertRaises(ValueError):
            self.server.grep(pattern="x", max_matches=0)
        with self.assertRaises(ValueError):
            self.server.grep(pattern="x", max_file_bytes=0)
        with self.assertRaises(ValueError):
            self.server.grep(pattern="x", summary_mode="bad")
        with self.assertRaises(FileNotFoundError):
            self.server.grep(pattern="x", path="missing")
        with self.assertRaises(ValueError):
            self.server.grep(pattern="(", path="docs")

        original_open = Path.open

        def maybe_fail_open(path_obj, *args, **kwargs):
            if path_obj.name == "bad.txt":
                raise OSError("blocked")
            return original_open(path_obj, *args, **kwargs)

        with patch.object(Path, "open", new=maybe_fail_open), patch.object(
            self.server,
            "_result_store_put",
            return_value="rid-grep",
        ):
            compressed = self.server.grep(
                pattern="Alpha",
                path="docs",
                recursive=False,
                case_insensitive=False,
                max_matches=300,
                adaptive_limits=False,
                compress=True,
                store_result=True,
                output_profile="normal",
            )
        self.assertEqual(compressed[0]["result_id"], "rid-grep")

        limited = self.server.grep(
            pattern="Alpha",
            path="docs/dup.txt",
            dedupe=False,
            max_matches=1,
            adaptive_limits=False,
            output_profile="compact",
        )
        self.assertEqual(len(limited), 1)

        quick = self.server.grep(
            pattern="Alpha",
            path="docs",
            include_globs=["src/*.py"],
            summary_mode="quick",
            adaptive_limits=False,
        )
        self.assertEqual(quick[0]["total_matches"], 0)

        with self.assertRaises(ValueError):
            self.server.replace_in_files(pattern="x", replacement="y", max_replacements=0)
        with self.assertRaises(ValueError):
            self.server.replace_in_files(pattern="x", replacement="y", max_file_bytes=0)
        with self.assertRaises(FileNotFoundError):
            self.server.replace_in_files(pattern="x", replacement="y", path="missing")

        original_read_text = Path.read_text

        def maybe_fail_read_text(path_obj, *args, **kwargs):
            if path_obj.name == "bad.txt":
                raise OSError("blocked")
            return original_read_text(path_obj, *args, **kwargs)

        replaced = None
        with patch.object(Path, "read_text", new=maybe_fail_read_text):
            replaced = self.server.replace_in_files(
                pattern="Alpha",
                replacement="Omega",
                path="docs",
                recursive=False,
                regex=False,
                dry_run=True,
                max_files=1,
                include_globs=["docs/*.txt"],
            )
        self.assertTrue(replaced["files_limit_reached"])
        self.assertEqual(replaced["files_changed_count"], 1)

        with self.assertRaises(ValueError):
            self.server.summarize_diff(patch_unified=-1)
        numstat = "5\t1\tpackage.json\njunk line\n"
        patch_text = "+++ b/package.json\n+TODO ship it\n"
        with patch.object(
            self.server,
            "_git",
            side_effect=[SimpleNamespace(stdout=numstat), SimpleNamespace(stdout=patch_text)],
        ):
            summary = self.server.summarize_diff(
                ref="HEAD~1",
                staged=True,
                pathspec="README.md",
                output_profile="verbose",
                include_patch=True,
                patch_unified=1,
            )
        self.assertIn("package.json", summary["risk_flags"]["risky_files"])
        self.assertEqual(summary["risk_flags"]["todo_like_additions"], 1)
        self.assertEqual(summary["patch_unified"], 1)
        self.assertIn("files_sorted_by_churn", summary)

    def test_dependency_call_ast_and_tree_sitter_branches(self):
        self.write_repo_text("src/rel_only.py", "from . import sample\n")
        self.write_repo_text("src/bad_syntax.py", "def broken(:\n")
        self.write_repo_text("src/many_imports.py", "import os\nimport sys\n")
        self.write_repo_text(
            "src/many_calls.py",
            "def helper():\n    return 1\n\n"
            "def caller():\n    helper()\n    print('x')\n    (lambda z: z)(1)\n",
        )

        with self.assertRaises(ValueError):
            self.server.dependency_map(path="src", max_files=0)
        with self.assertRaises(FileNotFoundError):
            self.server.dependency_map(path="missing")

        with patch.object(self.server, "_result_store_put", return_value="rid-dep"):
            dep = self.server.dependency_map(
                path="src",
                include_stdlib=True,
                max_files=1,
                adaptive_limits=False,
                output_profile="compact",
                compress=True,
                store_result=True,
            )
        self.assertEqual(dep["result_id"], "rid-dep")

        verbose_dep = self.server.dependency_map(
            path="src",
            include_stdlib=True,
            max_files=20,
            adaptive_limits=False,
            output_profile="verbose",
        )
        self.assertIn("unresolved_imports", verbose_dep)

        with self.assertRaises(ValueError):
            self.server.call_graph(path="src", summary_mode="bad")
        with self.assertRaises(FileNotFoundError):
            self.server.call_graph(path="missing")

        with patch.object(self.server, "_result_store_put", return_value="rid-call"):
            call = self.server.call_graph(
                path="src",
                max_edges=1,
                adaptive_limits=False,
                output_profile="normal",
                compress=True,
                store_result=True,
            )
        self.assertEqual(call["result_id"], "rid-call")
        self.assertIn("edges_compressed", call)

        with self.assertRaises(ValueError):
            self.server.ast_search(path="src", max_results=0)
        with self.assertRaises(FileNotFoundError):
            self.server.ast_search(path="missing")
        with self.assertRaises(ValueError):
            self.server.ast_search(path="src", node_type="Nope")

        imports = self.server.ast_search(path="src", node_type="Import", max_results=1)
        self.assertEqual(len(imports), 1)
        filtered = self.server.ast_search(path="src", node_type="Call", name_pattern="does-not-match")
        self.assertEqual(filtered, [])

        with self.assertRaises(ValueError):
            self.server.tree_sitter_core(mode="bad")
        with self.assertRaises(FileNotFoundError):
            self.server.tree_sitter_core(mode="parse", path="missing")

        no_match = self.server.tree_sitter_core(
            mode="parse",
            path="src",
            language="javascript",
            output_profile="normal",
        )
        self.assertEqual(no_match["file_count"], 0)

        with patch.object(self.server, "_tree_sitter_available", return_value=True), patch.object(
            self.server,
            "_tree_sitter_parse_nodes",
            side_effect=RuntimeError("boom"),
        ), patch.object(
            self.server,
            "ast_search",
            return_value=[{"node_type": "FunctionDef", "line": 1, "column": 1, "end_line": 2}],
        ), patch.object(
            self.server,
            "_result_store_put",
            return_value="rid-ts",
        ):
            parsed = self.server.tree_sitter_core(
                mode="parse",
                path="src/sample.py",
                output_profile="normal",
                fields=["path", "nodes"],
                compress=True,
                store_result=True,
                max_files=1,
                max_nodes=1,
                adaptive_limits=False,
            )
        self.assertEqual(parsed["result_id"], "rid-ts")
        self.assertIn("files_compressed", parsed)

        regex_filtered = self.server.tree_sitter_core(
            mode="search",
            path="src",
            text_pattern="zzzz-no-match",
            output_profile="normal",
            adaptive_limits=False,
        )
        self.assertEqual(regex_filtered["file_count"], 0)

    def test_terminal_repo_index_and_image_branches(self):
        fake_proc = _FakePtyProc()
        writes = []
        closes = []
        with patch.object(self.server.pty, "openpty", return_value=(11, 12)), patch.object(
            self.server.subprocess,
            "Popen",
            return_value=fake_proc,
        ), patch.object(self.server.os, "set_blocking", return_value=None), patch.object(
            self.server.os,
            "write",
            side_effect=lambda fd, data: writes.append((fd, data)),
        ), patch.object(
            self.server.os,
            "close",
            side_effect=lambda fd: closes.append(fd),
        ), patch.object(
            self.server,
            "_terminal_read_available",
            return_value="",
        ):
            started = self.server.terminal_support_session(
                mode="start",
                command=["cat"],
                cwd=".",
                include_output=False,
            )
            with self.assertRaises(ValueError):
                self.server.terminal_support_session(mode="poll")
            sent = self.server.terminal_support_session(
                mode="send",
                session_id=started["session_id"],
                input_text="hello",
                include_output=False,
            )
            stopped = self.server.terminal_support_session(
                mode="stop",
                session_id=started["session_id"],
                include_output=False,
            )
        self.assertEqual(started["backend"], "pty")
        self.assertEqual(writes[0][1], b"hello")
        self.assertEqual(sent["input_chars"], 5)
        self.assertIn(12, closes)
        self.assertIn(11, closes)
        self.assertFalse(stopped["running"])

        index_file = self.repo_path / self.server.REPO_INDEX_FILE
        index_file.parent.mkdir(parents=True, exist_ok=True)
        index_file.write_text(
            json.dumps(
                {
                    "generated_at": "2026-03-12T00:00:00+00:00",
                    "file_count": 2,
                    "symbol_count": 1,
                    "dependency_edge_count": 1,
                    "files": [{"path": "src/sample.py"}],
                }
            ),
            encoding="utf-8",
        )
        with patch.object(self.server, "_result_store_put", return_value="rid-index-read"):
            quick_read = self.server.repo_index_daemon(
                mode="read",
                summary_mode="quick",
                store_result=True,
            )
        self.assertEqual(quick_read["result_id"], "rid-index-read")

        with self.assertRaises(FileNotFoundError):
            self.server.repo_index_daemon(mode="refresh", path="missing")

        index_file.write_text("{bad", encoding="utf-8")
        self.write_repo_text(".secret.py", "print('skip')\n")
        self.write_repo_text("src/one.py", "print('1')\n")
        self.write_repo_text("src/two.py", "print('2')\n")
        with patch.object(self.server, "_file_sha256", side_effect=OSError("hash fail")), patch.object(
            self.server,
            "symbol_index",
            return_value=[{"name": "alpha"}],
        ), patch.object(
            self.server,
            "dependency_map",
            return_value={"edge_count": 1, "edges": [{"from": "a", "to": "b"}]},
        ), patch.object(
            self.server,
            "call_graph",
            return_value={"edge_count": 1, "edges": [{"caller": "a", "callee": "b"}]},
        ), patch.object(
            self.server,
            "_is_git_repo",
            return_value=False,
        ), patch.object(
            self.server,
            "_result_store_put",
            return_value="rid-index-refresh",
        ):
            refreshed = self.server.repo_index_daemon(
                mode="refresh",
                path=".",
                include_hashes=True,
                max_files=1,
                adaptive_limits=False,
                compress=True,
                store_result=True,
                output_profile="normal",
            )
        self.assertEqual(refreshed["result_id"], "rid-index-refresh")
        self.assertIn("files_compressed", refreshed)

        image_path = self.write_repo_text("docs/image.txt", "not really image")
        with self.assertRaises(ValueError):
            self.server.image_interpret(image_path="docs/image.txt", mode="bad")
        with self.assertRaises(FileNotFoundError):
            self.server.image_interpret(image_path="docs/missing.png", mode="caption")

        class _ImageProxy:
            @staticmethod
            def open(_path):
                return _DummyImageCtx()

        with patch.object(self.server, "Image", _ImageProxy), patch.object(
            self.server,
            "pytesseract",
            SimpleNamespace(image_to_string=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ocr fail"))),
        ), patch.object(
            self.server,
            "_image_basic_features",
            return_value={"width": 10, "height": 10, "aspect_ratio": 1.0, "mean_luma": 220},
        ):
            warned = self.server.image_interpret(image_path="docs/image.txt", mode="caption")
        self.assertIn("OCR failed", warned["warnings"][0])

        ocr_texts = [
            " ".join(["word"] * 45),
            "one two three four five six seven eight nine ten eleven twelve thirteen",
            "one two three four five six seven eight nine",
            "   ",
        ]

        def next_ocr(*_args, **_kwargs):
            return ocr_texts.pop(0)

        with patch.object(self.server, "Image", _ImageProxy), patch.object(
            self.server,
            "pytesseract",
            SimpleNamespace(image_to_string=next_ocr),
        ), patch.object(
            self.server,
            "_image_basic_features",
            side_effect=[
                {"width": 100, "height": 50, "aspect_ratio": 2.0, "mean_luma": 100},
                {"width": 100, "height": 50, "aspect_ratio": 1.8, "mean_luma": 100},
                {"width": 100, "height": 100, "aspect_ratio": 1.0, "mean_luma": 100},
                {"width": 20, "height": 20, "aspect_ratio": 1.0, "mean_luma": 240},
            ],
        ), patch.object(
            self.server,
            "local_infer",
            side_effect=RuntimeError("local fail"),
        ):
            document_scan = self.server.image_interpret(
                image_path="docs/image.txt",
                mode="ocr",
                use_local_model=True,
            )
            ui_screenshot = self.server.image_interpret(image_path="docs/image.txt", mode="classify")
            slide_like = self.server.image_interpret(image_path="docs/image.txt", mode="classify")
            ui_parse = self.server.image_interpret(image_path="docs/image.txt", mode="ui_parse")
        self.assertIn("word", document_scan["summary"])
        self.assertEqual(ui_screenshot["summary"], "ui_screenshot (0.78)")
        self.assertEqual(slide_like["summary"], "diagram_or_slide (0.68)")
        self.assertEqual(ui_parse["ui_elements"][0]["type"], "text")
        self.assertIn("local model interpret failed", document_scan["warnings"][-1])

    def test_prompt_math_sql_diagram_coding_and_flaky_branches(self):
        self.assertEqual(self.server._infer_batch_from_prompt("one\n---\ntwo"), ["one", "two"])
        self.assertEqual(self.server._infer_batch_from_prompt("1. first\n2. second"), ["first", "second"])
        self.assertEqual(self.server._infer_batch_from_prompt("a || b"), ["a", "b"])

        with self.assertRaises(ValueError):
            self.server.math_solver(mode="bad", expression="x")
        simple = self.server.math_solver(mode="simplify", expression="x + x", include_steps=False)
        self.assertEqual(simple["exact"], "2*x")
        with self.assertRaises(ValueError):
            self.server.math_solver(mode="solve", expression="")
        solved = self.server.math_solver(mode="solve", equations=["x=1"], include_steps=False)
        self.assertEqual(solved["solutions"][0]["x"], "1")
        with self.assertRaises(ValueError):
            self.server.math_solver(mode="matrix", matrix_a=None)

        with self.assertRaises(ValueError):
            self.server.sql_expert(mode="bad")
        with self.assertRaises(ValueError):
            self.server.sql_expert(mode="format", query="")
        with self.assertRaises(ValueError):
            self.server.sql_expert(mode="lint", query="")
        with self.assertRaises(ValueError):
            self.server.sql_expert(mode="nl2sql", nl_request="")
        linted = self.server.sql_expert(mode="lint", query=" update items set x = 1 order by id ")
        self.assertGreaterEqual(len(linted["issues"]), 2)
        orders = self.server.sql_expert(mode="nl2sql", nl_request="show recent orders")
        self.assertIn("orders", orders["sql_skeleton"])

        with self.assertRaises(ValueError):
            self.server.diagram_from_code(path="src", diagram_type="flowchart", max_nodes=0)
        edges = [
            {"from": "src/a.py", "to": "src/b.py"},
            {"from": "src/b.py", "to": ""},
            {"from": "src/c.py", "to": "src/d.py"},
        ]
        with patch.object(self.server, "dependency_map", return_value={"edges": edges}):
            flow = self.server.diagram_from_code(path="src", diagram_type="flowchart", max_nodes=5)
            classes = self.server.diagram_from_code(path="src", diagram_type="class", max_nodes=1)
        self.assertIn("flowchart LR", flow["mermaid"])
        self.assertIn("classDiagram", classes["mermaid"])

        with self.assertRaises(ValueError):
            self.server._coding_checks(profile="bad")
        with self.assertRaises(ValueError):
            self.server._coding_checks(timeout_seconds=0)
        with self.assertRaises(FileNotFoundError):
            self.server._coding_checks(python_executable="missing-python")

        timeout_exc = subprocess.TimeoutExpired(cmd=["pytest"], timeout=1)
        timeout_exc.stdout = "out"
        timeout_exc.stderr = "err"
        with patch.object(self.server.subprocess, "run", side_effect=timeout_exc):
            timed = self.server._coding_checks(
                profile="tests",
                target="README.md",
                timeout_seconds=5,
                python_executable="README.md",
            )
        self.assertFalse(timed["ok"])
        self.assertTrue(timed["steps"][0]["timeout"])

        with self.assertRaises(ValueError):
            self.server.flaky_test_detector(runner="bad")
        with self.assertRaises(ValueError):
            self.server.flaky_test_detector(runs=1)
        with self.assertRaises(ValueError):
            self.server.flaky_test_detector(timeout_seconds=0)

        history_path = self.repo_path / ".codebase-tooling-mcp" / "memory" / "flaky.json"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text("{bad", encoding="utf-8")
        seen_cmds = []

        def flaky_run(cmd, **kwargs):
            del kwargs
            seen_cmds.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="boom")

        with patch.object(self.server.subprocess, "run", side_effect=flaky_run):
            self.server.flaky_test_detector(
                runner="pytest",
                fail_fast=True,
                runs=2,
                target="tests",
                history_path=str(history_path),
                timeout_seconds=5,
            )
        self.assertIn("-x", seen_cmds[0])

        history_path.write_text(
            json.dumps({"schema": "flaky_test_history.v1", "updated_at": "x", "tests": []}),
            encoding="utf-8",
        )
        seen_cmds.clear()
        with patch.object(self.server.subprocess, "run", side_effect=flaky_run):
            self.server.flaky_test_detector(
                runner="unittest",
                fail_fast=True,
                runs=2,
                target="tests",
                history_path=str(history_path),
                timeout_seconds=5,
            )
        self.assertIn("-f", seen_cmds[0])

    def test_interpret_presentation_encode_required_tool_chain_and_smart_fix(self):
        with self.assertRaises(FileNotFoundError):
            self.server.interpret_presentation("missing.pptx")
        plain = self.write_repo_text("docs/plain.txt", "x")
        with self.assertRaises(ValueError):
            self.server.interpret_presentation("docs/plain.txt")

        legacy = self.write_repo_text("docs/legacy.ppt", "binary")
        slides = [
            {"index": idx, "title": f"Slide {idx}", "text": f"Body {idx}"}
            for idx in range(1, 11)
        ]
        with patch.object(
            self.server,
            "_read_ppt_legacy_text",
            return_value=(slides, {"slide_count": 10, "slides_read": 10}, ["legacy warning"]),
        ), patch.object(
            self.server,
            "local_infer",
            side_effect=RuntimeError("summary fail"),
        ):
            interpreted = self.server.interpret_presentation(
                path=str(legacy.relative_to(self.repo_path)),
                use_local_model=True,
            )
        self.assertIn("legacy warning", interpreted["warnings"][0])
        self.assertTrue(interpreted["summary"].endswith(", ..."))

        with patch.object(
            self.server,
            "_read_ppt_legacy_text",
            return_value=([{"index": 1, "title": "", "text": ""}], {"slide_count": 1, "slides_read": 1}, []),
        ):
            empty_summary = self.server.interpret_presentation(
                path=str(legacy.relative_to(self.repo_path)),
                use_local_model=False,
                output_profile="compact",
            )
        self.assertEqual(empty_summary["summary"], "No textual content extracted from presentation.")

        with self.assertRaises(ValueError):
            self.server.encode_lossless(value={}, min_symbol_length=0)
        with self.assertRaises(ValueError):
            self.server.encode_lossless(value={}, min_symbol_reuse=0)
        with self.assertRaises(ValueError):
            self.server.encode_lossless(value={}, min_blob_chars=0)
        with patch.object(self.server, "_result_store_put", return_value="rid-encode"):
            encoded = self.server.encode_lossless(
                value={"alpha_key_long": "value" * 120},
                use_symbols=False,
                use_blob_refs=False,
                store_result=True,
            )
        self.assertEqual(encoded["result_id"], "rid-encode")

        with self.assertRaises(ValueError):
            self.server.required_tool_chain(required_tools=[])
        with self.assertRaises(ValueError):
            self.server.required_tool_chain(required_tools=["a"], max_age_minutes=0)

        result_store = self.repo_path / self.server.RESULT_STORE_FILE
        result_store.parent.mkdir(parents=True, exist_ok=True)
        result_store.write_text(
            json.dumps(
                {
                    "results": {
                        "rid1": {"tool": "grep", "created_at": "not-a-date"},
                        "rid2": {"tool": "build", "created_at": "2999-01-01T00:00:00+00:00"},
                        "rid3": ["bad"],
                    }
                }
            ),
            encoding="utf-8",
        )
        chain = self.server.required_tool_chain(
            required_tools=["build", "test"],
            required_result_ids=["rid2", "missing"],
            required_artifacts=["README.md", "missing.txt"],
            require_order=False,
            max_age_minutes=10,
        )
        self.assertIn("test", chain["missing_tools"])
        self.assertIn("missing", chain["missing_result_ids"])
        self.assertIn("missing.txt", chain["missing_artifacts"])

        with self.assertRaises(ValueError):
            self.server.smart_fix_batch(findings=[{"path": "README.md"}], mode="plan")
        with self.assertRaises(ValueError):
            self.server.smart_fix_batch(findings=[], mode="plan")
        with self.assertRaises(ValueError):
            self.server.smart_fix_batch(
                findings=[{"path": "README.md", "search": "x", "replacement": "y"}],
                mode="bad",
            )

        regex_target = self.write_repo_text("docs/regex.txt", "unchanged\n")
        executed = self.server.smart_fix_batch(
            findings=[{"path": "docs/regex.txt", "search": "not-there", "replacement": "x"}],
            mode="execute",
            regex=True,
            replace_all=False,
            run_validation=True,
        )
        self.assertEqual(executed["skipped_count"], 1)
        self.assertEqual(regex_target.read_text(encoding="utf-8"), "unchanged\n")
