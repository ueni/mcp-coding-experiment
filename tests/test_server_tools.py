import importlib.util
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


def _load_server_module():
    module_path = Path(__file__).resolve().parents[1] / "toolchain" / "dev" / "server.py"
    spec = importlib.util.spec_from_file_location("dev_server", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ServerToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _load_server_module()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.tmp.name).resolve()

        subprocess.run(["git", "-C", str(self.repo_path), "init", "-b", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo_path), "config", "user.email", "ci@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo_path), "config", "user.name", "CI Bot"],
            check=True,
        )

        (self.repo_path / "src").mkdir(parents=True, exist_ok=True)
        (self.repo_path / "tests").mkdir(parents=True, exist_ok=True)
        (self.repo_path / "docs").mkdir(parents=True, exist_ok=True)
        (self.repo_path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        (self.repo_path / "src" / "sample.py").write_text(
            "import os\n\n"
            "def alpha(x):\n"
            "    return x + 1\n\n"
            "def beta(y):\n"
            "    return alpha(y)\n",
            encoding="utf-8",
        )
        (self.repo_path / "tests" / "test_sample.py").write_text(
            "from src.sample import alpha\n\n"
            "def test_alpha():\n"
            "    assert alpha(1) == 2\n",
            encoding="utf-8",
        )
        (self.repo_path / "tests" / "test_smoke.py").write_text(
            "import unittest\n\n"
            "class SmokeTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (self.repo_path / "docs" / "a.md").write_text("hello world\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo_path), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo_path), "commit", "-m", "init"], check=True)

        self.server.REPO_PATH = self.repo_path
        self.server.ALLOW_MUTATIONS = True

    def tearDown(self):
        self.tmp.cleanup()

    def test_prompt_optimize(self):
        out = self.server.prompt_optimize("Please analyze the code and make a safe fix.")
        self.assertEqual(out["schema"], "prompt_optimize.v1")
        self.assertIn("optimized_prompt", out)
        self.assertGreater(out["optimized_chars"], 0)

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
        for mode in ("coding", "review", "search"):
            out = self.server.prompt_optimize("Need minimal output", mode=mode)
            self.assertEqual(out["mode"], mode)
            self.assertLessEqual(out["optimized_chars"], 2000)

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
            "repeat": "toolchain/dev/server.py",
            "nested": {
                "repeat": "toolchain/dev/server.py",
                "items": ["toolchain/dev/server.py", "docs/index.md"],
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
            "repeat": "toolchain/dev/server.py",
            "nested": {"repeat": "changed", "items": ["toolchain/dev/server.py"]},
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

        routed = self.server.tool_router_learned(
            query="find files",
            candidates=["find_paths", "grep"],
            mode="route",
        )
        self.assertEqual(routed["schema"], "tool_router_learned.v1")
        self.assertIn(routed["selected_tool"], {"find_paths", "grep"})
        rec = self.server.tool_router_learned(
            query="find files",
            candidates=["find_paths", "grep"],
            mode="record",
            selected_tool="find_paths",
            success=True,
            latency_ms=12.0,
        )
        self.assertEqual(rec["mode"], "record")

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


if __name__ == "__main__":
    unittest.main()
