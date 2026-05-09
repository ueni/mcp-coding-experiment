# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.server_test_support import ServerToolsTestBase


class ServerAnalysisCoverageTest(ServerToolsTestBase):
    def test_json_query_formats_and_invalid_type(self):
        self.write_repo_text("config.json", '{"svc": {"port": 8080}}\n')
        self.write_repo_text("config.toml", '[svc]\nport = 9090\n')
        self.write_repo_text("config.yaml", 'svc:\n  port: 7070\n')

        json_out = self.server.json_query("config.json", query="svc.port", file_type="json")
        toml_out = self.server.json_query(
            "config.toml",
            query="svc.port",
            file_type="toml",
            output_profile="verbose",
        )
        yaml_out = self.server.json_query("config.yaml", query="svc.port", file_type="yaml")

        self.assertIn("8080", json_out["value_json"])
        self.assertEqual(toml_out["value"], 9090)
        self.assertEqual(toml_out["value_type"], "int")
        self.assertIn("7070", yaml_out["value_json"])

        with self.assertRaises(ValueError):
            self.server.json_query("config.json", file_type="ini")

    def test_math_solver_additional_modes(self):
        if self.server.sp is None:
            self.skipTest("sympy not installed in test runtime")
        differentiated = self.server.math_solver(mode="differentiate", expression="x**3", variable="x")
        integrated = self.server.math_solver(mode="integrate", expression="2*x", variable="x")
        matrix = self.server.math_solver(
            mode="matrix",
            matrix_a=[[1, 2], [3, 4]],
            matrix_b=[[1], [0]],
        )
        optimized = self.server.math_solver(mode="optimize", expression="x**2 - 4*x + 1", variable="x")

        self.assertEqual(differentiated["exact"], "3*x**2")
        self.assertEqual(integrated["exact"], "x**2")
        self.assertEqual(matrix["shape"], [2, 2])
        self.assertEqual(matrix["rank"], 2)
        self.assertIn("2", optimized["critical_points"])

    def test_sql_expert_lint_and_nl2sql(self):
        linted = self.server.sql_expert(
            mode="lint",
            query="SELECT * FROM users ORDER BY created_at DESC",
        )
        nl2sql = self.server.sql_expert(mode="nl2sql", nl_request="show recent user records")

        self.assertEqual(linted["schema"], "sql_expert.v1")
        self.assertGreaterEqual(len(linted["issues"]), 1)
        self.assertIn("users", nl2sql["sql_skeleton"])

    def test_diagram_from_code_class_sequence_and_verbose_call_edges(self):
        dep = {"edges": [{"from": "src/sample.py", "to": "src/utils.py"}]}
        calls = {"edges": [{"path": "src/sample.py", "caller": "alpha", "callee": "beta"}]}
        with patch.object(self.server, "dependency_map", return_value=dep), patch.object(
            self.server,
            "call_graph",
            return_value=calls,
        ):
            class_diagram = self.server.diagram_from_code(
                path="src",
                diagram_type="class",
                output_profile="verbose",
                include_call_edges=True,
            )
            sequence = self.server.diagram_from_code(
                path="src",
                diagram_type="sequence",
                output_profile="compact",
            )

        self.assertIn("classDiagram", class_diagram["mermaid"])
        self.assertEqual(class_diagram["call_edges"], calls["edges"])
        self.assertIn("sequenceDiagram", sequence["mermaid"])
        with self.assertRaises(ValueError):
            self.server.diagram_from_code(path="src", diagram_type="invalid")

    def test_tree_sitter_core_parse_search_and_validation(self):
        parsed = self.server.tree_sitter_core(
            mode="parse",
            path="src",
            node_types=["FunctionDef"],
            output_profile="compact",
            compress=True,
            store_result=True,
        )
        searched = self.server.tree_sitter_core(
            mode="search",
            path="src",
            node_types=["FunctionDef"],
            text_pattern="alpha",
            summary_mode="quick",
        )

        self.assertEqual(parsed["schema"], "tree_sitter_core.v1")
        self.assertIn("files_compressed", parsed)
        self.assertIn("result_id", parsed)
        self.assertEqual(searched["schema"], "tree_sitter_core.quick.v1")
        self.assertGreaterEqual(searched["file_count"], 1)

        with self.assertRaises(ValueError):
            self.server.tree_sitter_core(mode="parse", path="src", max_files=0)
        with self.assertRaises(ValueError):
            self.server.tree_sitter_core(mode="parse", path="src", summary_mode="bad")

    def test_repo_index_daemon_variants_and_incremental_reuse(self):
        refreshed = self.server.repo_index_daemon(
            mode="refresh",
            path=".",
            output_profile="verbose",
            include_hashes=True,
            store_result=True,
            incremental=False,
        )
        compact = self.server.repo_index_daemon(
            mode="read",
            output_profile="compact",
            store_result=True,
        )
        quick = self.server.repo_index_daemon(
            mode="read",
            output_profile="normal",
            summary_mode="quick",
        )
        verbose = self.server.repo_index_daemon(
            mode="read",
            output_profile="normal",
            compress=True,
            store_result=True,
        )
        queried = self.server.repo_index_daemon(
            mode="query",
            query="files",
            fields=["path"],
            limit=1,
            output_profile="compact",
        )

        self.assertEqual(refreshed["schema"], "repo_index_daemon.refresh.v1")
        self.assertIn("result_id", refreshed)
        self.assertEqual(compact["schema"], "repo_index_daemon.compact.v1")
        self.assertIn("result_id", compact)
        self.assertEqual(quick["schema"], "repo_index_daemon.quick.v1")
        self.assertIn("files_compressed", verbose)
        self.assertIn("value_json", queried)

        self.write_repo_text("docs/a.md", "updated docs\n")
        incremented = self.server.repo_index_daemon(
            mode="refresh",
            path=".",
            output_profile="normal",
            incremental=True,
        )
        self.assertGreaterEqual(incremented["changed_paths_count"], 1)

    def test_tool_assisted_infer_helpers_and_parallel_fallback(self):
        self.write_repo_text(
            ".gitignore",
            "# codebase-tooling-mcp generated\n/.codebase-tooling-mcp/\n/.continue/\n# stop\nignored\n",
        )
        self.write_repo_text(
            ".devcontainer/devcontainer.json",
            '{"mounts": ["source=${localEnv:HOME}/.codex,target=/workspace/.codex,type=bind"]}\n',
        )
        self.write_repo_text(
            "settings.yaml",
            "OLLAMA_HOST: http://127.0.0.1:11434\nCONTINUE_OLLAMA_MODEL: qwen\nOTHER: value\n",
        )

        ignore_text = self.server._tool_assisted_infer(
            "From .gitignore, list the codebase-tooling generated ignore entries.",
            max_tokens=64,
        )
        env_text = self.server._tool_assisted_infer(
            "From settings.yaml, list the ollama environment keys.",
            max_tokens=64,
        )
        mount_text = self.server._tool_assisted_infer(
            "In .devcontainer/devcontainer.json, what is the codex mount target?",
            max_tokens=64,
        )
        summary_text = self.server._tool_assisted_infer(
            "Summarize README.md in 2 concise sentences focused on behavior.",
            max_tokens=96,
        )

        self.assertIn("/.codebase-tooling-mcp/", ignore_text)
        self.assertIn("OLLAMA_HOST", env_text)
        self.assertIn("/workspace/.codex", mount_text)
        self.assertLessEqual(len(summary_text), 420)

        tool_backed = self.server._parallel_infer_one(
            prompt="From .gitignore, list the codebase-tooling generated ignore entries.",
            task="general",
            backend="fallback",
            model="",
            max_tokens=64,
            temperature=0.1,
            system="",
            output_profile="compact",
            store_result=True,
        )
        self.assertEqual(tool_backed["backend"], "tool_fallback")
        self.assertIn("result_id", tool_backed)

        with patch.object(self.server, "local_infer", return_value={"schema": "local_infer.v1", "ok": True}):
            delegated = self.server._parallel_infer_one(
                prompt="No file path here",
                task="general",
                backend="fallback",
                model="",
                max_tokens=32,
                temperature=0.1,
                system="",
                output_profile="compact",
                store_result=False,
            )
        self.assertTrue(delegated["ok"])

    def test_local_infer_autocomplete_and_translation_modes(self):
        with patch.object(self.server, "_local_infer_via_endpoint", side_effect=RuntimeError("offline")):
            infer = self.server.local_infer(
                prompt="explain sample.py",
                backend="endpoint",
                output_profile="compact",
                store_result=True,
            )
        self.assertEqual(infer["backend"], "fallback")
        self.assertIn("result_id", infer)

        with patch.object(self.server, "_local_infer_via_endpoint", side_effect=RuntimeError("offline")):
            completion = self.server.autocomplete(
                prefix="def handler():",
                backend="endpoint",
                stop=["\n\n"],
                output_profile="compact",
                store_result=True,
            )
        self.assertEqual(completion["backend"], "fallback")
        self.assertIn("result_id", completion)
        self.assertTrue(completion["ok"])

        with patch.object(
            self.server,
            "local_infer",
            return_value={"schema": "local_infer.compact.v1", "backend": "fallback", "output": "hallo"},
        ):
            translated = self.server.translation_small(
                text="hello",
                source_lang="en",
                target_lang="de",
                mode="local_infer",
            )
        self.assertEqual(translated["translated"], "hallo")
        self.assertEqual(translated["backend"], "fallback")

    def test_image_helpers_vision_ocr_and_image_interpret(self):
        if self.server.Image is None:
            self.skipTest("Pillow not installed in test runtime")

        image_path = self.repo_path / "docs" / "screen.png"
        img = self.server.Image.new("RGB", (20, 10), color=(255, 255, 255))
        img.save(image_path)

        fake_ocr = MagicMock(image_to_string=MagicMock(return_value="Title\nPress Start\n"))
        with patch.object(self.server, "pytesseract", fake_ocr):
            parsed = self.server.vision_ocr_parser(image_path="docs/screen.png", language="eng")
            with patch.object(
                self.server,
                "local_infer",
                return_value={"schema": "local_infer.compact.v1", "backend": "fallback", "output": "ui summary"},
            ):
                interpreted = self.server.image_interpret(
                    image_path="docs/screen.png",
                    mode="ui_parse",
                    use_local_model=True,
                    output_profile="compact",
                )
            classified = self.server.image_interpret(
                image_path="docs/screen.png",
                mode="classify",
                use_local_model=False,
                output_profile="normal",
            )

        features = self.server._image_basic_features(image_path)
        self.assertEqual(parsed["schema"], "vision_ocr_parser.v1")
        self.assertEqual(features["width"], 20)
        self.assertEqual(features["height"], 10)
        self.assertTrue(interpreted["used_local_model"])
        self.assertGreaterEqual(len(interpreted["ui_elements"]), 1)
        self.assertIn("(", classified["summary"])

    def test_helper_extractors_build_snippet_ports_and_counts(self):
        prompt_paths = self.server._extract_prompt_file_paths(
            "Read README.md and src/sample.py before changing docs/a.md.",
            max_paths=3,
        )
        ignores = self.server._extract_codebase_tooling_generated_ignores(
            "# codebase-tooling-mcp generated\n/.codebase-tooling-mcp/\n/.continue/\n# next section\nignored\n"
        )
        env_keys = self.server._extract_env_keys(
            "OLLAMA_HOST: http://127.0.0.1\nCONTINUE_OLLAMA_MODEL: qwen\nDEBUG: 1\n",
            prefixes=("OLLAMA_", "CONTINUE_"),
        )
        compacted = self.server._compact_sentences(
            "One sentence. Two sentence! Three sentence?",
            max_sentences=2,
            max_chars=50,
        )
        snippet = self.server._build_snippet(self.repo_path / "src" / "sample.py", 1, 2, context_after=1)
        ports = self.server._list_listening_ports()
        count_none = self.server._count_processes_with_tokens()
        count_python = self.server._count_processes_with_tokens("python")

        self.assertIn("README.md", prompt_paths)
        self.assertEqual(ignores, ["/.codebase-tooling-mcp/", "/.continue/"])
        self.assertEqual(env_keys, ["OLLAMA_HOST", "CONTINUE_OLLAMA_MODEL"])
        self.assertIn("One sentence.", compacted)
        self.assertEqual(snippet["start_line"], 1)
        self.assertIn("def alpha", snippet["content"])
        self.assertIsInstance(ports, set)
        self.assertEqual(count_none, 0)
        self.assertGreaterEqual(count_python, 0)

        with self.assertRaises(ValueError):
            self.server._build_snippet(self.repo_path / "src" / "sample.py", 0, 1)
        with self.assertRaises(ValueError):
            self.server._build_snippet(self.repo_path / "src" / "sample.py", 2, 1)

    def test_summarize_diff_risk_scoring_self_check_pipeline_and_release_readiness(self):
        sample = self.repo_path / "src" / "sample.py"
        sample.write_text(sample.read_text(encoding="utf-8") + "\n# TODO: follow up\n", encoding="utf-8")
        summary = self.server.summarize_diff(include_patch=True, patch_unified=1, output_profile="normal")
        risk = self.server.risk_scoring()
        self.assertGreaterEqual(summary["file_count"], 1)
        self.assertIn("patch", summary)
        self.assertIn("src/sample.py", summary["files"][0]["path"])
        self.assertIn(risk["risk_level"], {"low", "medium", "high"})

        diff_out = SimpleNamespace(stdout="src/sample.py\nREADME.md\n")
        compile_fail = subprocess.CompletedProcess(
            args=[sys.executable, "-m", "py_compile"],
            returncode=1,
            stdout="",
            stderr="SyntaxError",
        )
        with patch.object(self.server, "_require_git_repo", return_value=None), patch.object(
            self.server,
            "_git",
            return_value=diff_out,
        ), patch.object(
            self.server.subprocess,
            "run",
            return_value=compile_fail,
        ), patch.object(
            self.server,
            "risk_scoring",
            return_value={"risk_level": "high", "risk_score": 8},
        ), patch.object(
            self.server,
            "doc_sync_check",
            return_value={"needs_docs_update": True},
        ), patch.object(
            self.server,
            "impact_tests",
            return_value={"tests": ["tests/test_sample.py"]},
        ), patch.object(
            self.server,
            "command_runner",
            return_value={"ok": False, "exit_code": 1, "command": ["pytest", "-q"], "stderr": "failed", "timeout": False},
        ):
            full_pipeline = self.server.self_check_pipeline(
                base_ref="HEAD~1",
                head_ref="HEAD",
                snapshot_path=".codebase-tooling-mcp/missing-snapshot.json",
                summary_mode="full",
            )
        self.assertFalse(full_pipeline["ok"])
        self.assertEqual(full_pipeline["checks"]["compile"]["error_count"], 1)
        self.assertTrue(full_pipeline["checks"]["api"]["skipped"])

        self.write_repo_text(".codebase-tooling-mcp/api_snapshot.json", json.dumps({"symbols": []}))
        with patch.object(self.server, "_require_git_repo", return_value=None), patch.object(
            self.server,
            "_git",
            return_value=diff_out,
        ), patch.object(
            self.server,
            "risk_scoring",
            return_value={"risk_level": "low", "risk_score": 1},
        ), patch.object(
            self.server,
            "doc_sync_check",
            return_value={"needs_docs_update": False},
        ), patch.object(
            self.server,
            "api_surface_snapshot",
            return_value={"removed_count": 0, "added_count": 1},
        ), patch.object(
            self.server,
            "impact_tests",
            side_effect=[{"tests": ["tests/test_sample.py"]}, {"tests": ["tests/test_sample.py"]}],
        ), patch.object(
            self.server,
            "command_runner",
            return_value={"ok": True, "exit_code": 0, "command": ["pytest", "-q"], "stderr": "", "timeout": False},
        ):
            quick_pipeline = self.server.self_check_pipeline(
                base_ref="HEAD~1",
                head_ref="HEAD",
                run_compile_check=False,
                snapshot_path=".codebase-tooling-mcp/api_snapshot.json",
                summary_mode="quick",
            )
        self.assertEqual(quick_pipeline["schema"], "self_check_pipeline.quick.v1")
        self.assertTrue(quick_pipeline["ok"])

        with patch.object(
            self.server,
            "_require_git_repo",
            return_value=None,
        ), patch.object(
            self.server,
            "self_test",
            return_value={"ok": False, "exit_code": 1},
        ), patch.object(
            self.server,
            "doc_sync_check",
            return_value={"needs_docs_update": True},
        ), patch.object(
            self.server,
            "_git",
            return_value=SimpleNamespace(stdout="+ secret = 'x'\n"),
        ), patch.object(
            self.server,
            "security_triage",
            return_value={"finding_count": 2},
        ), patch.object(
            self.server,
            "license_monitor",
            side_effect=RuntimeError("reuse missing"),
        ), patch.object(
            self.server,
            "risk_scoring",
            return_value={"risk_level": "high", "risk_score": 9},
        ), patch.object(
            self.server,
            "impact_tests",
            return_value={"tests": ["tests/test_sample.py"]},
        ):
            full_release = self.server.release_readiness(summary_mode="full")
        self.assertEqual(full_release["schema"], "release_readiness.v1")
        self.assertFalse(full_release["ok"])
        self.assertEqual(full_release["checks"]["license"]["ok"], False)
        self.assertEqual(full_release["checks"]["security"]["finding_count"], 2)

        with self.assertRaises(ValueError):
            self.server.self_check_pipeline(summary_mode="bad")
        with self.assertRaises(ValueError):
            self.server.release_readiness(summary_mode="bad")
