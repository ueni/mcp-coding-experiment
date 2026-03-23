# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class ServerCoverageBurnDownTest(ServerToolsTestBase):
    def test_iter_candidate_files_and_find_paths_variants(self):
        self.write_repo_text("src/.hidden.py", "VALUE = 1\n")
        self.write_repo_text("src/nested/deep.py", "VALUE = 2\n")
        self.write_repo_text(".hidden/secret.txt", "secret\n")

        file_only = list(self.server._iter_candidate_files(self.repo_path / "README.md", recursive=False))
        recursive = list(self.server._iter_candidate_files(self.repo_path / "src", recursive=True))
        recursive_hidden = list(
            self.server._iter_candidate_files(self.repo_path / "src", recursive=True, include_hidden=True)
        )

        self.assertEqual(file_only, [self.repo_path / "README.md"])
        self.assertTrue(any(p.name == "deep.py" for p in recursive))
        self.assertFalse(any(p.name == ".hidden.py" for p in recursive))
        self.assertTrue(any(p.name == ".hidden.py" for p in recursive_hidden))

        found_file = self.server.find_paths(path="README.md", recursive=False)
        found_dirs = self.server.find_paths(
            path=".",
            recursive=False,
            file_type="dir",
            output_profile="normal",
        )
        found_nested = self.server.find_paths(
            path="src",
            recursive=True,
            include_hidden=True,
            file_type="file",
            max_depth=1,
            output_profile="normal",
        )
        self.assertEqual(found_file, ["README.md"])
        self.assertTrue(any(item.endswith("src/") for item in found_dirs))
        self.assertIn("src/sample.py", found_nested)
        self.assertIn("src/.hidden.py", found_nested)
        self.assertNotIn("src/nested/deep.py", found_nested)

    def test_grep_and_replace_in_files_variants(self):
        self.write_repo_text("docs/notes.txt", "Alpha beta\nalpha gamma\n")
        self.write_repo_text(".hidden/match.txt", "alpha hidden\n")

        matches = self.server.grep(
            pattern="alpha",
            path=".",
            case_insensitive=True,
            output_profile="normal",
            fields=["path", "line", "match"],
            include_hidden=False,
            offset=0,
            limit=10,
        )
        quick = self.server.grep(
            pattern="alpha",
            path=".",
            case_insensitive=True,
            include_hidden=True,
            summary_mode="quick",
            store_result=True,
        )
        compressed = self.server.grep(
            pattern="alpha",
            path=".",
            case_insensitive=True,
            include_hidden=True,
            compress=True,
        )

        self.assertGreaterEqual(len(matches), 2)
        self.assertTrue(all("path" in row for row in matches))
        self.assertEqual(quick[0]["schema"], "grep.quick.v1")
        self.assertIn("result_id", quick[0])
        self.assertIn("rows", compressed[0])

        dry = self.server.replace_in_files(
            path="docs",
            pattern="alpha",
            replacement="omega",
            recursive=True,
            regex=True,
            case_insensitive=True,
            dry_run=True,
            max_files=10,
            max_replacements=10,
        )
        self.assertEqual(dry["total_replacements"], 2)
        self.assertEqual(dry["files_changed_count"], 1)
        self.assertIn("Alpha beta", (self.repo_path / "docs" / "notes.txt").read_text(encoding="utf-8"))

        changed = self.server.replace_in_files(
            path=".",
            pattern="alpha",
            replacement="omega",
            recursive=True,
            regex=False,
            include_hidden=True,
            dry_run=False,
            max_files=10,
            max_replacements=1,
        )
        self.assertEqual(changed["total_replacements"], 1)
        self.assertTrue(changed["replacements_limit_reached"])
        self.assertIn("omega", (self.repo_path / "docs" / "notes.txt").read_text(encoding="utf-8"))

        with self.assertRaises(ValueError):
            self.server.grep(pattern="[", path=".")

    def test_dependency_map_and_call_graph_variants(self):
        self.write_repo_text(
            "src/extra.py",
            "import os\nfrom src.sample import alpha\n\n\ndef gamma(v):\n    return alpha(v)\n",
        )
        self.write_repo_text(
            "src/calls.py",
            "from src.sample import alpha, beta\n\n\ndef chain(v):\n    return beta(alpha(v))\n",
        )

        dep = self.server.dependency_map(
            path="src",
            recursive=True,
            include_stdlib=True,
            output_profile="verbose",
            compress=True,
            store_result=True,
        )
        dep_quick = self.server.dependency_map(
            path="src",
            recursive=True,
            summary_mode="quick",
            output_profile="normal",
        )
        cg = self.server.call_graph(
            path="src",
            recursive=True,
            output_profile="compact",
            compress=True,
            store_result=True,
        )
        cg_quick = self.server.call_graph(
            path="src",
            recursive=True,
            output_profile="verbose",
            summary_mode="quick",
        )

        self.assertEqual(dep["schema"], "dependency_map.v1")
        self.assertIn("edges_compressed", dep)
        self.assertIn("result_id", dep)
        self.assertGreaterEqual(len(dep["unresolved_imports"]), 1)
        self.assertEqual(dep_quick["schema"], "dependency_map.quick.v1")
        self.assertEqual(cg["schema"], "call_graph.compact.v1")
        self.assertIn("edges_compressed", cg)
        self.assertIn("result_id", cg)
        self.assertEqual(cg_quick["schema"], "call_graph.quick.v1")

        with self.assertRaises(ValueError):
            self.server.call_graph(path="src", max_edges=0)
        with self.assertRaises(ValueError):
            self.server.dependency_map(path="src", summary_mode="bad")

    def test_vscode_task_run_and_build_log_proposals(self):
        self.write_repo_text(
            ".vscode/tasks.json",
            json.dumps(
                {
                    "version": "2.0.0",
                    "tasks": [
                        {
                            "label": "Docker: build",
                            "type": "shell",
                            "command": "docker build .",
                            "options": {"cwd": "${workspaceFolder}/docs"},
                        }
                    ],
                }
            ),
        )
        failed = subprocess.CompletedProcess(
            args=["docker", "build", "."],
            returncode=1,
            stdout="",
            stderr="error getting credentials",
        )
        timed = subprocess.TimeoutExpired(
            cmd=["docker", "build", "."],
            timeout=5,
            output="no space left on device",
            stderr="",
        )
        with patch.object(self.server, "_validate_build_task_command", return_value=None), patch.object(
            self.server.subprocess,
            "run",
            return_value=failed,
        ):
            failed_out = self.server.vscode_task_run(
                label="Docker: build",
                tasks_path=".vscode/tasks.json",
                control_profile="build",
            )
        self.assertFalse(failed_out["ok"])
        self.assertEqual(failed_out["cwd"], "docs")
        self.assertGreaterEqual(len(failed_out["proposals"]), 1)

        with patch.object(self.server, "_validate_build_task_command", return_value=None), patch.object(
            self.server.subprocess,
            "run",
            side_effect=timed,
        ):
            timeout_out = self.server.vscode_task_run(
                label="Docker: build",
                tasks_path=".vscode/tasks.json",
                control_profile="build",
                timeout_seconds=5,
            )
        self.assertTrue(timeout_out["timeout"])
        self.assertGreaterEqual(len(timeout_out["proposals"]), 1)
        self.assertIn("disk space", timeout_out["proposals"][0]["issue"].lower())

        with self.assertRaises(ValueError):
            self.server.vscode_task_run(label="missing", tasks_path=".vscode/tasks.json")
        with self.assertRaises(ValueError):
            self.server.vscode_task_run(label="Docker: build", tasks_path=".vscode/tasks.json", timeout_seconds=0)

        proposals = self.server._build_log_proposals("pull access denied", "tls handshake timeout")
        self.assertGreaterEqual(len(proposals), 2)
        self.assertIn("Image pull denied", proposals[0]["issue"])

    def test_validate_safe_command_and_build_task_command(self):
        with self.assertRaises(ValueError):
            self.server._validate_safe_command([])
        with self.assertRaises(ValueError):
            self.server._validate_safe_command(["git"])
        with self.assertRaises(ValueError):
            self.server._validate_safe_command(["git", "push"])
        with self.assertRaises(ValueError):
            self.server._validate_safe_command(["sed", "-i", "s/a/b/", "x.txt"])
        with self.assertRaises(ValueError):
            self.server._validate_safe_command(["find", ".", "-delete"])
        with self.assertRaises(ValueError):
            self.server._validate_safe_command(["awk", "system('rm -rf /')", "file.txt"])
        self.server._validate_safe_command(["git", "status"])

        with self.assertRaises(ValueError):
            self.server._validate_build_task_command(["python", "build.py"])
        with self.assertRaises(ValueError):
            self.server._validate_build_task_command(["docker", "run", "app"], control_profile="build")
        self.server._validate_build_task_command(
            ["docker", "compose", "-f", "compose.yml", "build"],
            control_profile="build",
        )

    def test_delta_helpers_delete_path_and_git_wrappers(self):
        root = {"items": [{"name": "a"}]}
        updated = self.server._delta_set_value(root, ["items", "1", "name"], "b")
        removed = self.server._delta_remove_value(updated, ["items", "0"])
        self.assertEqual(removed["items"][0]["name"], "b")
        self.assertEqual(self.server._delta_remove_value({"a": 1}, ["missing"]), {"a": 1})
        with self.assertRaises(ValueError):
            self.server._delta_set_value(1, ["a"], 2)

        self.write_repo_text("tmp/file.txt", "x\n")
        self.write_repo_text("tmp/dir/file.txt", "y\n")
        deleted_file = self.server.delete_path("tmp/file.txt")
        self.assertEqual(deleted_file["deleted"], "tmp/file.txt")
        deleted_dir = self.server.delete_path("tmp/dir", recursive=True)
        self.assertEqual(deleted_dir["deleted"], "tmp/dir")
        with self.assertRaises(FileNotFoundError):
            self.server.delete_path("tmp/missing.txt")

        sample = self.repo_path / "src" / "sample.py"
        sample.write_text(sample.read_text(encoding="utf-8") + "\n# change\n", encoding="utf-8")
        diff_text = self.server.git_diff(pathspec="src/sample.py")
        self.assertIn("# change", diff_text)

        commit_out = self.server.git_commit(message="chore: empty commit", allow_empty=True)
        self.assertIn("chore: empty commit", commit_out["summary"])
        branch_out = self.server.git_create_branch(name="feat/coverage", checkout=False)
        self.assertEqual(branch_out["branch"], "feat/coverage")
        checked = self.server.git_checkout(ref="feat/coverage")
        self.assertEqual(checked["current_branch"], "feat/coverage")

        completed = type("Proc", (), {"stdout": "ok\n", "stderr": ""})
        with patch.object(self.server, "_git", return_value=completed):
            self.assertIn("ok", self.server.git_fetch(remote="origin", prune=True))
            self.assertIn("ok", self.server.git_pull(remote="origin", branch="main", rebase=True))
            self.assertIn("ok", self.server.git_push(remote="origin", branch="main", set_upstream=True))

    def test_commit_lint_tag_artifact_memory_and_spec_to_tests(self):
        bad = self.server.commit_lint_tag(
            message="release pipeline update",
            include_diff_hints=False,
        )
        self.assertFalse(bad["lint_ok"])
        self.assertGreaterEqual(len(bad["suggestions"]), 1)

        good = self.server.commit_lint_tag(
            message="fix(auth)!: rotate secret token cache",
            include_diff_hints=False,
        )
        self.assertTrue(good["lint_ok"])
        self.assertIn("security", good["tags"])
        self.assertIn("breaking", good["tags"])

        report_path = self.repo_path / "docs" / "artifact.txt"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("alpha\n", encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            self.server.artifact_memory_index(mode="read")

        refreshed = self.server.artifact_memory_index(mode="refresh", path="docs")
        read = self.server.artifact_memory_index(mode="read")
        queried = self.server.artifact_memory_index(mode="query", query="artifact")
        added = self.server.artifact_memory_index(mode="add", query="docs/a.md")
        self.assertEqual(refreshed["mode"], "refresh")
        self.assertGreaterEqual(read["count"], 1)
        self.assertEqual(queried["count"], 1)
        self.assertEqual(added["added"], "docs/a.md")

        generated = self.server.spec_to_tests(
            spec_text="- system must authenticate users\n- API should reject invalid tokens\n",
            framework="unittest",
            mode="generate",
        )
        written = self.server.spec_to_tests(
            spec_text="- service must be fast\n",
            framework="pytest",
            mode="write",
            output_path="tests/generated_spec_test.py",
        )
        self.assertIn("class SpecTests", generated["test_code"])
        self.assertTrue((self.repo_path / "tests" / "generated_spec_test.py").is_file())
        self.assertEqual(written["output_path"], "tests/generated_spec_test.py")
        with self.assertRaises(ValueError):
            self.server.spec_to_tests(spec_text="x", framework="nose")
        with self.assertRaises(ValueError):
            self.server.spec_to_tests(spec_text="x", mode="append")

    def test_license_monitor_browse_web_read_document_and_lab_wrappers(self):
        def fake_run_reuse(args, timeout_seconds=120):
            if args[0] == "download":
                lic = self.repo_path / "LICENSES" / f"{args[1]}.txt"
                lic.parent.mkdir(parents=True, exist_ok=True)
                lic.write_text("MIT text\n", encoding="utf-8")
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
                "command": ["reuse", *args],
            }

        with patch.object(
            self.server,
            "_collect_missing_spdx_headers",
            side_effect=[["src/sample.py"], []],
        ), patch.object(
            self.server,
            "_collect_spdx_license_ids",
            return_value=["MIT"],
        ), patch.object(
            self.server,
            "_require_reuse_cli",
            return_value=None,
        ), patch.object(
            self.server,
            "_run_reuse",
            side_effect=fake_run_reuse,
        ):
            license_out = self.server.license_monitor(
                auto_fix_headers=True,
                download_missing_licenses=True,
                run_reuse_lint=True,
                generate_spdx=True,
            )
        self.assertTrue(license_out["ok"])
        self.assertIn("annotated_missing_headers:1", license_out["actions"])
        self.assertIn("downloaded_license_texts:1", license_out["actions"])
        self.assertTrue((self.repo_path / ".codebase-tooling-mcp" / "reports" / "REUSE_LINT.txt").is_file())
        with self.assertRaises(ValueError):
            self.server.license_monitor(max_missing_files=0)

        class FakeResponse:
            def __init__(self):
                self.status = 200
                self.url = "https://example.com/final"
                self.headers = {"Content-Type": "text/html; charset=utf-8"}

            def read(self, _size):
                return b"<html><body><h1>Title</h1><p>Hello world</p></body></html>"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(self.server, "_urlopen_with_host_certs", return_value=FakeResponse()):
            web = self.server.browse_web(
                url="https://example.com",
                max_bytes=20,
                max_chars=10,
                output_profile="compact",
            )
        self.assertEqual(web["schema"], "browse_web.compact.v1")
        self.assertTrue(web["truncated"])

        with patch.object(
            self.server,
            "_urlopen_with_host_certs",
            side_effect=urllib.error.URLError("offline"),
        ):
            with self.assertRaises(RuntimeError):
                self.server.browse_web(url="https://example.com")

        for name in ("doc.pdf", "doc.doc", "doc.docx", "sheet.xls", "notes.odt"):
            self.write_repo_text(name, "placeholder\n")
        with patch.object(self.server, "_read_pdf_text", return_value=("pdf text", {"pages_read": 1})), patch.object(
            self.server,
            "_read_doc_text",
            return_value=("doc text", {"backend": "fallback"}),
        ), patch.object(
            self.server,
            "_read_docx_text",
            return_value=("docx text", {"paragraphs": 1}),
        ), patch.object(
            self.server,
            "_read_xls_text",
            return_value=("xls text", {"sheets_read": 1}),
        ), patch.object(
            self.server,
            "_read_opendoc_text",
            return_value=("odt text", {"entries_read": 1}),
        ):
            pdf = self.server.read_document("doc.pdf", output_profile="compact")
            doc = self.server.read_document("doc.doc", output_profile="normal")
            docx = self.server.read_document("doc.docx", output_profile="normal")
            xls = self.server.read_document("sheet.xls", output_profile="normal")
            odt = self.server.read_document("notes.odt", output_profile="normal")
        self.assertEqual(pdf["schema"], "read_document.compact.v1")
        self.assertEqual(doc["metadata"]["backend"], "fallback")
        self.assertEqual(docx["metadata"]["paragraphs"], 1)
        self.assertEqual(xls["metadata"]["sheets_read"], 1)
        self.assertEqual(odt["metadata"]["entries_read"], 1)

        labs_dir = self.repo_path / "labs"
        labs_dir.mkdir(parents=True, exist_ok=True)
        (labs_dir / "fake.py").write_text("print('ok')\n", encoding="utf-8")
        with patch.object(self.server, "_require_git_repo", return_value=None), patch.object(
            self.server,
            "LABS_DIR",
            Path("labs"),
        ), patch.object(
            self.server,
            "_list_report_files",
            return_value=[".codebase-tooling-mcp/reports/ONE.txt"],
        ), patch.object(
            self.server.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["python"], returncode=0, stdout="ok", stderr=""),
        ):
            lab = self.server._run_lab_script("fake.py", ["--flag"])
        self.assertTrue(lab["ok"])
        self.assertEqual(lab["reports"], [".codebase-tooling-mcp/reports/ONE.txt"])

        with patch.object(self.server, "_require_git_repo", return_value=None), patch.object(
            self.server,
            "LABS_DIR",
            Path("labs"),
        ), patch.object(
            self.server,
            "_list_report_files",
            return_value=[],
        ), patch.object(
            self.server.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["python"], returncode=1, stdout="", stderr="boom"),
        ):
            with self.assertRaises(RuntimeError):
                self.server._run_lab_script("fake.py", ["--flag"])

        self.write_repo_text(".config/labs/release_rehearsal.json", "{}\n")
        self.write_repo_text(".config/labs/refactor_tournament.json", "{}\n")
        self.write_repo_text(".config/labs/policy_gatekeeper.json", "{}\n")
        self.write_repo_text(".config/labs/branch_swarm_lab.json", "{}\n")
        with patch.object(self.server, "_run_lab_script", return_value={"ok": True, "args": []}) as run_lab:
            self.server.lab_release_rehearsal(allow_dirty=True, keep_branch=True)
            self.server.lab_refactor_tournament(allow_dirty=True, keep_branches=True)
            self.server.lab_policy_gatekeeper(changed_ref="HEAD", report_path=".codebase-tooling-mcp/reports/POLICY.md")
            self.server.lab_branch_swarm(allow_dirty=True, keep_branches=True)
        self.assertEqual(run_lab.call_count, 4)

    def test_collect_python_symbols_top_level(self):
        source = (
            "class PublicClass:\n    pass\n\n"
            "def public_fn():\n    return 1\n\n"
            "async def public_async():\n    return 2\n\n"
            "def _private():\n    return 3\n"
        )
        public = self.server._collect_python_symbols_top_level(source, "src/demo.py")
        private = self.server._collect_python_symbols_top_level(
            source,
            "src/demo.py",
            include_private=True,
        )
        invalid = self.server._collect_python_symbols_top_level("def broken(:\n", "src/demo.py")
        self.assertEqual([row["kind"] for row in public], ["class", "function", "async_function"])
        self.assertEqual(len(private), 4)
        self.assertEqual(invalid, [])
