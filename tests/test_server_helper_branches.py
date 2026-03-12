# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import io
import json
import subprocess
import sys
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class _FakePdfPage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePdfReader:
    def __init__(self, _path):
        self.pages = [_FakePdfPage("Page 1"), _FakePdfPage("Page 2")]


class _FakeDocxDocument:
    def __init__(self, _path):
        self.paragraphs = [SimpleNamespace(text="Hello"), SimpleNamespace(text="World")]
        cell = SimpleNamespace(text="Cell")
        row = SimpleNamespace(cells=[cell])
        self.tables = [SimpleNamespace(rows=[row])]


class _FakeXlsSheet:
    def __init__(self, name, rows):
        self.name = name
        self._rows = rows
        self.nrows = len(rows)

    def row_values(self, idx):
        return self._rows[idx]


class _FakeXlsBook:
    def __init__(self):
        self.nsheets = 1
        self._sheet = _FakeXlsSheet("Sheet1", [["A", "B"], ["C", "D"]])

    def sheet_by_index(self, idx):
        assert idx == 0
        return self._sheet


class _FakeEndpointResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStdStream:
    def __init__(self):
        self.closed = False
        self.data = b""

    def write(self, data):
        self.data += data

    def flush(self):
        return None

    def close(self):
        self.closed = True


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdStream()
        self.stdout = _FakeStdStream()
        self._poll = None
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self._poll = 9

    def wait(self, timeout=None):
        del timeout
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise subprocess.TimeoutExpired(cmd=["cat"], timeout=1)
        self._poll = 0
        return 0


