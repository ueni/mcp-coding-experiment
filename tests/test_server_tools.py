# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio
import json
import subprocess
import sys
import unittest
import urllib.parse
import zipfile
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase

class ServerToolsTest(ServerToolsTestBase):

    def test_prompt_optimize(self):
        out = self.server.prompt_optimize("Please analyze the code and make a safe fix.")
        self.assertEqual(out["schema"], "prompt_optimize.v1")
        self.assertIn("optimized_prompt", out)
        self.assertGreater(out["optimized_chars"], 0)

    def test_mcp_resources_and_templates(self):
        async def run_checks():
            resources = await self.server.mcp.list_resources()
            templates = await self.server.mcp.list_resource_templates()

            resource_uris = {str(item.model_dump().get("uri")) for item in resources}
            template_uris = {item.model_dump().get("uriTemplate") for item in templates}

            self.assertIn("repo://summary", resource_uris)
            self.assertIn("repo://file/{path}", template_uris)
            self.assertIn("repo://tree/{path}", template_uris)

            summary_contents = await self.server.mcp.read_resource("repo://summary")
            self.assertGreaterEqual(len(summary_contents), 1)
            summary_payload = json.loads(summary_contents[0].content)
            self.assertEqual(summary_payload["schema"], "resource.repo_summary.v1")

            encoded_file = urllib.parse.quote("src/sample.py", safe="")
            file_contents = await self.server.mcp.read_resource(f"repo://file/{encoded_file}")
            self.assertGreaterEqual(len(file_contents), 1)
            self.assertIn("def alpha", file_contents[0].content)

            tree_contents = await self.server.mcp.read_resource("repo://tree/src")
            self.assertGreaterEqual(len(tree_contents), 1)
            tree_payload = json.loads(tree_contents[0].content)
            self.assertEqual(tree_payload["schema"], "resource.repo_tree.v1")
            self.assertIn("src/sample.py", tree_payload["entries"])

        asyncio.run(run_checks())

    def test_terminal_support_session(self):
        started = self.server.terminal_support_session(
            mode="start",
            command=["cat"],
            cwd=".",
            read_timeout_ms=10,
        )
        self.assertEqual(started["schema"], "terminal_support_session.v1")
        sid = started["session_id"]
        listed = self.server.terminal_support_session(mode="list")
        self.assertIn(sid, {row["session_id"] for row in listed["sessions"]})
        sent = self.server.terminal_support_session(
            mode="send",
            session_id=sid,
            input_text="hello-support\n",
            read_timeout_ms=20,
        )
        self.assertEqual(sent["schema"], "terminal_support_session.v1")
        self.assertIn("hello-support", sent["output"])
        stopped = self.server.terminal_support_session(mode="stop", session_id=sid, read_timeout_ms=20)
        self.assertEqual(stopped["schema"], "terminal_support_session.v1")
        self.assertFalse(stopped["running"])
        self.assertTrue((self.repo_path / stopped["log_path"]).is_file())

    def test_cache_control_and_symbol_cache(self):
        _ = self.server.symbol_index(path="src", output_profile="compact")
        stats = self.server.cache_control(mode="stats")
        self.assertIn("tools", stats)
        self.assertGreaterEqual(stats["tools"].get("symbol_index", 0), 1)
        inspect = self.server.cache_control(mode="inspect_tool", tool="symbol_index", limit=10)
        self.assertEqual(inspect["mode"], "inspect_tool")
        self.assertGreaterEqual(inspect["count"], 1)
        self.assertEqual(inspect["entries"][0]["value_type"], "list")
        pruned = self.server.cache_control(mode="prune", tool="symbol_index", max_age_minutes=1)
        self.assertEqual(pruned["mode"], "prune")
        self.assertIn("scanned_entries", pruned)

        cleared = self.server.cache_control(mode="clear_tool", tool="symbol_index")
        self.assertEqual(cleared["tool"], "symbol_index")
        self.assertGreaterEqual(cleared["removed_entries"], 1)

    def test_result_handle_store_fetch_list_clear(self):
        stored = self.server.result_handle(mode="store", tool="manual", value=[{"a": 1}, {"a": 2}])
        rid = stored["result_id"]
        fetched = self.server.result_handle(mode="fetch", result_id=rid, offset=1, limit=1)
        self.assertEqual(fetched["value"], [{"a": 2}])

        listed = self.server.result_handle(mode="list")
        self.assertGreaterEqual(listed["count"], 1)

        cleared = self.server.result_handle(mode="clear")
        self.assertTrue(cleared["cleared"])

    def test_grep_quick_and_store_result(self):
        out = self.server.grep(
            pattern="alpha",
            path=".",
            summary_mode="quick",
            store_result=True,
            output_profile="compact",
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["schema"], "grep.quick.v1")
        self.assertIn("result_id", out[0])

    def test_semantic_find_quick_compress(self):
        out = self.server.semantic_find(
            query="alpha sample",
            path=".",
            summary_mode="quick",
            compress=True,
            store_result=True,
            output_profile="compact",
        )
        self.assertEqual(out["schema"], "semantic_find.quick.v1")
        self.assertIn("result_id", out)

    def test_dependency_map_and_call_graph(self):
        dep = self.server.dependency_map(
            path="src",
            output_profile="compact",
            fields=["from", "to"],
            offset=0,
            limit=10,
        )
        self.assertEqual(dep["schema"], "dependency_map.compact.v1")
        self.assertIn("edge_count", dep)

        cg = self.server.call_graph(path="src", output_profile="compact", summary_mode="quick")
        self.assertIn("edge_count", cg)

    def test_tree_sitter_core_status(self):
        out = self.server.tree_sitter_core(mode="status")
        self.assertIn("available", out)
        self.assertEqual(out["engine"], "tree_sitter_languages")

    def test_repo_index_daemon_refresh_read_query(self):
        refresh = self.server.repo_index_daemon(
            mode="refresh",
            path=".",
            output_profile="compact",
            summary_mode="quick",
            incremental=True,
        )
        self.assertEqual(refresh["schema"], "repo_index_daemon.quick.v1")

        read = self.server.repo_index_daemon(mode="read", output_profile="compact")
        self.assertEqual(read["schema"], "repo_index_daemon.compact.v1")

        query = self.server.repo_index_daemon(
            mode="query",
            query="files",
            offset=0,
            limit=2,
            fields=["path"],
            output_profile="normal",
        )
        self.assertEqual(query["mode"], "query")
        self.assertIn("value_json", query)

    def test_code_index_router_modes(self):
        refreshed = self.server.code_index_router(
            mode="refresh",
            path=".",
            output_profile="compact",
            summary_mode="quick",
        )
        self.assertEqual(refreshed["schema"], "code_index_router.v1")
        self.assertEqual(refreshed["mode"], "refresh")
        self.assertIn("schema", refreshed["result"])

        symbols = self.server.code_index_router(
            mode="symbols",
            path="src",
            output_profile="compact",
            summary_mode="quick",
            limit=10,
        )
        self.assertEqual(symbols["schema"], "code_index_router.v1")
        self.assertEqual(symbols["mode"], "symbols")
        self.assertIsInstance(symbols["result"], list)
        self.assertGreaterEqual(len(symbols["result"]), 1)

    def test_tool_benchmark(self):
        out = self.server.tool_benchmark(tools=["find_paths", "grep"], iterations=1, warmup=0)
        self.assertEqual(out["schema"], "tool_benchmark.v1")
        self.assertEqual(len(out["results"]), 2)

    def test_self_test_unittest(self):
        out = self.server.self_test(
            runner="unittest",
            target="tests/test_smoke.py",
            verbose=False,
            timeout_seconds=60,
        )
        self.assertEqual(out["schema"], "self_test.v1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["runner"], "unittest")

    def test_output_size_guard_write_and_check(self):
        write = self.server.output_size_guard(mode="write")
        self.assertEqual(write["mode"], "write")
        self.assertTrue((self.repo_path / ".build" / "reports" / "TOOL_OUTPUT_BASELINE.json").is_file())

        check = self.server.output_size_guard(mode="check")
        self.assertIn("ok", check)

    def test_token_budget_guard_compact_default(self):
        out = self.server.token_budget_guard(reset=True)
        self.assertEqual(out["default_output_profile"], "compact")

    def test_prompt_optimize_modes(self):
        for mode in ("coding", "review", "search", "tooling_strict"):
            out = self.server.prompt_optimize("Need minimal output", mode=mode)
            self.assertEqual(out["mode"], mode)
            self.assertLessEqual(out["optimized_chars"], 2000)
            self.assertIn("strictness_score_before", out)
            self.assertIn("strictness_score_after", out)
            self.assertGreaterEqual(out["strictness_score_after"], out["strictness_score_before"])

    def test_tool_prompt_score(self):
        out = self.server.tool_prompt_score(scope="core", top_n=5)
        self.assertEqual(out["schema"], "tool_prompt_score.v1")
        self.assertEqual(out["scope"], "core")
        self.assertGreaterEqual(out["tool_count"], 1)
        self.assertIn("avg_score", out)
        self.assertIn("lowest_tools", out)

    def test_local_model_status(self):
        out = self.server.local_model_status()
        self.assertEqual(out["schema"], "local_model_status.v1")
        self.assertIn("embed", out)
        self.assertIn("infer", out)

    def test_local_embed_and_rerank(self):
        emb = self.server.local_embed(
            texts=["alpha beta", "gamma delta"],
            backend="hash",
            output_profile="normal",
        )
        self.assertEqual(emb["schema"], "local_embed.v1")
        self.assertEqual(emb["count"], 2)
        self.assertGreater(emb["dim"], 0)

        rerank = self.server.local_rerank(
            query="alpha",
            candidates=[
                {"path": "src/sample.py", "kind": "path", "match": "alpha"},
                {"path": "docs/a.md", "kind": "path", "match": "hello"},
            ],
            top_k=2,
            backend="hash",
            output_profile="normal",
        )
        self.assertEqual(rerank["schema"], "local_rerank.v1")
        self.assertEqual(rerank["count"], 2)

    def test_local_infer_fallback(self):
        out = self.server.local_infer(
            prompt="explain alpha function quickly",
            backend="fallback",
            output_profile="compact",
            max_tokens=64,
        )
        self.assertTrue(out["ok"])
        self.assertIn("output", out)

    def test_model_router_parallel_infer(self):
        out = self.server.model_router(
            mode="parallel_infer",
            prompts=["alpha", "beta", "gamma"],
            backend="fallback",
            max_parallel=2,
            output_profile="compact",
            max_tokens=32,
        )
        self.assertEqual(out["schema"], "parallel_infer.v1")
        self.assertEqual(out["count"], 3)
        self.assertEqual(len(out["rows"]), 3)
        self.assertEqual(out["max_parallel"], 2)

        with self.assertRaises(ValueError):
            self.server.model_router(mode="parallel_infer", prompts=[], max_parallel=2)

    def test_model_router_parallel_infer_tool_backed_fallback(self):
        (self.repo_path / ".gitignore").write_text(
            "# codebase-tooling-mcp generated\n/.build/\n/.continue/\n",
            encoding="utf-8",
        )
        out = self.server.model_router(
            mode="parallel_infer",
            prompts=[
                "From .gitignore, list the codebase-tooling generated ignore entries.",
            ],
            backend="fallback",
            output_profile="compact",
            max_parallel=1,
            max_tokens=64,
        )
        self.assertEqual(out["schema"], "parallel_infer.v1")
        self.assertEqual(out["ok_count"], 1)
        row = out["rows"][0]
        self.assertTrue(row["ok"])
        result = row["result"]
        self.assertEqual(result["backend"], "tool_fallback")
        self.assertIn("/.build/", result["output"])
        self.assertIn("/.continue/", result["output"])

    def test_model_router_parallel_infer_tool_backed_summary_is_concise(self):
        out = self.server.model_router(
            mode="parallel_infer",
            prompts=[
                "Summarize src/sample.py in 2 concise sentences focused on behavior.",
            ],
            backend="fallback",
            output_profile="compact",
            max_parallel=1,
            max_tokens=96,
        )
        self.assertEqual(out["schema"], "parallel_infer.v1")
        row = out["rows"][0]
        result = row["result"]
        self.assertEqual(result["backend"], "tool_fallback")
        text = result["output"]
        self.assertIn("alpha", text.lower())
        self.assertNotIn("[truncated:", text)
        self.assertLessEqual(len(text), 420)

    def test_model_router_infer_auto_parallel_upgrade(self):
        parallel_payload = {
            "schema": "parallel_infer.v1",
            "count": 2,
            "ok_count": 2,
            "error_count": 0,
            "max_parallel": 2,
            "rows": [],
        }
        with patch.object(self.server, "_parallel_infer", return_value=parallel_payload) as pinf, patch.object(
            self.server, "local_infer", return_value={"schema": "local_infer.v1", "ok": True}
        ) as linf:
            out = self.server.model_router(
                mode="infer",
                prompt="- summarize docs\n- review changed files",
                backend="fallback",
                max_parallel=2,
            )
        self.assertEqual(out["schema"], "model_router.infer_auto_parallel.v1")
        self.assertTrue(out["upgraded"])
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["result"]["schema"], "parallel_infer.v1")
        self.assertEqual(pinf.call_count, 1)
        self.assertEqual(linf.call_count, 0)

    def test_model_router_infer_auto_parallel_can_be_disabled(self):
        with patch.object(self.server, "local_infer", return_value={"schema": "local_infer.v1", "ok": True}) as linf:
            out = self.server.model_router(
                mode="infer",
                prompt="- summarize docs\n- review changed files",
                backend="fallback",
                auto_parallel_when_possible=False,
            )
        self.assertEqual(out["schema"], "local_infer.v1")
        self.assertEqual(linf.call_count, 1)

    def test_autocomplete_fallback(self):
        out = self.server.autocomplete(
            prefix="def handler():",
            backend="fallback",
            output_profile="compact",
            max_tokens=16,
        )
        self.assertEqual(out["schema"], "autocomplete.compact.v1")
        self.assertEqual(out["backend"], "fallback")
        self.assertTrue(out["ok"])
        self.assertIn("completion", out)

    def test_semantic_find_with_local_rerank(self):
        out = self.server.semantic_find(
            query="alpha sample",
            path=".",
            output_profile="normal",
            max_results=5,
            use_local_rerank=True,
            local_rerank_top_k=5,
        )
        self.assertEqual(out["schema"], "semantic_find.v1")
        self.assertGreaterEqual(out["count"], 1)

    def test_math_tools(self):
        if self.server.sp is None:
            self.skipTest("sympy not installed in test runtime")
        parsed = self.server.math_parser("x**2 + 2*x + 1")
        self.assertEqual(parsed["schema"], "math_parser.v1")
        self.assertIn("parsed", parsed)

        solved = self.server.math_solver(mode="solve", expression="x**2 - 1")
        self.assertEqual(solved["schema"], "math_solver.v1")
        self.assertIn("solutions", solved)

        verified = self.server.math_verify("x*(x+1)", "x**2 + x")
        self.assertEqual(verified["schema"], "math_verify.v1")
        self.assertTrue(verified["proven"])

    def test_sql_security_and_doc_tools(self):
        sql_fmt = self.server.sql_expert(mode="format", query="select * from users where id=1")
        self.assertEqual(sql_fmt["schema"], "sql_expert.v1")
        self.assertIn("formatted", sql_fmt)

        triage = self.server.security_triage(
            diff_text='+ api_key = "secret"\n+ os.system("rm -rf /")\n',
            paths=["src/auth.py"],
        )
        self.assertEqual(triage["schema"], "security_triage.v1")
        self.assertGreaterEqual(triage["finding_count"], 1)

        summary = self.server.doc_summarizer_small(
            "Error occurred. Warning detected. Everything else is fine."
        )
        self.assertEqual(summary["schema"], "doc_summarizer_small.v1")
        self.assertIn("summary", summary)

    def test_classifier_testgen_translation(self):
        classified = self.server.code_review_classifier(
            findings=[
                {"title": "Potential SQL injection in query builder"},
                {"title": "Slow loop allocation"},
            ]
        )
        self.assertEqual(classified["schema"], "code_review_classifier.v1")
        self.assertGreaterEqual(classified["counts"]["security"], 1)

        generated = self.server.test_gen_small(
            function_name="alpha",
            path="src/sample.py",
            framework="pytest",
        )
        self.assertEqual(generated["schema"], "test_gen_small.v1")
        self.assertIn("test_code", generated)

        translated = self.server.translation_small(
            text="hello world",
            source_lang="en",
            target_lang="de",
            mode="lexical",
        )
        self.assertEqual(translated["schema"], "translation_small.v1")
        self.assertNotEqual(translated["translated"], "")

    def test_diagram_from_code_and_mermaid_lint_fix(self):
        diagram = self.server.diagram_from_code(
            path="src",
            diagram_type="flowchart",
            max_nodes=20,
            include_call_edges=False,
            output_profile="compact",
        )
        self.assertEqual(diagram["schema"], "diagram_from_code.compact.v1")
        self.assertIn("flowchart", diagram["mermaid"])

        linted = self.server.mermaid_lint_fix("A -> B", auto_fix=True)
        self.assertEqual(linted["schema"], "mermaid_lint_fix.v1")
        self.assertGreaterEqual(linted["issue_count"], 1)
        self.assertIn("flowchart", linted["fixed_mermaid"])
        self.assertIn("-->", linted["fixed_mermaid"])

    def test_drawio_generator_generate_and_parse(self):
        generated = self.server.drawio_generator(
            mode="generate",
            nodes=[
                {"id": "n1", "label": "Service"},
                {"id": "n2", "label": "DB"},
            ],
            edges=[{"source": "n1", "target": "n2"}],
        )
        self.assertEqual(generated["schema"], "drawio_generator.v1")
        self.assertEqual(generated["mode"], "generate")
        self.assertIn("<mxfile", generated["drawio_xml"])

        parsed = self.server.drawio_generator(mode="parse", drawio_xml=generated["drawio_xml"])
        self.assertEqual(parsed["schema"], "drawio_generator.v1")
        self.assertEqual(parsed["mode"], "parse")
        self.assertEqual(parsed["node_count"], 2)
        self.assertEqual(parsed["edge_count"], 1)

    def test_diagram_sync_check_update_and_check(self):
        diagram_doc = self.repo_path / "docs" / "architecture.md"
        diagram_doc.write_text("# Architecture\n\nflowchart LR\nA-->B\n", encoding="utf-8")

        checked = self.server.diagram_sync_check(
            source_paths=["src/sample.py"],
            diagram_path="docs/architecture.md",
            mode="check",
        )
        self.assertEqual(checked["schema"], "diagram_sync_check.v1")
        self.assertFalse(checked["in_sync"])

        updated = self.server.diagram_sync_check(
            source_paths=["src/sample.py"],
            diagram_path="docs/architecture.md",
            mode="update",
        )
        self.assertTrue(updated["in_sync"])
        self.assertFalse(updated["needs_update"])

        rechecked = self.server.diagram_sync_check(
            source_paths=["src/sample.py"],
            diagram_path="docs/architecture.md",
            mode="check",
        )
        self.assertTrue(rechecked["in_sync"])

    def test_read_document_formats(self):
        # Real .xlsx fixture when openpyxl is installed.
        if self.server.openpyxl is not None:
            wb = self.server.openpyxl.Workbook()
            ws = wb.active
            ws.title = "Data"
            ws.append(["name", "value"])
            ws.append(["alpha", 1])
            xlsx_path = self.repo_path / "docs" / "table.xlsx"
            wb.save(str(xlsx_path))
            xlsx_out = self.server.read_document(path="docs/table.xlsx", max_rows_per_sheet=10)
            self.assertIn(xlsx_out["schema"], {"read_document.v1", "read_document.compact.v1"})
            self.assertEqual(xlsx_out["extension"], ".xlsx")
            self.assertIn("alpha", xlsx_out["text"])

        # Real .docx fixture when python-docx is installed.
        if self.server.docx is not None:
            doc = self.server.docx.Document()
            doc.add_paragraph("hello docx")
            docx_path = self.repo_path / "docs" / "sample.docx"
            doc.save(str(docx_path))
            docx_out = self.server.read_document(path="docs/sample.docx")
            self.assertIn(docx_out["schema"], {"read_document.v1", "read_document.compact.v1"})
            self.assertEqual(docx_out["extension"], ".docx")
            self.assertIn("hello docx", docx_out["text"])

        # Dispatch coverage for .pdf and .xls using parser patching.
        pdf_path = self.repo_path / "docs" / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        with patch.object(self.server, "_read_pdf_text", return_value=("hello pdf", {"page_count": 1, "pages_read": 1})):
            pdf_out = self.server.read_document(path="docs/sample.pdf", max_pages=5, output_profile="compact")
        self.assertEqual(pdf_out["schema"], "read_document.compact.v1")
        self.assertEqual(pdf_out["extension"], ".pdf")
        self.assertIn("hello pdf", pdf_out["text"])

        xls_path = self.repo_path / "docs" / "legacy.xls"
        xls_path.write_bytes(b"fake-xls")
        with patch.object(self.server, "_read_xls_text", return_value=("a | b", {"sheet_count": 1, "rows_read": 1, "sheets": [{"name": "S1", "rows_read": 1}]})):
            xls_out = self.server.read_document(path="docs/legacy.xls")
        self.assertIn(xls_out["schema"], {"read_document.v1", "read_document.compact.v1"})
        self.assertEqual(xls_out["extension"], ".xls")
        self.assertIn("a | b", xls_out["text"])

    def test_read_document_opendoc_formats(self):
        odt_xml = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body><office:text><text:p>Hello ODT</text:p></office:text></office:body>
