import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
