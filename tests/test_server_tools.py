# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio
import json
import subprocess
import sys
import unittest
import zipfile
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase

class ServerToolsTest(ServerToolsTestBase):

    def test_prompt_optimize(self):
        out = self.server.prompt_optimize("Please analyze the code and make a safe fix.")
        self.assertEqual(out["schema"], "prompt_optimize.v1")
        self.assertIn("optimized_prompt", out)
        self.assertGreater(out["optimized_chars"], 0)

    def test_no_public_mcp_resources_or_templates_by_default(self):
        async def run_checks():
            resources = await self.server.mcp.list_resources()
            templates = await self.server.mcp.list_resource_templates()

            resource_uris = {str(item.model_dump().get("uri")) for item in resources}
            template_uris = {item.model_dump().get("uriTemplate") for item in templates}

            self.assertEqual(resource_uris, set())
            self.assertEqual(template_uris, set())

        asyncio.run(run_checks())
        self.assertEqual(
            self.server._normalize_public_resource_path("src/sample.py"),
            "src/sample.py",
        )
        with self.assertRaises(ValueError):
            self.server._normalize_public_resource_path(".codebase-tooling-mcp/index/repo_index.json")

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

    def test_terminal_support_session_safe_inline_python(self):
        started = self.server.terminal_support_session(
            mode="start",
            command=["python3", "-c", "print('hello-inline')"],
            cwd=".",
            read_timeout_ms=100,
        )
        self.assertEqual(started["schema"], "terminal_support_session.v1")
        stopped = self.server.terminal_support_session(
            mode="stop",
            session_id=started["session_id"],
            read_timeout_ms=20,
        )
        self.assertIn("hello-inline", started["output"] + stopped["output"])
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

    def test_semantic_find_quick_skips_symbol_index_and_returns_ranked_results(self):
        grep_payload = [
            {
                "path": "src/sample.py",
                "line": 1,
                "column": 5,
                "match": "alpha",
                "lineText": "def alpha(x):",
            }
        ]
        with patch.object(self.server, "find_paths", return_value=["src/sample.py"]), patch.object(
            self.server,
            "symbol_index",
            side_effect=AssertionError("symbol_index should not run in semantic_find quick mode"),
        ), patch.object(self.server, "grep", return_value=grep_payload):
            out = self.server.semantic_find(
                query="alpha",
                path=".",
                summary_mode="quick",
                output_profile="normal",
            )
        self.assertEqual(out["schema"], "semantic_find.quick.v1")
        self.assertEqual(out["count"], 1)
        self.assertIn("results", out)
        self.assertGreaterEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["path"], "src/sample.py")
        self.assertIn("src/sample.py", out["top_paths"])

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

    def test_code_index_leaf_modes(self):
        refreshed = self.server.repo_index_daemon(
            mode="refresh",
            path=".",
            output_profile="compact",
            summary_mode="quick",
        )
        self.assertEqual(refreshed["schema"], "repo_index_daemon.quick.v1")
        self.assertGreaterEqual(refreshed["file_count"], 1)

        symbols = self.server.symbol_index(
            path="src",
            output_profile="compact",
            limit=10,
        )
        self.assertIsInstance(symbols, list)
        self.assertGreaterEqual(len(symbols), 1)

    def test_tool_benchmark(self):
        out = self.server.tool_benchmark(tools=["find_paths", "grep"], iterations=1, warmup=0)
        self.assertEqual(out["schema"], "tool_benchmark.v1")
        self.assertEqual(len(out["results"]), 2)
        self.assertTrue(all("latency_ms_median" in row for row in out["results"]))

        report_path = self.repo_path / out["report_path"]
        self.assertTrue(report_path.is_file())
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "tool_benchmark.report.v1")
        self.assertEqual(set(report["tools"].keys()), {"find_paths", "grep"})
        self.assertIn("median_duration_ms", report["tools"]["find_paths"])

        out2 = self.server.tool_benchmark(tools=["find_paths"], iterations=1, warmup=0)
        report2 = json.loads((self.repo_path / out2["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(set(report2["tools"].keys()), {"find_paths", "grep"})
        self.assertEqual(report2["tools"]["find_paths"]["tool"], "find_paths")

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
        self.assertTrue((self.repo_path / ".codebase-tooling-mcp" / "reports" / "TOOL_OUTPUT_BASELINE.json").is_file())

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


    def test_local_infer_fallback_without_grounded_context_is_unavailable(self):
        out = self.server.local_infer(
            prompt="explain alpha function quickly",
            backend="fallback",
            output_profile="compact",
            max_tokens=64,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["backend"], "unavailable")
        self.assertTrue(out["degraded"])
        self.assertEqual(out["degraded_reason"], "no_grounded_fallback_available")
        self.assertEqual(out["output"], "")

    def test_local_infer_fallback_uses_grounded_tool_summary(self):
        out = self.server.local_infer(
            prompt="Summarize src/sample.py in 2 concise sentences focused on behavior.",
            backend="fallback",
            output_profile="compact",
            max_tokens=96,
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["backend"], "tool_fallback")
        self.assertTrue(out["degraded"])
        self.assertIn("alpha", out["output"].lower())

    def test_task_router_parallel_infer(self):
        out = self.server.task_router(
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
            self.server.task_router(mode="parallel_infer", prompts=[], max_parallel=2)

    def test_task_router_parallel_infer_tool_backed_fallback(self):
        (self.repo_path / ".gitignore").write_text(
            "# codebase-tooling-mcp generated\n/.codebase-tooling-mcp/\n/.continue/\n",
            encoding="utf-8",
        )
        out = self.server.task_router(
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
        self.assertIn("/.codebase-tooling-mcp/", result["output"])
        self.assertIn("/.continue/", result["output"])

    def test_task_router_parallel_infer_tool_backed_summary_is_concise(self):
        out = self.server.task_router(
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

    def test_task_router_infer_auto_parallel_upgrade(self):
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
            out = self.server.task_router(
                mode="infer",
                prompt="- summarize docs\n- review changed files",
                backend="fallback",
                max_parallel=2,
            )
        self.assertEqual(out["schema"], "task_router.infer_auto_parallel.v1")
        self.assertTrue(out["upgraded"])
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["result"]["schema"], "parallel_infer.v1")
        self.assertEqual(pinf.call_count, 1)
        self.assertEqual(linf.call_count, 0)


    def test_task_router_infer_auto_parallel_can_be_disabled(self):
        with patch.object(self.server, "local_infer", return_value={"schema": "local_infer.v1", "ok": True}) as linf:
            out = self.server.task_router(
                mode="infer",
                prompt="- summarize docs\n- review changed files",
                backend="fallback",
                auto_parallel_when_possible=False,
            )
        self.assertEqual(out["schema"], "local_infer.v1")
        self.assertEqual(linf.call_count, 1)

    def test_task_router_infer_uses_single_explicit_prompt(self):
        with patch.object(self.server, "local_infer", return_value={"schema": "local_infer.v1", "ok": True}) as linf:
            out = self.server.task_router(
                mode="infer",
                prompt="ignored prompt",
                prompts=["single explicit prompt"],
                backend="fallback",
            )
        self.assertEqual(out["schema"], "local_infer.v1")
        self.assertEqual(linf.call_count, 1)
        self.assertEqual(linf.call_args.kwargs["prompt"], "single explicit prompt")

    def test_task_router_task_classifies_encodes_and_routes(self):
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "deepseek-r1:1.5b",
            "ok": True,
            "output": "security findings",
        }
        with patch.object(self.server, "local_infer", return_value=infer_payload) as linf:
            out = self.server.task_router(
                mode="task",
                prompt="Review auth middleware for security vulnerabilities and secret exposure.",
                backend="fallback",
                output_profile="normal",
            )
        self.assertEqual(out["schema"], "task_router.task.v1")
        self.assertEqual(out["classification"]["route"], "security")
        self.assertEqual(out["routing"]["selected_model"], "deepseek-r1:1.5b")
        self.assertTrue(out["routing"]["routing_loaded"])
        packet = json.loads(out["encoding"]["encoded_prompt"])
        self.assertEqual(packet["r"], "SEC")
        self.assertIn("i", packet)
        self.assertEqual(packet["i"]["d"], "findings")
        self.assertEqual(linf.call_args.kwargs["task"], "security")
        self.assertEqual(linf.call_args.kwargs["model"], "deepseek-r1:1.5b")

    def test_task_router_task_includes_retrieval_context(self):
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "granite3.3:2b",
            "ok": True,
            "output": "review findings",
        }
        search_payload = {
            "schema": "semantic_find.quick.v1",
            "query": "sample alpha behavior regressions",
            "count": 1,
            "results": [
                {
                    "kind": "symbol",
                    "path": "src/sample.py",
                    "name": "alpha",
                    "line_start": 4,
                    "line_end": 6,
                    "score": 7.0,
                }
            ],
        }
        snippet_payload = {
            "path": "src/sample.py",
            "start_line": 2,
            "end_line": 10,
            "content": "def alpha():\n    return 'alpha'\n",
        }
        with patch.object(self.server, "semantic_find", return_value=search_payload), patch.object(
            self.server, "read_snippet", return_value=snippet_payload
        ), patch.object(self.server, "local_infer", return_value=infer_payload):
            out = self.server.task_router(
                mode="task",
                prompt="Review sample alpha behavior for regressions.",
                backend="fallback",
                output_profile="normal",
            )
        packet = json.loads(out["encoding"]["encoded_prompt"])
        self.assertIn("k", packet)
        self.assertIn("src/sample.py", packet["k"])
        self.assertIn("def alpha()", packet["k"])
        self.assertEqual(out["retrieval"]["item_count"], 1)
        self.assertEqual(out["retrieval"]["sources"]["code_search"], 1)
        self.assertEqual(out["retrieval"]["items"][0]["path"], "src/sample.py")
        self.assertIn("context", out["context_packet"])
        self.assertGreaterEqual(out["retrieval"]["telemetry"]["explored_candidates"], 1)
        self.assertEqual(out["retrieval"]["telemetry"]["selected_items"], 1)

    def test_task_router_task_retrieval_context_uses_quick_top_paths(self):
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "granite3.3:2b",
            "ok": True,
            "output": "review findings",
        }
        search_payload = {
            "schema": "semantic_find.quick.v1",
            "query": "sample alpha behavior regressions",
            "count": 1,
            "top_paths": ["src/sample.py"],
        }
        snippet_payload = {
            "path": "src/sample.py",
            "start_line": 1,
            "end_line": 6,
            "content": "def alpha(x):\n    return x + 1\n",
        }
        with patch.object(self.server, "semantic_find", return_value=search_payload), patch.object(
            self.server, "read_snippet", return_value=snippet_payload
        ), patch.object(self.server, "local_infer", return_value=infer_payload):
            out = self.server.task_router(
                mode="task",
                prompt="Review sample alpha behavior for regressions.",
                backend="fallback",
                output_profile="normal",
            )
        packet = json.loads(out["encoding"]["encoded_prompt"])
        self.assertIn("k", packet)
        self.assertIn("src/sample.py", packet["k"])
        self.assertEqual(out["retrieval"]["item_count"], 1)
        self.assertEqual(out["retrieval"]["items"][0]["path"], "src/sample.py")
        self.assertEqual(out["retrieval"]["sources"]["code_search"], 1)
        self.assertGreaterEqual(out["retrieval"]["telemetry"]["explored_candidates"], 1)

    def test_task_router_task_uses_repo_memory_and_curated_skill_pack(self):
        sample_path = self.repo_path / "src" / "sample.py"
        sample_path.write_text(
            sample_path.read_text(encoding="utf-8").replace(
                "    return alpha(y)\n",
                "    # recent commit for repo memory\n    return alpha(y)\n",
            ),
            encoding="utf-8",
        )
        self.commit_all("feat(sample): update sample behavior")
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "qwen2.5-coder:1.5b",
            "ok": True,
            "output": "bounded patch plan",
        }
        search_payload = {
            "schema": "semantic_find.quick.v1",
            "query": "sample helper minimal patch",
            "count": 1,
            "results": [
                {
                    "kind": "symbol",
                    "path": "src/sample.py",
                    "name": "alpha",
                    "line_start": 3,
                    "line_end": 7,
                    "score": 6.0,
                }
            ],
        }
        snippet_payload = {
            "path": "src/sample.py",
            "start_line": 1,
            "end_line": 8,
            "content": "def alpha(x):\n    return x + 1\n",
        }
        prompt = (
            "Implement a careful but bounded update for src/sample.py that preserves behavior, "
            "stays repository-grounded, and only changes the minimum necessary region. "
        ) * 5
        with patch.object(self.server, "semantic_find", return_value=search_payload), patch.object(
            self.server, "read_snippet", return_value=snippet_payload
        ), patch.object(self.server, "local_infer", return_value=infer_payload):
            out = self.server.task_router(
                mode="task",
                prompt=prompt,
                backend="fallback",
                output_profile="normal",
            )
        self.assertEqual(out["routing"]["selected_by"], "auto:repo_specialist_grounded")
        self.assertGreaterEqual(out["repo_memory"]["entry_count"], 1)
        self.assertGreaterEqual(out["skill_pack"]["module_count"], 1)
        self.assertIn("history=", out["context_packet"]["context"])
        self.assertIn("skills=", out["context_packet"]["context"])
        self.assertEqual(out["retrieval"]["telemetry"]["selected_items"], 1)

    def test_task_router_defaults_to_task_mode(self):
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "deepseek-r1:1.5b",
            "ok": True,
            "output": "security findings",
        }
        with patch.object(self.server, "local_infer", return_value=infer_payload) as linf:
            out = self.server.task_router(
                prompt="Review auth middleware for security vulnerabilities and secret exposure.",
                backend="fallback",
                output_profile="normal",
            )
        self.assertEqual(out["schema"], "task_router.task.v1")
        self.assertEqual(out["classification"]["route"], "security")
        self.assertEqual(linf.call_args.kwargs["task"], "security")

    def test_task_router_task_honors_task_override_in_compact_mode(self):
        infer_payload = {
            "schema": "local_infer.compact.v1",
            "backend": "fallback",
            "model": "granite3.3:2b",
            "ok": True,
            "output": "review findings",
        }
        with patch.object(self.server, "local_infer", return_value=infer_payload) as linf:
            out = self.server.task_router(
                mode="task",
                prompt="Implement helper for parser cleanup.",
                task="review",
                backend="fallback",
                output_profile="compact",
            )
        self.assertEqual(out["schema"], "task_router.task.compact.v1")
        self.assertEqual(out["route"], "review")
        self.assertEqual(out["model"], "granite3.3:2b")
        self.assertTrue(out["ok"])
        self.assertEqual(linf.call_args.kwargs["task"], "review")
        self.assertEqual(linf.call_args.kwargs["model"], "granite3.3:2b")

    def test_task_router_task_auto_selects_micro_coding_model_for_short_prompt(self):
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "qwen2.5-coder:1.5b",
            "ok": True,
            "output": "slugify helper",
        }
        with patch.object(self.server, "local_infer", return_value=infer_payload) as linf:
            out = self.server.task_router(
                mode="task",
                prompt="Implement a small Python function that slugifies text.",
                backend="fallback",
                output_profile="normal",
            )
        self.assertEqual(out["classification"]["route"], "coding")
        self.assertEqual(out["routing"]["selected_model"], "qwen2.5-coder:1.5b")
        self.assertEqual(out["routing"]["selected_by"], "auto:short_coding_prompt")
        self.assertEqual(linf.call_args.kwargs["task"], "coding")
        self.assertEqual(linf.call_args.kwargs["model"], "qwen2.5-coder:1.5b")

    def test_task_router_task_respects_requested_max_tokens(self):
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "deepseek-r1:1.5b",
            "ok": True,
            "output": "security findings",
        }
        with patch.object(self.server, "local_infer", return_value=infer_payload) as linf:
            out = self.server.task_router(
                mode="task",
                prompt="Review auth middleware for security vulnerabilities and secret exposure.",
                backend="fallback",
                max_tokens=64,
                output_profile="normal",
            )
        self.assertEqual(linf.call_args.kwargs["max_tokens"], 64)
        self.assertEqual(out["cost_plan"]["requested_max_tokens"], 64)
        self.assertEqual(out["cost_plan"]["effective_max_tokens"], 64)

    def test_skill_pack_lint_curated_pack_is_small_and_unique(self):
        out = self.server.skill_pack_lint(route="coding")
        self.assertEqual(out["schema"], "skill_pack_lint.v1")
        self.assertTrue(out["lint"]["ok"])
        self.assertLessEqual(out["lint"]["module_count"], 3)
        self.assertEqual(out["lint"]["duplicates"], [])


    def test_task_router_task_reads_memory_and_encodes_session_and_memory(self):
        self.server.memory_summary_upsert(
            namespace="task/route/security",
            focus="recent_activity",
            summary="prior route summary",
        )
        self.server.memory_decision_record(
            namespace="task/session/abc",
            topic="response_style",
            decision="be terse",
            decided_by="human",
        )
        facts_path = self.repo_path / ".codebase-tooling-mcp" / "memory" / "workspace_facts.json"
        facts_path.parent.mkdir(parents=True, exist_ok=True)
        facts_path.write_text(
            json.dumps(
                {
                    "generated_at": "cached",
                    "is_git_repo": True,
                    "file_count": 11,
                    "top_extensions": [{"extension": ".py", "count": 3}],
                    "has_tests_dir": True,
                    "has_readme": True,
                    "default_output_profile": "compact",
                }
            ),
            encoding="utf-8",
        )
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "deepseek-r1:1.5b",
            "ok": True,
            "output": "security findings",
        }
        with patch.object(self.server, "local_infer", return_value=infer_payload):
            out = self.server.task_router(
                mode="task",
                prompt="Review auth middleware for security vulnerabilities and secret exposure.",
                backend="fallback",
                output_profile="normal",
                memory_session="abc",
            )
        packet = json.loads(out["encoding"]["encoded_prompt"])
        self.assertEqual(packet["s"], "abc")
        self.assertIn("m", packet)
        self.assertLessEqual(len(packet["m"]), 900)
        self.assertIn("prior route summary", packet["m"])
        self.assertIn("response_style=be terse", packet["m"])
        self.assertIn("files=11", packet["m"])
        self.assertEqual(out["memory"]["session_namespace"], "task/session/abc")
        self.assertEqual(out["memory"]["route_namespace"], "task/route/security")
        self.assertEqual(out["intent"]["deliverable"], "findings")
        self.assertEqual(out["cost_plan"]["effort"], "medium")

    def test_task_router_task_success_persists_session_entry_and_route_summary_with_evidence(self):
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "deepseek-r1:1.5b",
            "ok": True,
            "output": "security findings",
            "result_id": "infer-123",
        }
        with patch.object(self.server, "local_infer", return_value=infer_payload):
            out = self.server.task_router(
                mode="task",
                prompt="Review src/sample.py for security vulnerabilities and secret exposure.",
                backend="fallback",
                output_profile="normal",
                memory_session="persist",
            )
        payload = self.server._memory_load()
        session_entries = [
            row for row in payload["entries"] if row.get("namespace") == "task/session/persist"
        ]
        self.assertEqual(len(session_entries), 1)
        value = session_entries[0]["value"]
        self.assertEqual(value["route"], "security")
        self.assertEqual(value["model"], "deepseek-r1:1.5b")
        self.assertEqual(value["backend"], "fallback")
        self.assertTrue(value["ok"])
        self.assertEqual(value["result_id"], "infer-123")
        route_summaries = [
            row
            for row in payload["summaries"]
            if row.get("namespace") == "task/route/security"
            and row.get("focus") == "recent_activity"
        ]
        self.assertEqual(len(route_summaries), 1)
        self.assertIn("security findings", route_summaries[0]["summary"])
        self.assertTrue(out["memory"]["session_write"]["written"])
        self.assertTrue(out["memory"]["route_summary_write"]["written"])
        self.assertGreaterEqual(out["memory"]["evidence_count"], 1)

    def test_task_router_task_persists_explicit_session_without_grounded_evidence(self):
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "deepseek-r1:1.5b",
            "ok": True,
            "output": "ungrounded security findings",
        }
        with patch.object(self.server, "local_infer", return_value=infer_payload):
            out = self.server.task_router(
                mode="task",
                prompt="Review auth middleware for security vulnerabilities and secret exposure.",
                backend="fallback",
                output_profile="normal",
                memory_session="no-evidence",
            )
        payload = self.server._memory_load()
        session_entries = [
            row for row in payload["entries"] if row.get("namespace") == "task/session/no-evidence"
        ]
        self.assertEqual(len(session_entries), 1)
        self.assertTrue(out["memory"]["session_write"]["written"])
        self.assertFalse(out["memory"]["route_summary_write"]["written"])
        self.assertEqual(out["memory"]["route_summary_write"]["reason"], "no_evidence")
        self.assertGreaterEqual(out["memory"]["session_evidence_count"], 1)

    def test_task_router_task_failure_records_failure_and_session_context_with_evidence(self):
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "deepseek-r1:1.5b",
            "ok": True,
            "output": "",
        }
        with patch.object(self.server, "local_infer", return_value=infer_payload):
            out = self.server.task_router(
                mode="task",
                prompt="Review src/sample.py for security vulnerabilities and secret exposure.",
                backend="fallback",
                output_profile="normal",
                memory_session="failure",
            )
        payload = self.server._memory_load()
        session_entries = [
            row for row in payload["entries"] if row.get("namespace") == "task/session/failure"
        ]
        self.assertEqual(len(session_entries), 1)
        self.assertFalse(session_entries[0]["value"]["ok"])
        self.assertEqual(session_entries[0]["value"]["response_summary"], "empty output")
        failures = self.server._failure_memory_load()["entries"]
        task_failures = [row for row in failures if row.get("category") == "task_router.task"]
        self.assertEqual(len(task_failures), 1)
        self.assertIn("empty output", task_failures[0]["stderr"])
        self.assertTrue(out["memory"]["failure_recorded"])

    def test_task_router_task_memory_context_is_summary_first_and_capped(self):
        self.server.memory_summary_upsert(
            namespace="task/route/security",
            focus="recent_activity",
            summary=("route-summary " * 80).strip(),
        )
        self.server.memory_upsert(
            namespace="task/route/security",
            key="raw-route",
            value={"detail": "raw route fallback should not appear"},
        )
        self.server.memory_summary_upsert(
            namespace="task/session/abc",
            focus="session",
            summary=("session-summary " * 80).strip(),
        )
        self.server.memory_upsert(
            namespace="task/session/abc",
            key="raw-session",
            value={"detail": "raw session fallback should not appear"},
        )
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "deepseek-r1:1.5b",
            "ok": True,
            "output": "security findings",
        }
        with patch.object(self.server, "local_infer", return_value=infer_payload):
            out = self.server.task_router(
                mode="task",
                prompt="Review auth middleware for security vulnerabilities and secret exposure.",
                backend="fallback",
                output_profile="normal",
                memory_session="abc",
            )
        packet = json.loads(out["encoding"]["encoded_prompt"])
        self.assertLessEqual(len(packet["m"]), 900)
        self.assertIn("recent_activity=route-summary", packet["m"])
        self.assertIn("session=session-summary", packet["m"])
        self.assertNotIn("raw route fallback should not appear", packet["m"])
        self.assertNotIn("raw session fallback should not appear", packet["m"])

    def test_autocomplete_fallback(self):
        out = self.server.autocomplete(
            prefix="def handler():",
            backend="fallback",
            output_profile="compact",
            max_tokens=16,
        )
        self.assertEqual(out["schema"], "autocomplete.compact.v1")
        self.assertEqual(out["backend"], "heuristic_fallback")
        self.assertTrue(out["ok"])
        self.assertTrue(out["degraded"])
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
        report = self.repo_path / ".codebase-tooling-mcp" / "reports" / "SAMPLE.txt"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("ok\n", encoding="utf-8")

        out = self.server.required_tool_chain(
            required_tools=["tool_a", "tool_b"],
            required_artifacts=[".codebase-tooling-mcp/reports/SAMPLE.txt"],
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
        self.assertEqual(contract["resource_uri_count"], 0)
        self.assertEqual(contract["resource_template_count"], 0)
        self.assertEqual(contract["unexpected_resources"], [])

        budget_set = self.server.cost_budget_enforcer(mode="set", max_tokens=100, max_calls=10, max_seconds=60)
        self.assertEqual(budget_set["schema"], "cost_budget_enforcer.v1")
        budget_record = self.server.cost_budget_enforcer(mode="record", used_tokens=10, used_calls=1, used_seconds=5)
        self.assertTrue(budget_record["ok"])

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

    def test_execution_replay_summarize_and_diagnose_failure(self):
        replay = self.server.execution_replay(mode="start")
        rid = replay["replay_id"]
        self.server.execution_replay(
            mode="log",
            replay_id=rid,
            event={"stage": "start", "prompt_summary": "Update src/sample.py"},
        )
        self.server.execution_replay(
            mode="log",
            replay_id=rid,
            event={
                "stage": "execution",
                "changed_paths": ["src/sample.py"],
                "verification": {"blocked": True, "reason": "diff expands scope"},
            },
        )
        self.server.execution_replay(
            mode="log",
            replay_id=rid,
            event={"stage": "finish", "ok": False, "stopped_reason": "verifier_disagreement"},
        )
        self.server.execution_replay(mode="finish", replay_id=rid)
        summary = self.server.execution_replay(mode="summarize", replay_id=rid)
        diagnosis = self.server.execution_replay(mode="diagnose", replay_id=rid)
        self.assertEqual(summary["schema"], "execution_replay.summary.v1")
        self.assertEqual(summary["changed_paths"], ["src/sample.py"])
        self.assertFalse(summary["ok"])
        self.assertEqual(diagnosis["schema"], "execution_replay.diagnosis.v1")
        self.assertTrue(diagnosis["failed"])
        self.assertEqual(diagnosis["failure_stage"], "execution")
        self.assertIn("scope", diagnosis["reason"])

    def test_execution_replay_summarize_defaults_to_not_ok_without_finish_event(self):
        replay = self.server.execution_replay(mode="start")
        rid = replay["replay_id"]
        self.server.execution_replay(
            mode="log",
            replay_id=rid,
            event={"stage": "start", "prompt_summary": "Inspect src/sample.py"},
        )
        self.server.execution_replay(
            mode="log",
            replay_id=rid,
            event={"stage": "execution", "changed_paths": ["src/sample.py"]},
        )
        self.server.execution_replay(mode="finish", replay_id=rid)

        summary = self.server.execution_replay(mode="summarize", replay_id=rid)

        self.assertEqual(summary["status"], "closed")
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["changed_paths"], ["src/sample.py"])

    def test_task_router_coding_modes_and_validation(self):
        with self.assertRaises(ValueError):
            self.server.task_router(mode="not_a_mode")

        with patch.object(
            self.server,
            "local_infer",
            return_value={"schema": "local_infer.v1", "model": "qwen2.5-coder:3b", "ok": True},
        ), patch.object(
            self.server,
            "_coding_checks",
            return_value={"schema": "coding_checks.v1", "ok": True, "steps": []},
        ), patch.object(
            self.server,
            "CODING_VENV_PYTHON",
            sys.executable,
        ):
            out = self.server.task_router(
                mode="coding_infer",
                prompt="write function",
                run_checks=True,
                sandbox_mode="shared",
            )
        self.assertEqual(out["schema"], "task_router.coding_infer.v1")
        self.assertTrue(out["check_requested"])
        self.assertIn("checks", out)
        self.assertIn("routing", out)
        self.assertIn("sandbox", out)
        self.assertIn("stdout_stream", out)
        self.assertIn("stderr_stream", out)

        with patch.object(
            self.server,
            "CODING_VENV_PYTHON",
            str(self.repo_path / "does-not-exist" / "python"),
        ):
            with self.assertRaises(FileNotFoundError):
                self.server.task_router(
                    mode="coding_check",
                    check_profile="lint",
                    check_target="src/sample.py",
                )

    def test_task_router_coding_infer_supports_micro_coding_task_hint(self):
        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "qwen2.5-coder:1.5b",
            "ok": True,
            "output": "helper",
        }
        sandbox_payload = {"venv_python": sys.executable, "sandbox_id": "shared"}
        with patch.object(self.server, "local_infer", return_value=infer_payload) as linf, patch.object(
            self.server, "_coding_sandbox_prepare", return_value=sandbox_payload
        ):
            out = self.server.task_router(
                mode="coding_infer",
                task="micro_coding",
                prompt="Implement helper",
                backend="fallback",
            )
        self.assertEqual(out["routing"]["selected_model"], "qwen2.5-coder:1.5b")
        self.assertEqual(out["routing"]["selected_by"], "task_hint:micro_coding")
        self.assertEqual(linf.call_args.kwargs["model"], "qwen2.5-coder:1.5b")

    def test_task_router_coding_check_and_pip_include_stream_fields(self):
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
            out = self.server.task_router(
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
            out_pip = self.server.task_router(
                mode="coding_pip",
                packages=["pytest"],
            )
        self.assertIn("stdout_stream", out_pip)
        self.assertIn("stderr_stream", out_pip)
        self.assertGreaterEqual(len(out_pip["stdout_stream"]), 1)

    def test_task_router_coding_sandbox_lifecycle(self):
        base_venv = self.repo_path / ".codebase-tooling-mcp" / "base-venv"
        subprocess.run(["python", "-m", "venv", str(base_venv)], check=True)
        python_bin = base_venv / "bin" / "python"

        with patch.object(self.server, "CODING_VENV_PYTHON", str(python_bin)):
            created = self.server.task_router(
                mode="coding_sandbox",
                sandbox_action="create",
                sandbox_id="sbox-test",
            )
            self.assertEqual(created["schema"], "coding_sandbox.v1")
            self.assertEqual(created["action"], "create")
            self.assertEqual(created["sandbox_id"], "sbox-test")

            listed = self.server.task_router(mode="coding_sandbox", sandbox_action="list")
            ids = {row["sandbox_id"] for row in listed["items"]}
            self.assertIn("sbox-test", ids)

            deleted = self.server.task_router(
                mode="coding_sandbox",
                sandbox_action="delete",
                sandbox_id="sbox-test",
            )
            self.assertTrue(deleted["deleted"])

    def test_task_router_guided_edit_validates_arguments(self):
        with self.assertRaises(ValueError):
            self.server.task_router(mode="guided_edit", prompt="update src/sample.py", max_steps=0)

        with self.assertRaises(ValueError):
            self.server.task_router(
                mode="guided_edit",
                prompt="update src/sample.py",
                target_paths=["src/sample.py"],
                validation_profile="full",
            )

    def test_task_router_guided_edit_applies_single_step_and_validates(self):
        before = (self.repo_path / "src" / "sample.py").read_text(encoding="utf-8")
        after = before.replace(
            "def alpha(x):\n    return x + 1\n",
            "def alpha(x):\n    # alpha increments the input\n    return x + 1\n",
        )
        replacement_output = (
            "def alpha(x):\n"
            "    # alpha increments the input\n"
            "    return x + 1"
        )
        planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "add a short behavior comment",
                "rationale": "keep the change minimal",
                "validation_scope": "quick",
            }
        )
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": planner_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": replacement_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "def alpha(x):\n    return x + 1",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "Target snippet:\ndef alpha(x):\n    return x + 1",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"verdict": "agree", "confidence": 0.93, "reason": "diff matches the bounded plan"}
                    ),
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a short behavior comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="fallback",
                validation_profile="quick",
            )
        self.assertEqual(out["schema"], "task_router.guided_edit.v1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["step_count"], 1)
        self.assertEqual(out["stopped_reason"], "single_step_complete")
        self.assertTrue(out["snapshot_id"])
        self.assertTrue(out["replay_id"])
        self.assertIn(
            "# alpha increments the input",
            (self.repo_path / "src" / "sample.py").read_text(encoding="utf-8"),
        )
        self.assertTrue(out["final_validation"]["ok"])
        self.assertIn("tests/test_sample.py", out["final_validation"]["selected_tests"])
        self.assertFalse(out["steps"][0]["rolled_back"])
        self.assertEqual(out["steps"][0]["execution"]["isolation"]["backend"], "git_worktree")
        self.assertTrue(out["steps"][0]["execution"]["materialized"])
        self.assertEqual(out["steps"][0]["execution"]["attempt_count"], 3)
        self.assertEqual(len(out["steps"][0]["execution"]["candidates"]), 3)
        self.assertEqual(out["steps"][0]["execution"]["selected_candidate"]["strategy"], "initial")
        self.assertTrue(out["memory_write"]["written"])
        self.assertTrue(out["memory_write"]["experience_write"]["written"])
        self.assertEqual(out["replay_summary"]["schema"], "execution_replay.summary.v1")
        self.assertEqual(out["replay_diagnosis"]["schema"], "execution_replay.diagnosis.v1")
        self.assertIn("candidate_selection", out["replay_summary"]["stages"])
        self.assertEqual(out["workflow_benchmark"]["run"]["candidate_count"], 3)
        self.assertEqual(out["workflow_benchmark"]["run"]["selected_candidate_index"], 1)
        self.assertGreaterEqual(out["repo_memory"]["entry_count"], 1)
        self.assertGreaterEqual(out["skill_pack"]["module_count"], 1)
        self.assertEqual(out["state_machine"]["states"], ["start", "plan", "execute", "validate", "finish"])
        self.assertEqual(out["state_machine"]["current_state"], "finish")
        self.assertEqual(out["state_machine"]["terminal_reason"], "single_step_complete")

    def test_task_router_guided_edit_repairs_invalid_planner_output(self):
        replacement_output = (
            "def alpha(x):\n"
            "    # alpha increments the input\n"
            "    return x + 1"
        )
        repaired_planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "add a short behavior comment",
                "rationale": "keep the change minimal",
                "validation_scope": "quick",
            }
        )
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "I would update src/sample.py with a tiny comment.",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": repaired_planner_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": replacement_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "def alpha(x):\n    return x + 1",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "Target snippet:\ndef alpha(x):\n    return x + 1",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"verdict": "agree", "confidence": 0.91, "reason": "repaired plan matches the diff"}
                    ),
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a short behavior comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="endpoint",
                validation_profile="quick",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["steps"][0]["planner_attempts"]), 2)
        self.assertEqual(out["steps"][0]["planner_attempts"][1]["kind"], "repair")
        self.assertTrue(out["steps"][0]["planner_attempts"][1]["ok"])
        self.assertIsNotNone(out["steps"][0]["repair_result"])
        self.assertEqual(out["steps"][0]["execution"]["isolation"]["backend"], "git_worktree")
        self.assertTrue(out["steps"][0]["execution"]["materialized"])
        self.assertEqual(out["steps"][0]["execution"]["attempt_count"], 3)

    def test_task_router_guided_edit_repairs_prior_violation_from_planner(self):
        planner_output = json.dumps(
            {
                "action_type": "add_test",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "add a short behavior comment",
                "rationale": "wrongly choose tests for a comment request",
                "validation_scope": "quick",
            }
        )
        repaired_planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "add a short behavior comment",
                "rationale": "keep the change minimal",
                "validation_scope": "quick",
            }
        )
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": planner_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": repaired_planner_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "def alpha(x):\n    # alpha increments the input\n    return x + 1",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "def alpha(x):\n    return x + 1",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": "Target snippet:\ndef alpha(x):\n    return x + 1",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"verdict": "agree", "confidence": 0.92, "reason": "repaired planner action matches the request"}
                    ),
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a short behavior comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="endpoint",
                validation_profile="quick",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["steps"][0]["planner_attempts"]), 2)
        self.assertEqual(out["steps"][0]["planner_attempts"][1]["kind"], "repair")
        self.assertEqual(out["steps"][0]["plan"]["action_type"], "replace_region")
        self.assertEqual(out["steps"][0]["execution"]["selected_candidate"]["strategy"], "initial")

    def test_task_router_guided_edit_repairs_invalid_edit_output(self):
        before = (self.repo_path / "src" / "sample.py").read_text(encoding="utf-8")
        repaired_output = (
            "def alpha(x):\n"
            "    # alpha increments the input\n"
            "    return x + 1"
        )
        planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "add a short behavior comment",
                "rationale": "keep the change minimal",
                "validation_scope": "quick",
            }
        )
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": planner_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "Recent repository history:\n9c5627112a48 Rename task router contract and tooling artifact root",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": "Curated skills:\nChange only the named files or symbols and keep the patch minimal.",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": repaired_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"verdict": "agree", "confidence": 0.94, "reason": "repaired diff matches the request"}
                    ),
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a short behavior comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="endpoint",
                validation_profile="quick",
            )
        self.assertTrue(out["ok"])
        self.assertTrue(out["steps"][0]["execution"]["repair_used"])
        self.assertEqual(out["steps"][0]["execution"]["attempt_count"], 3)
        self.assertIsNotNone(out["steps"][0]["execution"]["repair_result"])
        self.assertEqual(out["steps"][0]["execution"]["selected_candidate"]["strategy"], "repair")

    def test_task_router_guided_edit_rolls_back_on_failed_validation(self):
        before = (self.repo_path / "src" / "sample.py").read_text(encoding="utf-8")
        invalid_replacement_output = (
            "def alpha(x)\n"
            "    return x + 1"
        )
        wrong_behavior_output = (
            "def alpha(x):\n"
            "    return x + 999"
        )
        planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "make a bad edit for rollback coverage",
                "rationale": "exercise validation failure handling",
                "validation_scope": "quick",
            }
        )
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": planner_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": wrong_behavior_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": invalid_replacement_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "Target snippet:\ndef alpha(x):\n    return x + 1",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"verdict": "agree", "confidence": 0.88, "reason": "diff is syntactically scoped but invalid"}
                    ),
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Break src/sample.py to test rollback.",
                target_paths=["src/sample.py"],
                backend="fallback",
                validation_profile="quick",
                rollback_on_failure=True,
                snapshot_before_edit=True,
            )
        self.assertEqual(out["schema"], "task_router.guided_edit.v1")
        self.assertFalse(out["ok"])
        self.assertEqual(out["stopped_reason"], "low_confidence_abstain")
        self.assertEqual(out["abstain_reason"], "validation_abstain")
        self.assertEqual(out["final_validation"]["compile_error_count"], 0)
        self.assertFalse(out["final_validation"]["test_execution"]["ok"])
        self.assertFalse(out["steps"][0]["rolled_back"])
        self.assertEqual(
            (self.repo_path / "src" / "sample.py").read_text(encoding="utf-8"),
            before,
        )
        self.assertTrue(out["memory_write"]["written"])
        self.assertFalse(out["steps"][0]["execution"].get("materialized", False))
        self.assertEqual(out["workflow_benchmark"]["run"]["verifier_false_positive"], 1)
        self.assertTrue(out["verifier_false_positive_write"]["written"])
        self.assertEqual(out["state_machine"]["current_state"], "validate")
        self.assertEqual(out["state_machine"]["terminal_reason"], "low_confidence_abstain")

    def test_task_router_guided_edit_blocks_verifier_disagreement(self):
        planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "attempt an over-broad edit",
                "rationale": "exercise verifier disagreement handling",
                "validation_scope": "standard",
            }
        )
        broad_replacement_output = (
            "def alpha(x):\n"
            "    # over-broad change\n"
            "    return x + 2"
        )
        broader_replacement_output = (
            "def alpha(x):\n"
            "    # another broad change\n"
            "    return x + 3"
        )
        repair_replacement_output = (
            "def alpha(x):\n"
            "    # repaired but still broad\n"
            "    return x + 4"
        )
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": planner_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": broad_replacement_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": broader_replacement_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": repair_replacement_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"winner": "tie", "reason": "both candidates are similarly broad"}
                    ),
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"winner": "tie", "reason": "both candidates are similarly broad"}
                    ),
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"winner": "tie", "reason": "both candidates are similarly broad"}
                    ),
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"verdict": "disagree", "confidence": 0.97, "reason": "repaired diff still expands scope beyond the stated action"}
                    ),
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"verdict": "disagree", "confidence": 0.96, "reason": "alternate diff still expands scope beyond the stated action"}
                    ),
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"verdict": "disagree", "confidence": 0.97, "reason": "repair diff still expands scope beyond the stated action"}
                    ),
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a tiny comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="fallback",
                validation_profile="auto",
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["stopped_reason"], "low_confidence_abstain")
        self.assertEqual(out["abstain_reason"], "verifier_veto_abstain")
        self.assertFalse(out["steps"][0]["execution"]["applied"])
        self.assertTrue(out["steps"][0]["execution"]["verification"]["blocked"])
        self.assertTrue(out["steps"][0]["execution"]["repair_used"])
        self.assertEqual(out["steps"][0]["execution"]["attempt_count"], 3)
        self.assertEqual(out["state_machine"]["current_state"], "execute")
        self.assertEqual(out["state_machine"]["terminal_reason"], "low_confidence_abstain")

    def test_task_router_guided_edit_returns_structured_failure_for_invalid_planner_output(self):
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "Make a tiny edit in src/sample.py.",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": "still not json",
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a short behavior comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="endpoint",
                validation_profile="quick",
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["stopped_reason"], "planner_invalid")
        self.assertEqual(out["failure_diagnosis"]["failure_stage"], "plan")
        self.assertEqual(out["failure_diagnosis"]["reason"], "planner_invalid")
        self.assertEqual(out["state_machine"]["current_state"], "plan")
        self.assertEqual(out["state_machine"]["terminal_reason"], "planner_invalid")

    def test_task_router_guided_edit_returns_structured_failure_for_invalid_edit_output(self):
        planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "add a short behavior comment",
                "rationale": "keep the change minimal",
                "validation_scope": "quick",
            }
        )
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": planner_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "Target snippet:\ndef alpha(x):\n    return x + 1",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": "Curated skills:\nChange only the named files or symbols and keep the patch minimal.",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": "Recent repository history:\n9c5627112a48 Rename task router contract and tooling artifact root",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": "still invalid",
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a short behavior comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="endpoint",
                validation_profile="quick",
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["stopped_reason"], "low_confidence_abstain")
        self.assertEqual(out["abstain_reason"], "low_confidence_abstain")
        self.assertEqual(out["failure_diagnosis"]["failure_stage"], "execute")
        self.assertEqual(out["failure_diagnosis"]["reason"], "low_confidence_abstain")
        self.assertEqual(out["steps"][0]["execution"]["attempt_count"], 4)
        self.assertTrue(out["steps"][0]["execution"]["repair_used"])
        self.assertEqual(out["state_machine"]["current_state"], "execute")
        self.assertEqual(out["state_machine"]["terminal_reason"], "low_confidence_abstain")

    def test_guided_edit_parse_verification_accepts_word_confidence(self):
        parsed = self.server._guided_edit_parse_verification(
            json.dumps(
                {
                    "verdict": "disagree",
                    "confidence": "high",
                    "reason": "scope mismatch",
                }
            )
        )
        self.assertEqual(parsed["verdict"], "disagree")
        self.assertEqual(parsed["confidence"], 0.9)

    def test_guided_edit_output_to_diff_rejects_large_structured_expansion(self):
        region = self.server._guided_edit_target_region("src/sample.py", 3, 4, context_before=0, context_after=0)
        with self.assertRaises(ValueError):
            self.server._guided_edit_output_to_diff(
                raw_output=(
                    "def alpha(x):\n"
                    "    # line one\n"
                    "    # line two\n"
                    "    # line three\n"
                    "    return x + 1"
                ),
                target_region=region,
                allow_raw_diff=False,
                max_extra_lines=1,
            )

    def test_task_router_guided_edit_blocks_unrelated_update_docs_diff(self):
        other_path = self.repo_path / "tests" / "test_smoke.py"
        before = other_path.read_text(encoding="utf-8")
        after = before.replace(
            "        self.assertTrue(True)\n",
            "        self.assertTrue(True)\n        # unrelated change\n",
        )
        other_path.write_text(after, encoding="utf-8")
        diff_output = self.git("diff", "--", "tests/test_smoke.py").stdout
        other_path.write_text(before, encoding="utf-8")
        planner_output = json.dumps(
            {
                "action_type": "update_docs",
                "target": {"path": "src/sample.py", "start_line": 1, "end_line": 4},
                "goal": "update docs for the sample behavior",
                "rationale": "exercise out-of-scope diff blocking",
                "validation_scope": "standard",
            }
        )
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": planner_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": diff_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": "Curated skills:\nChange only the named files or symbols and keep the patch minimal.",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": "Recent repository history:\n9c5627112a48 Rename task router contract and tooling artifact root",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": "still invalid",
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Update docs for src/sample.py.",
                target_paths=["src/sample.py"],
                backend="fallback",
                validation_profile="auto",
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["stopped_reason"], "low_confidence_abstain")
        self.assertEqual(out["abstain_reason"], "low_confidence_abstain")
        self.assertFalse(out["steps"][0]["execution"]["watchdog"]["blocked"])
        self.assertEqual(out["steps"][0]["execution"]["stopped_reason_hint"], "low_confidence_abstain")
        self.assertEqual(out["state_machine"]["current_state"], "execute")
        self.assertEqual(out["state_machine"]["terminal_reason"], "low_confidence_abstain")

    def test_task_router_guided_edit_planner_stop_uses_normalized_state_machine(self):
        planner_output = json.dumps(
            {
                "action_type": "stop",
                "target": {"path": "src/sample.py", "start_line": 1, "end_line": 4},
                "goal": "no edit required",
                "rationale": "the request is already satisfied",
                "validation_scope": "quick",
            }
        )
        with patch.object(
            self.server,
            "local_infer",
            return_value={
                "schema": "local_infer.v1",
                "backend": "fallback",
                "model": "qwen2.5-coder:3b",
                "ok": True,
                "output": planner_output,
            },
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Leave src/sample.py unchanged.",
                target_paths=["src/sample.py"],
                backend="fallback",
                validation_profile="quick",
            )
        self.assertEqual(out["stopped_reason"], "planner_stop")
        self.assertEqual(out["state_machine"]["states"], ["start", "plan", "execute", "validate", "finish"])
        self.assertEqual(out["state_machine"]["current_state"], "plan")
        self.assertEqual(out["state_machine"]["terminal_reason"], "planner_stop")

    def test_task_router_guided_edit_ranks_candidates_and_selects_later_success(self):
        planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "add a short behavior comment",
                "rationale": "exercise candidate ranking",
                "validation_scope": "quick",
            }
        )
        candidate_one = (
            "def beta(y):\n"
            "    return y - 1"
        )
        candidate_two = (
            "def alpha(x):\n"
            "    # alpha increments the input\n"
            "    return x + 2"
        )
        candidate_three = (
            "def alpha(x):\n"
            "    # alpha increments the input\n"
            "    # kept intentionally verbose for ranking coverage\n"
            "    return x + 1"
        )
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {"schema": "local_infer.v1", "backend": "fallback", "model": "qwen", "ok": True, "output": planner_output},
                {"schema": "local_infer.v1", "backend": "fallback", "model": "qwen", "ok": True, "output": candidate_one},
                {"schema": "local_infer.v1", "backend": "fallback", "model": "qwen", "ok": True, "output": candidate_two},
                {"schema": "local_infer.v1", "backend": "fallback", "model": "qwen", "ok": True, "output": candidate_three},
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps(
                        {"winner": "tie", "reason": "both candidates are similarly scoped before verification"}
                    ),
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps({"verdict": "disagree", "confidence": 0.9, "reason": "candidate changes behavior"}),
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps({"verdict": "agree", "confidence": 0.93, "reason": "candidate matches the bounded request"}),
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a short behavior comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="fallback",
                validation_profile="quick",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["steps"][0]["execution"]["selected_candidate"]["index"], 3)
        self.assertEqual(out["steps"][0]["execution"]["candidates"][0]["stop_reason"], "acceptance_failed")
        self.assertEqual(out["steps"][0]["execution"]["candidates"][1]["stop_reason"], "verifier_disagreement")
        self.assertEqual(out["steps"][0]["execution"]["candidates"][2]["stop_reason"], "selected")

    def test_task_router_guided_edit_pairwise_prefers_better_candidate(self):
        planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "add a short behavior comment",
                "rationale": "exercise pairwise ranking",
                "validation_scope": "quick",
            }
        )
        candidate_one = (
            "def alpha(x):\n"
            "    # alpha increments the input in a somewhat verbose way\n"
            "    return x + 1"
        )
        candidate_two = (
            "def alpha(x):\n"
            "    # alpha increments the input\n"
            "    return x + 1"
        )
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {"schema": "local_infer.v1", "backend": "fallback", "model": "qwen", "ok": True, "output": planner_output},
                {"schema": "local_infer.v1", "backend": "fallback", "model": "qwen", "ok": True, "output": candidate_one},
                {"schema": "local_infer.v1", "backend": "fallback", "model": "qwen", "ok": True, "output": candidate_two},
                {"schema": "local_infer.v1", "backend": "fallback", "model": "qwen", "ok": True, "output": "Target snippet:\ndef alpha(x):\n    return x + 1"},
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps({"winner": "right", "reason": "right is the smaller valid edit"}),
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": json.dumps({"verdict": "agree", "confidence": 0.93, "reason": "candidate matches the bounded request"}),
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a short behavior comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="fallback",
                validation_profile="quick",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["steps"][0]["execution"]["selected_candidate"]["index"], 2)
        self.assertEqual(out["workflow_benchmark"]["run"]["selected_candidate_index"], 2)
        self.assertEqual(out["steps"][0]["execution"]["candidates"][0]["pairwise_score"], -1)
        self.assertEqual(out["steps"][0]["execution"]["candidates"][1]["pairwise_score"], 1)

    def test_task_router_guided_edit_verifier_uses_review_route_even_with_explicit_model(self):
        planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "add a short behavior comment",
                "rationale": "capture verifier routing",
                "validation_scope": "quick",
            }
        )
        verifier_models = []

        def fake_infer(*, prompt, model, **kwargs):
            if prompt.startswith("Plan exactly one bounded repository edit step."):
                return {"schema": "local_infer.v1", "backend": "fallback", "model": model, "ok": True, "output": planner_output}
            if prompt.startswith("Apply exactly one bounded edit to the target region."):
                if "Strategy: Produce the highest-confidence minimal bounded edit." in prompt:
                    return {
                        "schema": "local_infer.v1",
                        "backend": "fallback",
                        "model": model,
                        "ok": True,
                        "output": "def alpha(x):\n    # alpha increments the input\n    return x + 1",
                    }
                return {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": model,
                    "ok": True,
                    "output": "Target snippet:\ndef alpha(x):\n    return x + 1",
                }
            if prompt.startswith("Repair the bounded edit output for the target region."):
                return {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": model,
                    "ok": True,
                    "output": "Target snippet:\ndef alpha(x):\n    return x + 1",
                }
            verifier_models.append(model)
            return {
                "schema": "local_infer.v1",
                "backend": "fallback",
                "model": model,
                "ok": True,
                "output": json.dumps({"verdict": "agree", "confidence": 0.9, "reason": "candidate matches the request"}),
            }

        with patch.object(self.server, "local_infer", side_effect=fake_infer):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a short behavior comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="fallback",
                model="qwen2.5-coder:1.5b",
                validation_profile="quick",
            )
        self.assertTrue(out["ok"])
        self.assertTrue(verifier_models)
        self.assertTrue(all(model == "granite3.3:2b" for model in verifier_models))

    def test_guided_edit_output_to_diff_accepts_micro_edit(self):
        region = self.server._guided_edit_target_region("src/sample.py", 3, 4, context_before=0, context_after=0)
        parsed = self.server._guided_edit_output_to_diff(
            raw_output=json.dumps(
                {
                    "op": "insert_before",
                    "anchor": "return x + 1",
                    "text": "    # alpha increments the input",
                }
            ),
            target_region=region,
            allow_raw_diff=False,
            allow_micro_edit=True,
            max_extra_lines=2,
        )
        self.assertEqual(parsed["mode"], "micro_edit")
        self.assertIn("# alpha increments the input", parsed["replacement_text"])
        self.assertIn("src/sample.py", parsed["diff_text"])

    def test_guided_edit_acceptance_action_specific_postconditions(self):
        doc_region = {
            "path": "README.md",
            "original_text": "# Demo\nAlpha behavior\n",
            "original_lines": ["# Demo\n", "Alpha behavior\n"],
            "start_line": 1,
            "end_line": 2,
            "region_text": "# Demo\nAlpha behavior\n",
            "context_before_text": "",
            "context_after_text": "",
        }
        doc_diff = self.server._guided_edit_build_local_diff(
            target_region=doc_region,
            replacement_text="# Demo\nBehavior overview\n",
            max_extra_lines=2,
        )
        update_docs_acceptance = self.server._guided_edit_acceptance(
            prompt='Update README.md and include "Alpha behavior".',
            action={
                "action_type": "update_docs",
                "goal": "include Alpha behavior",
                "rationale": "docs coverage",
                "target": {"path": "README.md", "start_line": 1, "end_line": 2},
                "postconditions": {},
            },
            target_region=doc_region,
            parsed_edit={"mode": "replacement_text", "replacement_text": "# Demo\nBehavior overview\n", "diff_text": doc_diff},
            allowed_paths=["README.md"],
        )
        self.assertFalse(update_docs_acceptance["ok"])
        self.assertIn("update_docs_missing_required_phrase", update_docs_acceptance["hard_failures"])

        add_test_acceptance = self.server._guided_edit_acceptance(
            prompt="Add a regression test.",
            action={
                "action_type": "add_test",
                "goal": "add regression test",
                "rationale": "tests only",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "postconditions": {},
            },
            target_region=self.server._guided_edit_target_region("src/sample.py", 3, 4, context_before=0, context_after=0),
            parsed_edit={
                "mode": "raw_diff",
                "diff_text": (
                    "--- a/src/sample.py\n"
                    "+++ b/src/sample.py\n"
                    "@@ -3,2 +3,3 @@\n"
                    " def alpha(x):\n"
                    "+    # not a test\n"
                    "     return x + 1\n"
                ),
            },
            allowed_paths=["src/sample.py"],
        )
        self.assertFalse(add_test_acceptance["ok"])
        self.assertIn("add_test_must_change_test_paths", add_test_acceptance["hard_failures"])

        extract_helper_acceptance = self.server._guided_edit_acceptance(
            prompt="Extract helper helper_alpha from src/sample.py.",
            action={
                "action_type": "extract_helper",
                "goal": "extract helper_alpha",
                "rationale": "extract helper helper_alpha",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4, "symbol": "helper_alpha"},
                "postconditions": {},
            },
            target_region=self.server._guided_edit_target_region("src/sample.py", 3, 4, context_before=0, context_after=0),
            parsed_edit={
                "mode": "raw_diff",
                "diff_text": (
                    "--- a/src/sample.py\n"
                    "+++ b/src/sample.py\n"
                    "@@ -3,2 +3,2 @@\n"
                    " def alpha(x):\n"
                    "-    return x + 1\n"
                    "+    return x + 2\n"
                ),
            },
            allowed_paths=["src/sample.py"],
        )
        self.assertFalse(extract_helper_acceptance["ok"])
        self.assertIn("extract_helper_missing_helper_symbol", extract_helper_acceptance["hard_failures"])

    def test_task_router_guided_edit_records_regression_on_failure(self):
        planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "add a short behavior comment",
                "rationale": "force regression recording",
                "validation_scope": "quick",
            }
        )
        with patch.object(
            self.server,
            "local_infer",
            side_effect=[
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": planner_output,
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "Target snippet:\ndef alpha(x):\n    return x + 1",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "qwen2.5-coder:3b",
                    "ok": True,
                    "output": "Curated skills:\nChange only the named files or symbols and keep the patch minimal.",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": "Recent repository history:\n9c5627112a48 Rename task router contract and tooling artifact root",
                },
                {
                    "schema": "local_infer.v1",
                    "backend": "endpoint",
                    "model": "granite3.3:2b",
                    "ok": True,
                    "output": "still invalid",
                },
            ],
        ):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a short behavior comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="endpoint",
                validation_profile="quick",
            )
        self.assertFalse(out["ok"])
        self.assertTrue(out["regression_write"]["written"])
        regression_path = self.repo_path / ".codebase-tooling-mcp" / "reports" / "GUIDED_EDIT_REGRESSIONS.json"
        self.assertTrue(regression_path.is_file())
        payload = json.loads(regression_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "guided_edit.regressions.v1")
        self.assertEqual(payload["entries"][-1]["target_path"], "src/sample.py")
        self.assertEqual(payload["entries"][-1]["stopped_reason"], "low_confidence_abstain")

    def test_task_router_guided_edit_uses_deterministic_micro_edit_for_exact_anchor(self):
        seen_prompts = []

        def fake_infer(*, prompt, model, **kwargs):
            seen_prompts.append(prompt)
            return {
                "schema": "local_infer.v1",
                "backend": "fallback",
                "model": model,
                "ok": True,
                "output": json.dumps(
                    {
                        "verdict": "agree",
                        "confidence": 0.95,
                        "reason": "deterministic micro edit matches the bounded request",
                    }
                ),
            }

        with patch.object(self.server, "local_infer", side_effect=fake_infer):
            out = self.server.task_router(
                mode="guided_edit",
                prompt='Add this exact line: "    # alpha increments the input" before "return x + 1" in src/sample.py.',
                target_paths=["src/sample.py"],
                backend="fallback",
                validation_profile="quick",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["routing"]["selected_route"], "deterministic_micro")
        self.assertEqual(out["steps"][0]["planner_attempts"][0]["kind"], "deterministic")
        self.assertEqual(out["generation_tier"], 0)
        self.assertFalse(any(prompt.startswith("Plan exactly one bounded repository edit step.") for prompt in seen_prompts))
        self.assertEqual(out["steps"][0]["execution"]["selected_candidate"]["strategy"], "deterministic")
        self.assertEqual(out["steps"][0]["execution"]["selected_candidate"]["generation_tier"], 0)
        self.assertIn(
            "    # alpha increments the input\n    return x + 1\n",
            (self.repo_path / "src" / "sample.py").read_text(encoding="utf-8"),
        )

    def test_task_router_guided_edit_uses_stronger_fallback_only_after_tier1_exhaustion(self):
        planner_output = json.dumps(
            {
                "action_type": "replace_region",
                "target": {"path": "src/sample.py", "start_line": 3, "end_line": 4},
                "goal": "add a short behavior comment",
                "rationale": "exercise stronger fallback routing",
                "validation_scope": "quick",
            }
        )
        models = []

        def fake_infer(*, prompt, model, **kwargs):
            models.append(model)
            if prompt.startswith("Plan exactly one bounded repository edit step."):
                return {"schema": "local_infer.v1", "backend": "fallback", "model": model, "ok": True, "output": planner_output}
            if prompt.startswith("Apply exactly one bounded edit to the target region."):
                return {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": model,
                    "ok": True,
                    "output": "Target snippet:\ndef alpha(x):\n    return x + 1",
                }
            if prompt.startswith("Repair the bounded edit output for the target region."):
                if model == "granite3.3:2b":
                    return {
                        "schema": "local_infer.v1",
                        "backend": "fallback",
                        "model": model,
                        "ok": True,
                        "output": "def alpha(x):\n    # alpha increments the input\n    return x + 1",
                    }
                return {
                    "schema": "local_infer.v1",
                    "backend": "fallback",
                    "model": model,
                    "ok": True,
                    "output": "Recent repository history:\ninvalid",
                }
            return {
                "schema": "local_infer.v1",
                "backend": "fallback",
                "model": model,
                "ok": True,
                "output": json.dumps(
                    {"verdict": "agree", "confidence": 0.92, "reason": "fallback candidate is the first valid edit"}
                ),
            }

        with patch.object(self.server, "local_infer", side_effect=fake_infer):
            out = self.server.task_router(
                mode="guided_edit",
                prompt="Add a short behavior comment to src/sample.py.",
                target_paths=["src/sample.py"],
                backend="fallback",
                validation_profile="quick",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["generation_tier"], 2)
        self.assertEqual(out["steps"][0]["execution"]["selected_candidate"]["strategy"], "fallback")
        self.assertEqual(out["steps"][0]["execution"]["selected_candidate"]["generation_tier"], 2)
        self.assertIn("granite3.3:2b", models)

    def test_guided_edit_replay_benchmark_report_tracks_thresholds(self):
        results = (
            [{"bucket": "tiny_anchored", "applied_success": True, "safe_outcome": True} for _ in range(80)]
            + [{"bucket": "bounded_semantic", "applied_success": True, "safe_outcome": True} for _ in range(70)]
            + [{"bucket": "structural", "applied_success": True, "safe_outcome": True} for _ in range(46)]
            + [{"bucket": "structural", "applied_success": False, "safe_outcome": True} for _ in range(4)]
        )
        report = self.server._guided_edit_replay_benchmark_report(results=results, min_cases=200)
        self.assertEqual(report["schema"], "guided_edit.replay_benchmark.v1")
        self.assertEqual(report["case_count"], 200)
        self.assertTrue(report["thresholds_met"])
        self.assertEqual(report["bucket_metrics"]["tiny_anchored"]["applied_success_rate"], 1.0)
        self.assertEqual(report["bucket_metrics"]["structural"]["applied_success_rate"], 0.92)

    def test_memory_auto_compact_and_usage_stats(self):
        for i in range(10):
            self.server.memory_upsert(
                namespace="compact_demo",
                key=f"k{i}",
                value={"n": i, "payload": "x" * 180},
                ttl_days=30,
            )

        result = self.server.memory_get(
            namespace="compact_demo",
            max_entries=100,
            auto_compact=True,
            compact_threshold_entries=5,
            compact_threshold_chars=1000,
            compact_keep_entries=3,
        )
        self.assertGreaterEqual(result["count"], 10)
        self.assertIn("usage_stats", result)
        self.assertIn("events", result["usage_stats"])
        self.assertTrue(result["auto_compact"]["compacted"])

        compact = self.server.memory_auto_compact(
            namespace="compact_demo",
            threshold_entries=5,
            threshold_chars=1000,
            keep_entries=3,
        )
        self.assertFalse(compact["compacted"])
        payload = self.server._memory_load()
        entries = [row for row in payload["entries"] if row.get("namespace") == "compact_demo"]
        self.assertLessEqual(len(entries), 3)

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

    def test_docker_and_vscode_leaf_helpers(self):
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

        status = self.server.docker_cli_status()
        self.assertEqual(status["schema"], "docker_cli_status.v1")

        with self.assertRaises(ValueError):
            self.server.docker_cli_run(command=["docker", "run", "hello-world"])

        listed = self.server.vscode_tasks_list(
            tasks_path=".vscode/tasks.json",
            control_profile="build",
        )
        self.assertEqual(listed["schema"], "vscode_tasks_list.v1")
        self.assertEqual(listed["count"], 1)
        self.assertFalse(listed["tasks"][0]["ok"])

        with self.assertRaises(ValueError):
            self.server.vscode_task_run(
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

    def test_public_mcp_tool_surface(self):
        expected = set(self.server.PUBLIC_MCP_TOOL_NAMES)

        async def run_checks():
            tools = await self.server.mcp.list_tools()
            names = {item.model_dump().get("name") for item in tools}
            self.assertEqual(names, expected)
            self.assertIn("task_router", names)
            for tool_name in self.server.SCHEMA_BACKED_TOOL_NAMES:
                self.assertIn(tool_name, names)

        asyncio.run(run_checks())

    def test_public_task_router_argument_descriptions(self):
        async def run_checks():
            tools = await self.server.mcp.list_tools()
            tool = next(item for item in tools if item.model_dump().get("name") == "task_router")
            schema = tool.model_dump().get("inputSchema", {})
            props = schema.get("properties", {})
            self.assertGreaterEqual(len(props), 10)
            missing = sorted(name for name, spec in props.items() if not spec.get("description"))
            self.assertEqual(missing, [])
            self.assertIn("Start with `task`", props["mode"]["description"])
            self.assertIn("Reuse the same value across related requests", props["memory_session"]["description"])
            self.assertIn("single-prompt override", props["prompts"]["description"])

            def assert_array_property(name):
                spec = props[name]
                options = spec.get("anyOf", [])
                array_spec = next((row for row in options if row.get("type") == "array"), None)
                self.assertIsNotNone(array_spec, name)
                self.assertIn("items", array_spec, name)

            for name in ("texts", "stop", "packages", "prompts", "candidates"):
                assert_array_property(name)

        asyncio.run(run_checks())

    def test_leaf_surface_modes(self):
        self.write_repo_text("config.json", '{"outer": {"value": 7}}\n')

        repo_out = self.server.json_query(path="config.json", query="outer.value", file_type="json")
        self.assertEqual(json.loads(repo_out["value_json"]), 7)

        write_out = self.server.workspace_transaction(mode="write", path="notes.txt", content="hello\n")
        self.assertEqual(write_out["schema"], "workspace_transaction.v1")
        self.assertTrue((self.repo_path / "notes.txt").is_file())

        git_out = self.server.git_status(short=True)
        self.assertIn("notes.txt", git_out)

        grep_out = self.server.grep(pattern="alpha", path="src", output_profile="compact")
        self.assertGreaterEqual(len(grep_out), 1)

        mem_out = self.server.artifact_memory_index(mode="refresh", path="docs")
        self.assertEqual(mem_out["schema"], "artifact_memory_index.v1")

        tool_out = self.server.tool_router_learned(query="find files", candidates=["find_paths", "grep"], mode="route")
        self.assertEqual(tool_out["schema"], "tool_router_learned.v1")

        governance_out = self.server.runtime_contract_checker()
        self.assertEqual(governance_out["schema"], "runtime_contract_checker.v1")

        workflow_out = self.server.constraint_solver_for_tasks(
            actions=["run tests"],
            requirements=["run tests"],
        )
        self.assertTrue(workflow_out["ok"])

        guard_out = self.server.workspace_facts(refresh=True)
        self.assertIn("generated_at", guard_out)

        math_out = self.server.math_router(mode="verify", left="x*(x+1)", right="x**2 + x")
        self.assertEqual(math_out["schema"], "math_router.v1")
        self.assertTrue(math_out["result"]["proven"])

        doc_out = self.server.document_router(mode="translate", text="hello world", source_lang="en", target_lang="de")
        self.assertEqual(doc_out["schema"], "document_router.v1")

        diagram_out = self.server.diagram_router(mode="lint_mermaid", mermaid_text="A -> B", auto_fix=True)
        self.assertEqual(diagram_out["schema"], "diagram_router.v1")

    def test_remaining_router_invalid_modes(self):
        with self.assertRaises(ValueError):
            self.server.math_router(mode="bad")
        with self.assertRaises(ValueError):
            self.server.document_router(mode="bad")
        with self.assertRaises(ValueError):
            self.server.diagram_router(mode="bad")


if __name__ == "__main__":
    unittest.main()