</office:document-content>"""
        odt_path = self.repo_path / "docs" / "sample.odt"
        with zipfile.ZipFile(odt_path, "w") as zf:
            zf.writestr("content.xml", odt_xml)
        odt_out = self.server.read_document(path="docs/sample.odt")
        self.assertEqual(odt_out["extension"], ".odt")
        self.assertIn("Hello ODT", odt_out["text"])

        ods_xml = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body><office:spreadsheet>
    <table:table table:name="Sheet1">
      <table:table-row>
        <table:table-cell><text:p>a</text:p></table:table-cell>
        <table:table-cell><text:p>1</text:p></table:table-cell>
      </table:table-row>
    </table:table>
  </office:spreadsheet></office:body>
</office:document-content>"""
        ods_path = self.repo_path / "docs" / "sample.ods"
        with zipfile.ZipFile(ods_path, "w") as zf:
            zf.writestr("content.xml", ods_xml)
        ods_out = self.server.read_document(path="docs/sample.ods")
        self.assertEqual(ods_out["extension"], ".ods")
        self.assertIn("a | 1", ods_out["text"])

        odp_xml = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body><office:presentation><text:p>Hello ODP</text:p></office:presentation></office:body>
</office:document-content>"""
        odp_path = self.repo_path / "docs" / "sample.odp"
        with zipfile.ZipFile(odp_path, "w") as zf:
            zf.writestr("content.xml", odp_xml)
        odp_out = self.server.read_document(path="docs/sample.odp", output_profile="compact")
        self.assertEqual(odp_out["schema"], "read_document.compact.v1")
        self.assertEqual(odp_out["extension"], ".odp")
        self.assertIn("Hello ODP", odp_out["text"])

    def test_browse_web_html_extract_and_compact(self):
        class _FakeResp:
            def __init__(self):
                self.status = 200
                self.url = "https://example.com/final"
                self.headers = {"Content-Type": "text/html; charset=utf-8"}

            def read(self, n=-1):
                body = b"<html><head><title>T</title></head><body><h1>Hello</h1><script>x=1</script><p>World</p></body></html>"
                return body if n < 0 else body[:n]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(self.server, "_urlopen_with_host_certs", return_value=_FakeResp()):
            out = self.server.browse_web(
                url="https://example.com",
                output_profile="compact",
                max_chars=200,
            )
        self.assertEqual(out["schema"], "browse_web.compact.v1")
        self.assertEqual(out["status"], 200)
        self.assertIn("Hello World", out["text"])

    def test_browse_web_rejects_non_http_scheme(self):
        with self.assertRaises(ValueError):
            self.server.browse_web(url="file:///etc/passwd")

    def test_image_interpret_classify_and_ui_parse(self):
        img_path = self.repo_path / "docs" / "image.png"
        img_path.write_bytes(b"not-a-real-png-but-path-exists")
        with patch.object(
            self.server,
            "_image_basic_features",
            return_value={"width": 1280, "height": 720, "aspect_ratio": 1.7778, "mean_luma": 200.0, "mode": "RGB", "format": "png"},
        ), patch.object(
            self.server,
            "Image",
            None,
        ), patch.object(
            self.server,
            "pytesseract",
            None,
        ):
            out = self.server.image_interpret(
                image_path="docs/image.png",
                mode="classify",
                output_profile="compact",
            )
        self.assertEqual(out["schema"], "image_interpret.compact.v1")
        self.assertIn(out["label"], {"photo_like", "minimal_graphic", "ui_screenshot", "diagram_or_slide", "document_scan"})

        with patch.object(
            self.server,
            "_image_basic_features",
            return_value={"width": 1200, "height": 700, "aspect_ratio": 1.714, "mean_luma": 170.0, "mode": "RGB", "format": "png"},
        ), patch.object(
            self.server,
            "Image",
            None,
        ), patch.object(
            self.server,
            "pytesseract",
            None,
        ):
            ui_out = self.server.image_interpret(
                image_path="docs/image.png",
                mode="ui_parse",
                output_profile="normal",
            )
        self.assertEqual(ui_out["schema"], "image_interpret.v1")
        self.assertIn("summary", ui_out)

    def test_image_interpret_with_local_model_and_ocr_mode_validation(self):
        img_path = self.repo_path / "docs" / "image2.png"
        img_path.write_bytes(b"png")
        with patch.object(
            self.server,
            "_image_basic_features",
            return_value={"width": 800, "height": 600, "aspect_ratio": 1.3333, "mean_luma": 150.0, "mode": "RGB", "format": "png"},
        ), patch.object(
            self.server,
            "Image",
            None,
        ), patch.object(
            self.server,
            "pytesseract",
            None,
        ), patch.object(
            self.server,
            "local_infer",
            return_value={"output": "Interpreted by local model", "backend": "fallback"},
        ):
            out = self.server.image_interpret(
                image_path="docs/image2.png",
                mode="caption",
                use_local_model=True,
                output_profile="normal",
            )
        self.assertTrue(out["used_local_model"])
        self.assertEqual(out["model_backend"], "fallback")
        self.assertIn("Interpreted by local model", out["summary"])

        with patch.object(self.server, "Image", None), patch.object(self.server, "pytesseract", None):
            with self.assertRaises(RuntimeError):
                self.server.image_interpret(image_path="docs/image2.png", mode="ocr")

    def test_interpret_presentation_pptx_and_odp(self):
        pptx_slide = """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <p:sp><p:txBody><a:p><a:r><a:t>Roadmap</a:t></a:r></a:p></p:txBody></p:sp>
    <p:sp><p:txBody><a:p><a:r><a:t>Q1 deliverables</a:t></a:r></a:p></p:txBody></p:sp>
  </p:spTree></p:cSld>
