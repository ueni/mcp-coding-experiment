# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
from pathlib import Path
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class ServerCoverageLastMileTest(ServerToolsTestBase):
    def test_ssl_cache_limit_and_list_files_branches(self):
        cert_path = self.write_repo_text("certs/test-ca.pem", "CERT\n")

        with patch.dict(self.server.os.environ, {"SSL_CERT_FILE": ""}, clear=False), patch.object(
            self.server,
            "HOST_CA_CERT_FILE",
            "",
        ), patch.object(
            self.server.ssl,
            "create_default_context",
            return_value="default",
        ) as create_ctx:
            self.assertIsNone(self.server._ssl_context_for_url("http://example.com"))
            self.assertEqual(self.server._ssl_context_for_url("https://example.com"), "default")
        create_ctx.assert_called_once_with()

        with patch.dict(self.server.os.environ, {"SSL_CERT_FILE": str(cert_path)}, clear=False), patch.object(
            self.server.ssl,
            "create_default_context",
            return_value="with-ca",
        ) as create_ctx:
            self.assertEqual(self.server._ssl_context_for_url("https://example.com"), "with-ca")
        create_ctx.assert_called_once_with(cafile=str(cert_path))

        cache_file = self.repo_path / ".build" / "cache" / "tool_cache.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"entries": ["bad"]}), encoding="utf-8")
        self.assertEqual(self.server._cache_clear()["removed_entries"], 0)

        cache_file.write_text(
            json.dumps({"entries": {"grep": {"a": {"value": 1}, "b": {"value": 2}}, "ast": {"c": {"value": 3}}}}),
            encoding="utf-8",
        )
        cleared_tool = self.server._cache_clear("grep")
        self.assertEqual(cleared_tool["removed_entries"], 2)
        cleared_all = self.server._cache_clear()
        self.assertEqual(cleared_all["removed_entries"], 1)

        with self.assertRaises(ValueError):
            self.server._adaptive_limit(0)
        with patch.object(self.server, "_token_budget_load", return_value={"max_output_chars": "bad"}):
            self.assertEqual(self.server._adaptive_limit(999, soft_cap=100), 100)
        with patch.object(self.server, "_token_budget_load", return_value={"max_output_chars": 100000}):
            self.assertEqual(self.server._adaptive_limit(999, soft_cap=100), 75)
        with patch.object(self.server, "_token_budget_load", return_value={"max_output_chars": 50000}):
            self.assertEqual(self.server._adaptive_limit(999, soft_cap=100), 50)
        with patch.object(self.server, "_token_budget_load", return_value={"max_output_chars": 25000}):
            self.assertEqual(self.server._adaptive_limit(999, soft_cap=100), 35)

        hidden = self.write_repo_text(".hidden", "x\n")
        with self.assertRaises(ValueError):
            self.server.list_files(max_entries=0)
        with self.assertRaises(FileNotFoundError):
            self.server.list_files(path="missing")
        self.assertEqual(self.server.list_files(path=".hidden", include_hidden=False), [])
        self.assertEqual(self.server.list_files(path=".hidden", include_hidden=True), [str(hidden.relative_to(self.repo_path))])
        limited = self.server.list_files(path=".", recursive=False, max_entries=1)
        self.assertEqual(len(limited), 1)

    def test_math_mermaid_and_diagram_sync_branches(self):
        with self.assertRaises(ValueError):
            self.server.math_verify("x", "x", trials=0)
        out = self.server.math_verify("x + 1", "x + 2", variables=["x"], trials=2)
        self.assertFalse(out["proven"])
        self.assertEqual(len(out["checks"]), 2)

        with self.assertRaises(ValueError):
            self.server.mermaid_lint_fix("", auto_fix=True)
        fixed = self.server.mermaid_lint_fix("```mermaid\nA->B\n```", auto_fix=True)
        self.assertIn("flowchart LR", fixed["fixed_mermaid"])
        self.assertIn("-->", fixed["fixed_mermaid"])
        invalid = self.server.mermaid_lint_fix("flowchart LR\nnode1", auto_fix=False)
        self.assertFalse(invalid["valid"])
        self.assertEqual(invalid["fixed_mermaid"], "flowchart LR\nnode1")

        source = self.write_repo_text("docs/source.txt", "content\n")
        diagram = self.write_repo_text("docs/diagram.mmd", "flowchart LR\nA-->B\n")
        with self.assertRaises(ValueError):
            self.server.diagram_sync_check(source_paths=[], diagram_path="docs/diagram.mmd")
        with self.assertRaises(ValueError):
            self.server.diagram_sync_check(source_paths=["docs/source.txt"], diagram_path="docs/diagram.mmd", mode="bad")
        with self.assertRaises(FileNotFoundError):
            self.server.diagram_sync_check(source_paths=["docs/source.txt"], diagram_path="missing.mmd")
        with self.assertRaises(FileNotFoundError):
            self.server.diagram_sync_check(source_paths=["missing.txt"], diagram_path="docs/diagram.mmd")

        updated = self.server.diagram_sync_check(
            source_paths=["docs/source.txt"],
            diagram_path="docs/diagram.mmd",
            mode="update",
        )
        self.assertTrue(updated["in_sync"])
        updated_again = self.server.diagram_sync_check(
            source_paths=["docs/source.txt"],
            diagram_path="docs/diagram.mmd",
            mode="update",
        )
        self.assertTrue(updated_again["in_sync"])

    def test_prompt_and_generated_ignore_extractors(self):
        prompt = "./README.md and `README.md` plus docs/a.md and missing.txt and docs/a.md"
        extracted = self.server._extract_prompt_file_paths(prompt, max_paths=2)
        self.assertEqual(extracted, ["README.md", "docs/a.md"])
        self.assertEqual(self.server._extract_prompt_file_paths(prompt, max_paths=0), [])

        text = "\n".join(
            [
                "header",
                "# codebase-tooling-mcp generated",
                "",
                "# comment before entries",
                "/tmp/one",
                "/tmp/two",
                "stop-here",
                "/tmp/ignored",
            ]
        )
        ignores = self.server._extract_codebase_tooling_generated_ignores(text)
        self.assertEqual(ignores, ["/tmp/one", "/tmp/two"])