class ServerHelperCoverageTest(ServerToolsTestBase):
    def test_query_memory_cache_and_report_helpers(self):
        self.assertEqual(self.server._guess_file_type(Path("a.json")), "json")
        self.assertEqual(self.server._guess_file_type(Path("a.toml")), "toml")
        self.assertEqual(self.server._guess_file_type(Path("a.yaml")), "yaml")
        with self.assertRaises(ValueError):
            self.server._guess_file_type(Path("a.ini"))

        self.assertEqual(self.server._parse_query_path("items[0].name"), ["items", 0, "name"])
        self.assertEqual(self.server._query_value({"items": [{"name": "x"}]}, "items[0].name"), "x")
        with self.assertRaises(ValueError):
            self.server._parse_query_path("items[")
        with self.assertRaises(ValueError):
            self.server._query_value({"items": []}, "items[0]")
        with self.assertRaises(ValueError):
            self.server._query_value([], "name")

        self.assertEqual(self.server._memory_load(), {"entries": [], "summaries": [], "decisions": []})
        mem_file = self.repo_path / ".build" / "memory" / "context_memory.json"
        mem_file.parent.mkdir(parents=True, exist_ok=True)
        mem_file.write_text("{bad json", encoding="utf-8")
        self.assertEqual(self.server._memory_load(), {"entries": [], "summaries": [], "decisions": []})
        mem_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        self.assertEqual(self.server._memory_load(), {"entries": [], "summaries": [], "decisions": []})

        reports = self.repo_path / ".build" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "A.txt").write_text("a\n", encoding="utf-8")
        (reports / "B.txt").write_text("b\n", encoding="utf-8")
        listed_reports = self.server._list_report_files(max_entries=1)
        self.assertEqual(len(listed_reports), 1)
        self.assertIn(listed_reports[0], {".build/reports/A.txt", ".build/reports/B.txt"})

        self.server._cache_set("tool", "k1", {"schema": "one"})
        self.server._cache_set("tool", "k2", [{"a": 1}])
        stats = self.server._cache_stats()
        listed = self.server._cache_list_tool("tool", limit=5)
        pruned = self.server._cache_prune(max_age_minutes=1)
        cleared = self.server._cache_clear("tool")
        self.assertGreaterEqual(stats["total_entries"], 2)
        self.assertEqual(len(listed), 2)
        self.assertIn("scanned_entries", pruned)
        self.assertGreaterEqual(cleared["removed_entries"], 0)

    def test_document_reader_helpers(self):
        pdf_path = self.write_repo_text("files/doc.pdf", "pdf")
        doc_path = self.write_repo_text("files/doc.doc", "doc text")
        xls_path = self.write_repo_text("files/sheet.xls", "xls")
        ods_path = self.repo_path / "files" / "sheet.ods"
        odt_path = self.repo_path / "files" / "note.odt"
        odp_path = self.repo_path / "files" / "slides.odp"
        pptx_path = self.repo_path / "files" / "slides.pptx"
        ppt_path = self.write_repo_text("files/legacy.ppt", "Legacy PPT text")
        ods_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(self.server, "PdfReader", _FakePdfReader):
            pdf_text, pdf_meta = self.server._read_pdf_text(pdf_path, max_pages=1)
        self.assertEqual(pdf_text, "Page 1")
        self.assertEqual(pdf_meta["pages_read"], 1)
        with self.assertRaises(ValueError):
            self.server._read_pdf_text(pdf_path, max_pages=0)

        fake_docx = SimpleNamespace(Document=_FakeDocxDocument)
        with patch.object(self.server, "docx", fake_docx):
            docx_text, docx_meta = self.server._read_docx_text(Path("dummy.docx"))
        self.assertIn("Hello", docx_text)
        self.assertEqual(docx_meta["table_cells"], 1)

        antiword_proc = subprocess.CompletedProcess(args=["antiword"], returncode=0, stdout="antiword text", stderr="")
        with patch.object(self.server.shutil, "which", return_value="antiword"), patch.object(
            self.server.subprocess,
            "run",
            return_value=antiword_proc,
        ):
            doc_text, doc_meta = self.server._read_doc_text(doc_path)
        self.assertEqual(doc_text, "antiword text")
        self.assertEqual(doc_meta["backend"], "antiword")
        with patch.object(self.server.shutil, "which", return_value=None):
            fallback_text, fallback_meta = self.server._read_doc_text(doc_path)
        self.assertIn("doc text", fallback_text)
        self.assertEqual(fallback_meta["backend"], "latin1-fallback")

        fake_xlrd = SimpleNamespace(open_workbook=lambda _path: _FakeXlsBook())
        with patch.object(self.server, "xlrd", fake_xlrd):
            xls_text, xls_meta = self.server._read_xls_text(xls_path, max_rows_per_sheet=1)
        self.assertEqual(xls_meta["rows_read"], 1)
        self.assertIn("A | B", xls_text)

        ods_xml = (
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            '<table:table table:name="Sheet1"><table:table-row><table:table-cell><text:p>A</text:p></table:table-cell></table:table-row></table:table>'
            '</office:document-content>'
        )
        with zipfile.ZipFile(ods_path, "w") as zf:
            zf.writestr("content.xml", ods_xml)
        ods_text, ods_meta = self.server._read_opendoc_text(ods_path, ext=".ods", max_rows_per_sheet=5)
        self.assertIn("A", ods_text)
        self.assertEqual(ods_meta["sheet_count"], 1)

        odt_xml = (
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            '<text:p>Paragraph one</text:p><text:p>Paragraph two</text:p>'
            '</office:document-content>'
        )
        with zipfile.ZipFile(odt_path, "w") as zf:
            zf.writestr("content.xml", odt_xml)
        odt_text, odt_meta = self.server._read_opendoc_text(odt_path, ext=".odt", max_rows_per_sheet=5)
        self.assertIn("Paragraph one", odt_text)
        self.assertEqual(odt_meta["paragraph_count"], 2)

        pptx_xml = '<p:sld xmlns:p="p" xmlns:a="a"><a:t>Title</a:t><a:t>Body</a:t></p:sld>'
        with zipfile.ZipFile(pptx_path, "w") as zf:
            zf.writestr("ppt/slides/slide1.xml", pptx_xml)
        slides, meta = self.server._read_pptx_presentation(pptx_path, max_slides=2, max_chars_per_slide=50)
        self.assertEqual(meta["slides_read"], 1)
        self.assertEqual(slides[0]["title"], "Title")

        odp_xml = (
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            '<draw:page draw:name="Intro"><text:p>Line 1</text:p></draw:page>'
            '</office:document-content>'
        )
        with zipfile.ZipFile(odp_path, "w") as zf:
            zf.writestr("content.xml", odp_xml)
        odp_slides, odp_meta = self.server._read_odp_presentation(odp_path, max_slides=2, max_chars_per_slide=50)
        self.assertEqual(odp_meta["slides_read"], 1)
        self.assertEqual(odp_slides[0]["title"], "Intro")

        with patch.object(self.server.shutil, "which", side_effect=["catppt", None]), patch.object(
            self.server.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["catppt"], returncode=0, stdout="Slide text", stderr=""),
        ):
            legacy_slides, legacy_meta, warnings = self.server._read_ppt_legacy_text(
                ppt_path,
                max_slides=2,
                max_chars_per_slide=50,
            )
        self.assertEqual(legacy_meta["slide_count"], 1)
        self.assertEqual(warnings, [])
        self.assertIn("Slide text", legacy_slides[0]["text"])

        with patch.object(self.server.shutil, "which", return_value=None):
            fallback_slides, _, fallback_warnings = self.server._read_ppt_legacy_text(
                ppt_path,
                max_slides=2,
                max_chars_per_slide=50,
            )
        self.assertGreaterEqual(len(fallback_warnings), 1)
        self.assertGreaterEqual(len(fallback_slides), 1)

    def test_local_endpoint_runtime_and_terminal_helpers(self):
        body = json.dumps({"response": "hello"}).encode("utf-8")
        with patch.object(self.server, "_urlopen_with_host_certs", return_value=_FakeEndpointResponse(body)):
            text = self.server._local_infer_via_endpoint(
                prompt="hi",
                model="m",
                max_tokens=10,
                temperature=0.1,
                system="sys",
                stop=["done"],
            )
        self.assertEqual(text, "hello")

        plain = b"not json"
        with patch.object(self.server, "_urlopen_with_host_certs", return_value=_FakeEndpointResponse(plain)):
            raw = self.server._local_infer_via_endpoint(
                prompt="hi",
                model="m",
                max_tokens=10,
                temperature=0.1,
            )
        self.assertEqual(raw, "not json")

        selected, vectors = self.server._local_embed_vectors(["alpha", "beta"], backend="hash", normalize=True)
        self.assertEqual(selected, "hash")
        self.assertEqual(len(vectors), 2)
        with self.assertRaises(ValueError):
            self.server._local_embed_vectors(["x"], backend="bad")

        with patch.object(self.server, "_urlopen_with_host_certs", return_value=_FakeEndpointResponse(b"", status=204)):
            probe = self.server._probe_http("https://example.com")
        self.assertTrue(probe["reachable"])
        self.assertEqual(probe["status"], 204)
        with patch.object(self.server, "_urlopen_with_host_certs", side_effect=RuntimeError("offline")):
            failed = self.server._probe_http("https://example.com")
        self.assertFalse(failed["reachable"])
        self.assertIn("offline", failed["error"])

        with patch.object(self.server, "_list_listening_ports", return_value={11434, self.server.PORT}), patch.object(
            self.server,
            "_count_processes_with_tokens",
            side_effect=[1, 2],
        ), patch.object(
            self.server,
            "_probe_http",
            return_value={"reachable": True, "status": 200},
        ), patch.object(
            self.server,
            "_docker_cli_status",
            return_value={"available": True},
        ):
            runtime = self.server._runtime_state_payload(include_ollama_probe=True)
        self.assertTrue(runtime["ollama"]["running"])
        self.assertTrue(runtime["ollama"]["port_11434_listening"])
        self.assertEqual(runtime["docker"]["available"], True)

        with self.assertRaises(ValueError):
            self.server.terminal_support_session(mode="bad")
        with self.assertRaises(ValueError):
            self.server.terminal_support_session(mode="poll", session_id="missing")
        with self.assertRaises(ValueError):
            self.server.terminal_support_session(mode="start", command=["cat"], read_timeout_ms=-1)

        fake_proc = _FakeProc()
        self.server._TERMINAL_SESSIONS["pipe1"] = {
            "proc": fake_proc,
            "backend": "pipe",
            "master_fd": -1,
            "read_fd": 0,
            "log_path": str(self.repo_path / ".build" / "terminal-captures" / "pipe1.log"),
            "command": ["cat"],
            "cwd": ".",
            "input_chars": 0,
            "output_chars": 0,
        }
        Path(self.server._TERMINAL_SESSIONS["pipe1"]["log_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(self.server._TERMINAL_SESSIONS["pipe1"]["log_path"]).write_text("", encoding="utf-8")
        with patch.object(self.server, "_terminal_read_available", return_value="echo"):
            polled = self.server.terminal_support_session(
                mode="poll",
                session_id="pipe1",
                include_output=False,
            )
            stopped = self.server.terminal_support_session(
                mode="stop",
                session_id="pipe1",
                include_output=False,
            )
        self.assertEqual(polled["output"], "")
        self.assertFalse(stopped["running"])
        self.assertTrue(fake_proc.terminated)
        self.assertTrue(fake_proc.killed)

    def test_self_test_smart_fix_fast_path_and_risk_helpers(self):
        failing = subprocess.CompletedProcess(
            args=[sys.executable, "-m", "unittest", "missing"],
            returncode=1,
            stdout="",
            stderr="failed",
        )
        with patch.object(self.server.subprocess, "run", return_value=failing):
            out = self.server.self_test(
                runner="unittest",
                target="repo:missing.py",
                verbose=True,
                fail_fast=True,
                timeout_seconds=5,
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["exit_code"], 1)

        plan = self.server.smart_fix_batch(
            findings=[
                {"path": "src/sample.py", "search": "return x + 1", "replacement": "return x + 2"},
                {"path": "src/sample.py", "search": "missing", "replacement": "noop"},
            ],
            mode="plan",
        )
        self.assertEqual(plan["file_count"], 1)

        bad_py = self.write_repo_text("src/broken.py", "value = 1\n")
        broken_proc = subprocess.CompletedProcess(
            args=[sys.executable, "-m", "py_compile", str(bad_py)],
            returncode=1,
            stdout="",
            stderr="SyntaxError",
        )
        with patch.object(self.server.subprocess, "run", return_value=broken_proc):
            executed = self.server.smart_fix_batch(
                findings=[
                    {"path": "src/broken.py", "search": "value = 1", "replacement": "def broken(:\n"},
                ],
                mode="execute",
                regex=False,
                replace_all=False,
                run_validation=True,
            )
        self.assertFalse(executed["ok"])
        self.assertEqual(executed["compile_error_count"], 1)

        with patch.object(
            self.server,
            "token_budget_guard",
            return_value={"max_output_chars": 1000, "default_output_profile": "compact"},
        ), patch.object(
            self.server,
            "repo_index_daemon",
            return_value={"schema": "repo_index_daemon.quick.v1", "ok": True},
        ), patch.object(
            self.server,
            "release_readiness",
            return_value={"schema": "release_readiness.quick.v1", "ok": True},
        ), patch.object(
            self.server,
            "required_tool_chain",
            return_value={"schema": "required_tool_chain.v1", "ok": True},
        ), patch.object(
            self.server,
            "_result_store_put",
            return_value="rid-fast",
        ):
            fast = self.server.fast_path_dev(
                task="release",
                refresh_index=True,
                run_readiness=True,
                enforce_tool_chain=True,
                store_result=True,
            )
        self.assertTrue(fast["ok"])
        self.assertIn("repo_index", fast["steps"])
        self.assertIn("release_readiness", fast["steps"])
        self.assertIn("required_tool_chain", fast["steps"])
        self.assertEqual(fast["result_id"], "rid-fast")

        with patch.object(
            self.server,
            "summarize_diff",
            return_value={
                "file_count": 25,
                "total_added": 1200,
                "total_deleted": 400,
                "risk_flags": {"risky_files": ["Dockerfile", "requirements.txt"], "todo_like_additions": 2},
            },
        ):
            risk = self.server.risk_scoring()
        self.assertEqual(risk["risk_level"], "high")
        self.assertIn("large file count", risk["reasons"])
        self.assertIn("very high churn", risk["reasons"])
        self.assertIn("sensitive file changes", risk["reasons"])