</p:sld>"""
        pptx_path = self.repo_path / "docs" / "deck.pptx"
        with zipfile.ZipFile(pptx_path, "w") as zf:
            zf.writestr("ppt/slides/slide1.xml", pptx_slide)
        out_pptx = self.server.interpret_presentation(
            path="docs/deck.pptx", use_local_model=False, output_profile="compact"
        )
        self.assertEqual(out_pptx["schema"], "interpret_presentation.compact.v1")
        self.assertEqual(out_pptx["extension"], ".pptx")
        self.assertEqual(out_pptx["slide_count"], 1)
        self.assertIn("Roadmap", out_pptx["summary"])

        odp_xml = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body><office:presentation>
    <draw:page draw:name="Intro"><text:p>Welcome</text:p></draw:page>
  </office:presentation></office:body>
</office:document-content>"""
        odp_path = self.repo_path / "docs" / "deck.odp"
        with zipfile.ZipFile(odp_path, "w") as zf:
            zf.writestr("content.xml", odp_xml)
        out_odp = self.server.interpret_presentation(
            path="docs/deck.odp",
            use_local_model=False,
            output_profile="normal",
        )
        self.assertEqual(out_odp["schema"], "interpret_presentation.v1")
        self.assertEqual(out_odp["extension"], ".odp")
        self.assertEqual(out_odp["slide_count"], 1)
        self.assertIn("Intro", out_odp["summary"])

    def test_interpret_presentation_with_local_model(self):
        pptx_slide = """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Budget</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>"""
        pptx_path = self.repo_path / "docs" / "model.pptx"
        with zipfile.ZipFile(pptx_path, "w") as zf:
            zf.writestr("ppt/slides/slide1.xml", pptx_slide)
        with patch.object(
            self.server,
            "local_infer",
            return_value={"output": "Summary by local model", "backend": "fallback"},
        ):
            out = self.server.interpret_presentation(
                path="docs/model.pptx",
                use_local_model=True,
                output_profile="normal",
            )
        self.assertTrue(out["used_local_model"])
        self.assertEqual(out["model_backend"], "fallback")
        self.assertIn("Summary by local model", out["summary"])

    def test_license_monitor_detects_missing_headers(self):
        out = self.server.license_monitor(
            path="src",
            run_reuse_lint=False,
            generate_spdx=False,
            auto_fix_headers=False,
            download_missing_licenses=False,
        )
        self.assertEqual(out["schema"], "license_monitor.v1")
        self.assertGreaterEqual(out["missing_spdx_header_count"], 1)
        self.assertIn("src/sample.py", out["missing_spdx_headers"])

    def test_install_git_hooks_creates_pre_commit_and_pre_push(self):
        out = self.server.install_git_hooks(
            install_pre_commit=True,
            install_pre_push=True,
            include_foss_reports=True,
            include_lab_reports=True,
            overwrite=True,
        )
        self.assertEqual(out["schema"], "install_git_hooks.v1")
        self.assertIn(".git/hooks/pre-commit", out["installed"])
        self.assertIn(".git/hooks/pre-push", out["installed"])

        pre_commit = self.repo_path / ".git" / "hooks" / "pre-commit"
        pre_push = self.repo_path / ".git" / "hooks" / "pre-push"
        self.assertTrue(pre_commit.is_file())
        self.assertTrue(pre_push.is_file())
        self.assertIn("reuse lint", pre_commit.read_text(encoding="utf-8"))
        self.assertIn("policy_gatekeeper.py", pre_commit.read_text(encoding="utf-8"))
        self.assertIn("repo_digital_twin.py", pre_push.read_text(encoding="utf-8"))

    def test_commit_lint_tag(self):
        out = self.server.commit_lint_tag(message="feat(api): add release gate")
        self.assertEqual(out["schema"], "commit_lint_tag.v1")
        self.assertTrue(out["lint_ok"])
        self.assertIn("feat(api): add release gate", out["message"])

    def test_golden_output_guard_write_and_check(self):
        write = self.server.golden_output_guard(
            mode="write",
            tools=["token_budget_guard"],
        )
        self.assertEqual(write["schema"], "golden_output_guard.v1")
        self.assertTrue(write["ok"])

        check = self.server.golden_output_guard(
            mode="check",
            tools=["token_budget_guard"],
        )
        self.assertEqual(check["schema"], "golden_output_guard.v1")
        self.assertTrue(check["ok"])

    def test_flaky_test_detector_unittest(self):
        out = self.server.flaky_test_detector(
            runner="unittest",
            target="tests/test_smoke.py",
            runs=3,
            update_history=True,
        )
        self.assertEqual(out["schema"], "flaky_test_detector.v1")
        self.assertEqual(out["runs"], 3)
        self.assertEqual(out["runner"], "unittest")
        self.assertEqual(len(out["run_results"]), 3)

    def test_change_impact_gate_blocks_on_missing_docs(self):
        sample = self.repo_path / "src" / "sample.py"
        sample.write_text(sample.read_text(encoding="utf-8") + "\n# change\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo_path), "add", "src/sample.py"], check=True)
        subprocess.run(["git", "-C", str(self.repo_path), "commit", "-m", "feat: change sample"], check=True)

        out = self.server.change_impact_gate(
            base_ref="HEAD~1",
            head_ref="HEAD",
            require_docs_for_impl_diff=True,
            require_tests_for_critical=False,
            block_on_risk_level="none",
        )
        self.assertEqual(out["schema"], "change_impact_gate.v1")
        self.assertTrue(out["should_block"])
        self.assertGreaterEqual(len(out["blocked_reasons"]), 1)

    def test_smart_fix_batch_plan_and_execute(self):
        plan = self.server.smart_fix_batch(
            findings=[
                {
                    "path": "src/sample.py",
                    "search": "return x + 1",
                    "replacement": "return x + 2",
                    "description": "adjust increment",
                }
            ],
            mode="plan",
        )
        self.assertEqual(plan["schema"], "smart_fix_batch.v1")
        self.assertEqual(plan["mode"], "plan")

        execute = self.server.smart_fix_batch(
            findings=[
                {
                    "path": "src/sample.py",
                    "search": "return x + 1",
                    "replacement": "return x + 2",
                }
            ],
            mode="execute",
            run_validation=True,
        )
        self.assertEqual(execute["schema"], "smart_fix_batch.v1")
        self.assertEqual(execute["mode"], "execute")
        self.assertGreaterEqual(execute["applied_count"], 1)
        self.assertIn("return x + 2", (self.repo_path / "src" / "sample.py").read_text(encoding="utf-8"))

    def test_release_readiness_quick(self):
        out = self.server.release_readiness(
            base_ref="HEAD",
            head_ref="HEAD",
            run_tests=True,
            test_runner="unittest",
            test_target="tests/test_smoke.py",
            run_docs_check=True,
            run_security_check=False,
            run_license_check=False,
            run_risk_check=True,
            run_impact_check=True,
            summary_mode="quick",
        )
        self.assertEqual(out["schema"], "release_readiness.quick.v1")
        self.assertIn("checks", out)
        self.assertIn("tests", out["checks"])

    def test_lossless_codec_roundtrip_and_delta(self):
        payload = {
            "title": "release checklist",
            "repeat": "source/server.py",
            "nested": {
                "repeat": "source/server.py",
                "items": ["source/server.py", "docs/index.md"],
            },
        }
        enc = self.server.encode_lossless(
            value=payload,
            use_symbols=True,
            use_blob_refs=False,
            store_blobs=False,
        )
        self.assertEqual(enc["schema"], "lossless_codec.v1")
        self.assertEqual(enc["mode"], "encode")
        self.assertIn("encoded", enc)

        dec = self.server.decode_lossless(
            encoded=enc["encoded"],
            symbol_table=enc["symbol_table"],
            blobs_inline={},
        )
        self.assertEqual(dec["schema"], "lossless_codec.v1")
        self.assertEqual(dec["mode"], "decode")
        self.assertEqual(dec["decoded"], payload)

        rt = self.server.roundtrip_verify(value=payload, use_blob_refs=False)
        self.assertEqual(rt["schema"], "lossless_codec.v1")
        self.assertTrue(rt["ok"])

        target = {
            "title": "release checklist",
            "repeat": "source/server.py",
            "nested": {"repeat": "changed", "items": ["source/server.py"]},
            "new_key": 7,
        }
        delta = self.server.delta_encode(base=payload, target=target)
        self.assertEqual(delta["schema"], "delta_codec.v1")
        self.assertGreaterEqual(delta["op_count"], 1)
        applied = self.server.delta_apply(base=payload, ops=delta["ops"])
        self.assertEqual(applied["schema"], "delta_codec.v1")
        self.assertEqual(applied["value"], target)

    def test_required_tool_chain(self):
        a = self.server.result_handle(mode="store", tool="tool_a", value={"ok": 1})
        b = self.server.result_handle(mode="store", tool="tool_b", value={"ok": 1})
        report = self.repo_path / ".build" / "reports" / "SAMPLE.txt"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("ok\n", encoding="utf-8")

        out = self.server.required_tool_chain(
            required_tools=["tool_a", "tool_b"],
            required_artifacts=[".build/reports/SAMPLE.txt"],
            required_result_ids=[a["result_id"], b["result_id"]],
            require_order=True,
            max_age_minutes=60,
        )
        self.assertEqual(out["schema"], "required_tool_chain.v1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["missing_tools"], [])
        self.assertEqual(out["missing_artifacts"], [])
        self.assertEqual(out["missing_result_ids"], [])

    def test_fast_path_dev_minimal(self):
        out = self.server.fast_path_dev(
            task="quick-check",
            refresh_index=False,
            run_readiness=False,
            enforce_tool_chain=False,
            store_result=False,
        )
        self.assertEqual(out["schema"], "fast_path_dev.v1")
        self.assertEqual(out["task"], "quick-check")
        self.assertTrue(out["ok"])
        self.assertIn("token_budget", out["steps"])

    def test_llm_mcp_power_tools(self):
        compiled = self.server.workflow_compiler(goal="fast release with risk checks")
        self.assertEqual(compiled["schema"], "workflow_compiler.v1")
        self.assertGreaterEqual(len(compiled["steps"]), 1)
        self.assertFalse(compiled["cached"])
        compiled_cached = self.server.workflow_compiler(goal="fast release with risk checks")
        self.assertTrue(compiled_cached["cached"])
        self.assertEqual(compiled["cache_key"], compiled_cached["cache_key"])

        snap = self.server.state_snapshot(label="t")
        self.assertEqual(snap["schema"], "state_snapshot.v1")
        self.assertEqual(snap["backend"], "git-stash")
        self.assertIn("snapshot_id", snap)
        restored = self.server.state_restore(snapshot_id=snap["snapshot_id"])
        self.assertEqual(restored["schema"], "state_restore.v1")
        self.assertEqual(restored["backend"], "git-stash")

        pol = self.server.policy_simulator(base_ref="HEAD", head_ref="HEAD")
        self.assertEqual(pol["schema"], "policy_simulator.v1")
        self.assertIn("blocking_policies", pol)

        intent = self.server.intent_router(
            query="find files in the repo",
            candidates=["find_paths", "grep"],
        )
        self.assertEqual(intent["schema"], "intent_router.v1")
        self.assertEqual(intent["selected_tool"], "find_paths")

        routed = self.server.tool_router_learned(
            query="find files",
            candidates=["find_paths", "grep"],
            mode="route",
        )
        self.assertEqual(routed["schema"], "tool_router_learned.v1")
        self.assertEqual(routed["selected_by"], "intent_router")
        self.assertEqual(routed["selected_tool"], "find_paths")
        self.assertIsNotNone(routed["fallback"])

        rec = self.server.tool_router_learned(
            query="find files",
            candidates=["find_paths", "grep"],
            mode="record",
            selected_tool="grep",
            success=True,
            latency_ms=10.0,
        )
        self.assertEqual(rec["mode"], "record")
        self.server.tool_router_learned(
            query="find files",
            candidates=["find_paths", "grep"],
            mode="record",
            selected_tool="grep",
            success=True,
            latency_ms=10.0,
        )
        routed_learned = self.server.tool_router_learned(
            query="find files",
            candidates=["find_paths", "grep"],
            mode="route",
        )
        self.assertEqual(routed_learned["selected_by"], "learned")
        self.assertEqual(routed_learned["selected_tool"], "grep")
        self.assertTrue(routed_learned["confidence"]["confident"])

        art = self.server.artifact_memory_index(mode="refresh", path="docs")
        self.assertEqual(art["schema"], "artifact_memory_index.v1")
        artq = self.server.artifact_memory_index(mode="query", query="docs")
        self.assertEqual(artq["schema"], "artifact_memory_index.v1")

        solved = self.server.constraint_solver_for_tasks(
            requirements=["run tests", "update docs"],
            actions=["run tests", "commit changes"],
        )
        self.assertEqual(solved["schema"], "constraint_solver_for_tasks.v1")
        self.assertFalse(solved["ok"])

        st = self.server.spec_to_tests(
            spec_text="- system must authenticate users\n- response should be fast",
            framework="pytest",
            mode="generate",
        )
        self.assertEqual(st["schema"], "spec_to_tests.v1")
        self.assertIn("def test_spec_", st["test_code"])

        shards = self.server.auto_sharding_for_analysis(path=".", shard_size=2)
        self.assertEqual(shards["schema"], "auto_sharding_for_analysis.v1")
        self.assertGreaterEqual(shards["shard_count"], 1)

        conf = self.server.confidence_scoring(
            checks=[{"name": "a", "ok": True, "weight": 2}, {"name": "b", "ok": False, "weight": 1}]
        )
        self.assertEqual(conf["schema"], "confidence_scoring.v1")
        self.assertIn(conf["level"], {"low", "medium", "high"})

        contract = self.server.runtime_contract_checker()
        self.assertEqual(contract["schema"], "runtime_contract_checker.v1")
        self.assertIn("ok", contract)

        budget_set = self.server.cost_budget_enforcer(mode="set", max_tokens=100, max_calls=10, max_seconds=60)
        self.assertEqual(budget_set["schema"], "cost_budget_enforcer.v1")
        budget_record = self.server.cost_budget_enforcer(mode="record", used_tokens=10, used_calls=1, used_seconds=5)
        self.assertTrue(budget_record["ok"])

        lane = self.server.multi_agent_lane(task="pre-release", base_ref="HEAD", head_ref="HEAD")
        self.assertEqual(lane["schema"], "multi_agent_lane.v1")
        self.assertIn("confidence", lane)

        approval = self.server.human_approval_points(mode="create", action="deploy", risk_level="high", details="prod deploy")
        self.assertEqual(approval["schema"], "human_approval_points.v1")
        listed = self.server.human_approval_points(mode="list")
        self.assertGreaterEqual(listed["count"], 1)
        resolved = self.server.human_approval_points(
            mode="resolve",
            approval_id=approval["item"]["approval_id"],
            approved=True,
        )
        self.assertEqual(resolved["item"]["status"], "approved")

        rc_add = self.server.root_cause_memory(
            mode="add",
            issue="timeout in tests",
            root_cause="slow setup",
            fix="cache fixtures",
        )
        self.assertEqual(rc_add["schema"], "root_cause_memory.v1")
        rc_suggest = self.server.root_cause_memory(mode="suggest", issue="test timeout", max_entries=5)
        self.assertEqual(rc_suggest["schema"], "root_cause_memory.v1")

        replay = self.server.execution_replay(mode="start")
        self.assertEqual(replay["schema"], "execution_replay.v1")
        rid = replay["replay_id"]
        logged = self.server.execution_replay(mode="log", replay_id=rid, event={"tool": "x"})
        self.assertEqual(logged["schema"], "execution_replay.v1")
        read = self.server.execution_replay(mode="read", replay_id=rid)
        self.assertGreaterEqual(len(read["events"]), 1)
        done = self.server.execution_replay(mode="finish", replay_id=rid)
        self.assertEqual(done["status"], "closed")

    def test_model_router_coding_modes_and_validation(self):
        with self.assertRaises(ValueError):
            self.server.model_router(mode="not_a_mode")

        with patch.object(
            self.server,
            "local_infer",
            return_value={"schema": "local_infer.v1", "model": "qwen2.5-coder:7b", "ok": True},
        ), patch.object(
            self.server,
            "_coding_checks",
            return_value={"schema": "coding_checks.v1", "ok": True, "steps": []},
        ), patch.object(
            self.server,
            "CODING_VENV_PYTHON",
            sys.executable,
        ):
            out = self.server.model_router(
                mode="coding_infer",
                prompt="write function",
                run_checks=True,
                sandbox_mode="shared",
            )
        self.assertEqual(out["schema"], "model_router.coding_infer.v1")
        self.assertTrue(out["check_requested"])
        self.assertIn("checks", out)
        self.assertIn("sandbox", out)
        self.assertIn("stdout_stream", out)
        self.assertIn("stderr_stream", out)

        with patch.object(
            self.server,
            "CODING_VENV_PYTHON",
            str(self.repo_path / "does-not-exist" / "python"),
        ):
            with self.assertRaises(FileNotFoundError):
                self.server.model_router(
                    mode="coding_check",
                    check_profile="lint",
                    check_target="src/sample.py",
                )

    def test_model_router_coding_check_and_pip_include_stream_fields(self):
        checks_payload = {
            "schema": "coding_checks.v1",
            "ok": False,
            "steps": [
                {
                    "command": ["python", "-m", "pytest", "-q"],
                    "stdout": "collected 1 item",
                    "stderr": "E AssertionError",
                }
            ],
        }
        with patch.object(self.server, "_coding_checks", return_value=checks_payload), patch.object(
            self.server, "_coding_sandbox_prepare", return_value={"venv_python": sys.executable}
        ):
            out = self.server.model_router(
                mode="coding_check",
                check_profile="quick",
                check_target="src/sample.py",
            )
        self.assertIn("stdout_stream", out)
        self.assertIn("stderr_stream", out)
        self.assertGreaterEqual(len(out["stdout_stream"]), 1)
        self.assertGreaterEqual(len(out["stderr_stream"]), 1)

        pip_payload = {
            "schema": "coding_pip.v1",
            "ok": True,
            "command": [sys.executable, "-m", "pip", "install", "pytest"],
            "stdout": "Successfully installed pytest",
            "stderr": "",
        }
        with patch.object(self.server, "_coding_pip_install", return_value=pip_payload), patch.object(
            self.server, "_coding_sandbox_prepare", return_value={"venv_python": sys.executable}
        ):
            out_pip = self.server.model_router(
                mode="coding_pip",
                packages=["pytest"],
            )
        self.assertIn("stdout_stream", out_pip)
        self.assertIn("stderr_stream", out_pip)
        self.assertGreaterEqual(len(out_pip["stdout_stream"]), 1)

    def test_model_router_coding_sandbox_lifecycle(self):
        base_venv = self.repo_path / ".build" / "base-venv"
        subprocess.run(["python", "-m", "venv", str(base_venv)], check=True)
        python_bin = base_venv / "bin" / "python"

        with patch.object(self.server, "CODING_VENV_PYTHON", str(python_bin)):
            created = self.server.model_router(
                mode="coding_sandbox",
                sandbox_action="create",
                sandbox_id="sbox-test",
            )
            self.assertEqual(created["schema"], "coding_sandbox.v1")
            self.assertEqual(created["action"], "create")
            self.assertEqual(created["sandbox_id"], "sbox-test")

            listed = self.server.model_router(mode="coding_sandbox", sandbox_action="list")
            ids = {row["sandbox_id"] for row in listed["items"]}
            self.assertIn("sbox-test", ids)

            deleted = self.server.model_router(
                mode="coding_sandbox",
                sandbox_action="delete",
                sandbox_id="sbox-test",
            )
            self.assertTrue(deleted["deleted"])

    def test_memory_router_auto_compact_and_usage_stats(self):
        for i in range(10):
            self.server.memory_router(
                mode="upsert",
                namespace="compact_demo",
                key=f"k{i}",
                value={"n": i, "payload": "x" * 180},
                ttl_days=30,
            )

        out = self.server.memory_router(
            mode="get",
            namespace="compact_demo",
            max_entries=100,
            auto_compact=True,
            compact_threshold_entries=5,
            compact_threshold_chars=1000,
            compact_keep_entries=3,
        )
        result = out["result"]
        self.assertGreaterEqual(result["count"], 10)
        self.assertIn("usage_stats", result)
        self.assertIn("events", result["usage_stats"])
        self.assertTrue(result["auto_compact"]["compacted"])

        compact = self.server.memory_router(
            mode="auto_compact",
            namespace="compact_demo",
            compact_threshold_entries=5,
            compact_threshold_chars=1000,
            compact_keep_entries=3,
        )
        self.assertTrue(compact["result"]["compacted"])

    def test_self_test_internal_and_repo_target_routing(self):
        internal_dir = self.repo_path / "internal_selftests"
        internal_dir.mkdir(parents=True, exist_ok=True)
        (internal_dir / "test_internal.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )

        with patch.object(self.server, "INTERNAL_SELF_TESTS_DIR", internal_dir):
            default_out = self.server.self_test(
                runner="unittest",
                target="tests",
                verbose=False,
                timeout_seconds=60,
                fail_fast=True,
            )
        self.assertTrue(default_out["ok"])
        self.assertEqual(default_out["execution_root"], "/")
        self.assertEqual(default_out["resolved_target"], str(internal_dir))

        repo_out = self.server.self_test(
            runner="unittest",
            target="repo:tests/test_smoke.py",
            verbose=False,
            timeout_seconds=60,
            fail_fast=True,
        )
        self.assertTrue(repo_out["ok"])
        self.assertEqual(repo_out["execution_root"], str(self.repo_path))
        self.assertEqual(repo_out["resolved_target"], "tests/test_smoke.py")

        with self.assertRaises(ValueError):
            self.server.self_test(runner="unittest", target="repo:", timeout_seconds=10)

    def test_docker_task_router_validation_paths(self):
        vscode_dir = self.repo_path / ".vscode"
        vscode_dir.mkdir(parents=True, exist_ok=True)
        tasks_path = vscode_dir / "tasks.json"
        tasks_path.write_text(
            json.dumps(
                {
                    "version": "2.0.0",
                    "tasks": [
                        {
                            "label": "Docker: blocked",
                            "type": "shell",
                            "command": "docker run hello-world",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(ValueError):
            self.server.docker_task_router(mode="run")

        listed = self.server.docker_task_router(
            mode="list",
            tasks_path=".vscode/tasks.json",
            control_profile="build",
        )
        self.assertEqual(listed["schema"], "docker_task_router.v1")
        self.assertEqual(listed["result"]["count"], 1)
        self.assertFalse(listed["result"]["tasks"][0]["ok"])

        with self.assertRaises(ValueError):
            self.server.docker_task_router(
                mode="run",
                label="Docker: blocked",
                tasks_path=".vscode/tasks.json",
                control_profile="build",
            )

    def test_helper_paths_for_add_include_item_iter_candidates(self):
        proposals = self.server._build_log_proposals("no space left on device", "")
        self.assertGreaterEqual(len(proposals), 1)
        self.assertIn("disk space", proposals[0]["issue"].lower())

        hidden_dir = self.repo_path / ".hidden"
        hidden_dir.mkdir(parents=True, exist_ok=True)
        hidden_file = hidden_dir / "x.txt"
        hidden_file.write_text("x\n", encoding="utf-8")
        visible_file = self.repo_path / "visible.txt"
        visible_file.write_text("y\n", encoding="utf-8")

        out_no_hidden = self.server.list_files(path=".", recursive=True, include_hidden=False)
        self.assertIn("visible.txt", out_no_hidden)
        self.assertFalse(any(p.startswith(".hidden/") for p in out_no_hidden))

        out_with_hidden = self.server.list_files(path=".", recursive=True, include_hidden=True)
        self.assertTrue(any(p.startswith(".hidden/") for p in out_with_hidden))

        replaced = self.server.replace_in_files(
            path="visible.txt",
            pattern="y",
            replacement="z",
            recursive=False,
            dry_run=False,
            regex=False,
            include_hidden=True,
            max_files=5,
            max_replacements=5,
        )
        self.assertEqual(len(replaced["files_changed"]), 1)
        self.assertEqual(replaced["total_replacements"], 1)


if __name__ == "__main__":
    unittest.main()
