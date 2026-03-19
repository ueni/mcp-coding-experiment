# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class ServerMemoryWorkspaceCoverageTest(ServerToolsTestBase):
    def test_memory_router_decision_record_and_get_effective(self):
        self.server.memory_router(
            mode="decision_record",
            namespace="decisions",
            topic="router-plan",
            decision="draft",
            decided_by="llm",
            rationale="initial draft",
        )
        self.server.memory_router(
            mode="decision_record",
            namespace="decisions",
            topic="router-plan",
            decision="approved",
            decided_by="human",
            rationale="approved by reviewer",
        )
        self.server.memory_router(
            mode="summary_upsert",
            namespace="decisions",
            focus="coverage",
            summary="coverage target is source/server.py only",
        )
        self.server.memory_router(
            mode="upsert",
            namespace="decisions",
            key="paths",
            value={"file_paths": ["src/sample.py"]},
            tags=["files"],
        )

        out = self.server.memory_router(
            mode="get",
            namespace="decisions",
            max_entries=10,
            include_summaries=True,
            include_effective_decisions=True,
        )

        result = out["result"]
        self.assertEqual(out["schema"], "memory_router.v1")
        self.assertEqual(result["effective_decision_count"], 1)
        self.assertEqual(result["effective_decisions"][0]["decided_by"], "human")
        self.assertGreaterEqual(result["summary_count"], 1)
        self.assertGreaterEqual(result["count"], 1)

    def test_memory_validate_flags_stale_paths_and_drops_expired(self):
        self.server.memory_router(
            mode="upsert",
            namespace="validate-demo",
            key="stale-entry",
            value={"file_paths": ["missing.txt", "../escape.py"]},
            ttl_days=30,
        )
        self.server.memory_router(
            mode="summary_upsert",
            namespace="validate-demo",
            focus="summary",
            summary="expired summary",
            ttl_days=30,
        )
        self.server.memory_router(
            mode="decision_record",
            namespace="validate-demo",
            topic="expiry",
            decision="old decision",
            decided_by="llm",
            ttl_days=30,
        )

        payload = self.server._memory_load()
        expired_at = "2000-01-01T00:00:00+00:00"
        payload["entries"][0]["expires_at"] = expired_at
        payload["summaries"][0]["expires_at"] = expired_at
        payload["decisions"][0]["expires_at"] = expired_at
        self.server._memory_save(payload)

        validated = self.server.memory_router(
            mode="validate",
            validate_paths=True,
            drop_expired=False,
            max_entries=20,
        )
        result = validated["result"]
        self.assertEqual(validated["schema"], "memory_router.v1")
        self.assertEqual(result["stale_count"], 1)
        self.assertEqual(result["summary_stale_count"], 1)
        self.assertEqual(result["decision_stale_count"], 1)
        stale_entry = result["stale_entries"][0]
        self.assertTrue(stale_entry["expired"])
        self.assertIn("missing.txt", stale_entry["stale_paths"])
        self.assertIn("../escape.py", stale_entry["stale_paths"])

        dropped = self.server.memory_router(
            mode="validate",
            validate_paths=True,
            drop_expired=True,
            max_entries=20,
        )
        dropped_result = dropped["result"]
        self.assertGreaterEqual(dropped_result["dropped_expired"], 3)
        payload_after = self.server._memory_load()
        self.assertEqual(payload_after["entries"], [])
        self.assertEqual(payload_after["summaries"], [])
        self.assertEqual(payload_after["decisions"], [])

    def test_failure_memory_get_and_suggest(self):
        self.server._failure_record(
            command=["pytest", "-q"],
            stderr="command timed out while running pytest",
            stdout="collected 5 items",
            category="command_runner",
            suggestion="Increase timeout_seconds.",
        )
        self.server._failure_record(
            command=["local_infer", "endpoint"],
            stderr="connection refused by local endpoint",
            stdout="",
            category="local_infer",
            suggestion="Start the endpoint.",
        )

        rows = self.server.failure_memory(
            mode="get",
            category="command_runner",
            contains="timed out",
            max_entries=5,
        )
        self.assertEqual(rows["mode"], "get")
        self.assertEqual(rows["count"], 1)
        self.assertEqual(rows["entries"][0]["category"], "command_runner")

        suggestions = self.server.failure_memory(
            mode="suggest",
            error_text="pytest timed out again",
            max_suggestions=3,
        )
        self.assertEqual(suggestions["mode"], "suggest")
        self.assertGreaterEqual(suggestions["count"], 1)
        self.assertIn("timed out", suggestions["suggestions"][0]["stderr"].lower())

    def test_workspace_facts_refresh_and_cached_read(self):
        refreshed = self.server.workspace_facts(refresh=True)
        facts_path = self.repo_path / ".build" / "memory" / "workspace_facts.json"
        self.assertTrue(facts_path.is_file())
        self.assertTrue(refreshed["has_tests_dir"])
        self.assertTrue(refreshed["has_readme"])

        cached_payload = {
            "generated_at": "cached",
            "is_git_repo": False,
            "file_count": 7,
            "top_extensions": [{"extension": ".py", "count": 2}],
            "has_tests_dir": False,
            "has_readme": False,
            "default_output_profile": "compact",
        }
        facts_path.write_text(json.dumps(cached_payload), encoding="utf-8")
        cached = self.server.workspace_facts(refresh=False)
        self.assertEqual(cached, cached_payload)

    def test_workspace_transaction_apply_validate_and_rollback(self):
        begun = self.server.workspace_transaction(mode="begin", label="txn")
        txn_id = begun["result"]["transaction_id"]

        applied = self.server.workspace_transaction(
            mode="apply",
            transaction_id=txn_id,
            changes=[
                {"path": "src/bad.py", "content": "def broken(:\n    pass\n"},
                {"path": "README.md", "content": "# Changed\n"},
            ],
        )
        self.assertEqual(applied["schema"], "workspace_transaction.v1")
        self.assertEqual(applied["result"]["change_count"], 2)
        self.assertTrue((self.repo_path / "src" / "bad.py").is_file())

        validated = self.server.workspace_transaction(mode="validate", transaction_id=txn_id)
        self.assertEqual(validated["result"]["python_files_checked"], 1)
        self.assertEqual(validated["result"]["compile_error_count"], 1)
        self.assertIn("src/bad.py", validated["result"]["changed_paths"])

        rolled_back = self.server.workspace_transaction(mode="rollback", transaction_id=txn_id)
        self.assertEqual(rolled_back["result"]["status"], "rolled_back")
        self.assertFalse((self.repo_path / "src" / "bad.py").exists())
        self.assertIn("# Test Repo", (self.repo_path / "README.md").read_text(encoding="utf-8"))

    def test_workspace_transaction_commit_deletes_metadata(self):
        begun = self.server.workspace_transaction(mode="begin", label="commit-me")
        txn_id = begun["result"]["transaction_id"]
        self.server.workspace_transaction(
            mode="apply",
            transaction_id=txn_id,
            changes=[{"path": "src/new_file.py", "content": "VALUE = 1\n"}],
        )

        committed = self.server.workspace_transaction(
            mode="commit",
            transaction_id=txn_id,
            delete_metadata=True,
        )
        self.assertEqual(committed["result"]["status"], "committed")
        self.assertTrue(committed["result"]["metadata_deleted"])
        self.assertFalse(self.server._tx_path(txn_id).exists())

    def test_workspace_transaction_snapshot_restore_and_invalid_restore(self):
        with self.assertRaises(ValueError):
            self.server.workspace_transaction(mode="restore")

        with patch.object(
            self.server,
            "state_snapshot",
            return_value={"schema": "state_snapshot.v1", "snapshot_id": "snap-1"},
        ) as snapshot:
            snap = self.server.workspace_transaction(
                mode="snapshot",
                label="savepoint",
                include_build_dir=True,
            )
        self.assertEqual(snap["schema"], "workspace_transaction.v1")
        self.assertEqual(snap["result"]["snapshot_id"], "snap-1")
        snapshot.assert_called_once_with(label="savepoint", include_build_dir=True)

        with patch.object(
            self.server,
            "state_restore",
            return_value={"schema": "state_restore.v1", "restored": True},
        ) as restore:
            restored = self.server.workspace_transaction(mode="restore", snapshot_id="snap-1")
        self.assertEqual(restored["result"]["restored"], True)
        restore.assert_called_once_with(snapshot_id="snap-1")

    def test_required_tool_chain_reports_missing_inputs(self):
        result = self.server.result_handle(mode="store", tool="tool_a", value={"ok": True})
        out = self.server.required_tool_chain(
            required_tools=["tool_a", "tool_missing"],
            required_artifacts=[".build/reports/missing.txt"],
            required_result_ids=[result["result_id"], "missing-result-id"],
            require_order=True,
            max_age_minutes=60,
        )
        self.assertFalse(out["ok"])
        self.assertIn("tool_missing", out["missing_tools"])
        self.assertIn(".build/reports/missing.txt", out["missing_artifacts"])
        self.assertIn("missing-result-id", out["missing_result_ids"])

    def test_required_tool_chain_without_order_can_match_latest(self):
        first = self.server.result_handle(mode="store", tool="tool_a", value={"ok": 1})
        second = self.server.result_handle(mode="store", tool="tool_b", value={"ok": 2})
        report = self.repo_path / ".build" / "reports" / "READY.txt"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("ok\n", encoding="utf-8")

        out = self.server.required_tool_chain(
            required_tools=["tool_b", "tool_a"],
            required_artifacts=[".build/reports/READY.txt"],
            required_result_ids=[first["result_id"], second["result_id"]],
            require_order=False,
            max_age_minutes=60,
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["missing_tools"], [])
        self.assertEqual(out["missing_artifacts"], [])
        self.assertEqual(out["missing_result_ids"], [])

    def test_memory_trace_reusable_script_success_records_and_updates(self):
        with patch.object(self.server, "ALLOW_MUTATIONS", False):
            disabled = self.server._memory_trace_reusable_script_success(
                "src/sample.py",
                profile="quick",
                steps=[],
                venv_python=sys.executable,
            )
        self.assertEqual(disabled["reason"], "mutations_disabled")

        unsupported = self.server._memory_trace_reusable_script_success(
            "README.md",
            profile="quick",
            steps=[],
            venv_python=sys.executable,
        )
        self.assertEqual(unsupported["reason"], "unsupported_script_type")

        first = self.server._memory_trace_reusable_script_success(
            "src/sample.py",
            profile="quick",
            steps=[{"command": [sys.executable, "-m", "pytest", "-q"]}],
            venv_python=sys.executable,
        )
        second = self.server._memory_trace_reusable_script_success(
            "src/sample.py",
            profile="full",
            steps=[{"command": [sys.executable, "-m", "pytest", "-q", "tests"]}],
            venv_python=sys.executable,
        )
        self.assertTrue(first["recorded"])
        self.assertTrue(second["recorded"])

        payload = self.server._memory_load()
        entries = [e for e in payload["entries"] if e.get("key") == "script:src/sample.py"]
        self.assertEqual(len(entries), 1)
        value = entries[0]["value"]
        self.assertEqual(value["success_count"], 2)
        self.assertEqual(value["last_success_profile"], "full")


    def test_master_memory_route_and_session_namespace_isolation(self):
        self.server.memory_router(
            mode="summary_upsert",
            namespace="master/route/security",
            focus="recent_activity",
            summary="route-security",
        )
        self.server.memory_router(
            mode="summary_upsert",
            namespace="master/route/review",
            focus="recent_activity",
            summary="route-review",
        )
        self.server.memory_router(
            mode="summary_upsert",
            namespace="master/session/default",
            focus="session",
            summary="default-session",
        )
        self.server.memory_router(
            mode="summary_upsert",
            namespace="master/session/other",
            focus="session",
            summary="other-session",
        )

        default_ctx = self.server._build_master_memory_context(route="security", memory_session="")
        other_ctx = self.server._build_master_memory_context(route="security", memory_session="other")

        self.assertIn("route-security", default_ctx["context"])
        self.assertNotIn("route-review", default_ctx["context"])
        self.assertIn("default-session", default_ctx["context"])
        self.assertNotIn("other-session", default_ctx["context"])
        self.assertIn("other-session", other_ctx["context"])
        self.assertNotIn("default-session", other_ctx["context"])

    def test_master_memory_blank_session_normalizes_to_default(self):
        self.assertEqual(self.server._normalize_master_memory_session(""), "default")
        info = self.server._build_master_memory_context(route="security", memory_session="")
        self.assertEqual(info["memory_session"], "default")
        self.assertEqual(info["session_namespace"], "master/session/default")

    def test_master_memory_session_auto_compact_creates_summary(self):
        for idx in range(12):
            self.server.memory_router(
                mode="upsert",
                namespace="master/session/default",
                key=f"seed-{idx}",
                value={"text": "x" * 400},
                ttl_days=7,
            )

        infer_payload = {
            "schema": "local_infer.v1",
            "backend": "fallback",
            "model": "deepseek-r1:1.5b",
            "ok": True,
            "output": "security findings",
        }
        with patch.object(self.server, "local_infer", return_value=infer_payload):
            out = self.server.model_router(
                mode="master",
                prompt="Review auth middleware for security vulnerabilities and secret exposure.",
                backend="fallback",
                output_profile="normal",
                memory_session="",
            )

        payload = self.server._memory_load()
        summaries = [
            row
            for row in payload["summaries"]
            if row.get("namespace") == "master/session/default"
            and row.get("focus") == "auto_compact"
        ]
        self.assertEqual(len(summaries), 1)
        self.assertIn("Auto-compact summary", summaries[0]["summary"])
        self.assertTrue(out["memory"]["session_compaction"]["compacted"])

    def test_memory_compatibility_wrappers_remain_usable(self):
        self.server._failure_record(
            command=["pytest", "-q"],
            stderr="command timed out while running pytest",
            category="command_runner",
            suggestion="Increase timeout_seconds.",
        )
        direct_failure = self.server.failure_memory(
            mode="get",
            category="command_runner",
            max_entries=5,
        )
        routed_failure = self.server.memory_router(
            mode="failure_memory",
            query="get",
            category="command_runner",
            max_entries=5,
        )
        self.assertEqual(direct_failure["count"], routed_failure["result"]["count"])

        self.server.root_cause_memory(
            mode="add",
            issue="build fails",
            root_cause="bad config",
            fix="update config",
        )
        direct_root = self.server.root_cause_memory(
            mode="suggest",
            issue="build config",
            max_entries=5,
        )
        routed_root = self.server.memory_router(
            mode="root_cause",
            query="suggest",
            issue="build config",
            max_entries=5,
        )
        self.assertEqual(direct_root["count"], routed_root["result"]["count"])

        report = self.repo_path / ".build" / "reports" / "sample.txt"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("ok\n", encoding="utf-8")
        self.server.artifact_memory_index(mode="refresh", path=".build/reports")
        direct_artifact = self.server.artifact_memory_index(mode="query", query="sample", max_entries=5)
        routed_artifact = self.server.memory_router(
            mode="artifact_index",
            artifact_mode="query",
            path=".build/reports",
            query="sample",
            max_entries=5,
        )
        self.assertEqual(direct_artifact["count"], routed_artifact["result"]["count"])

    def test_effective_decisions_prefers_human_and_newer_timestamp(self):
        now = datetime.now(timezone.utc)
        rows = [
            {
                "namespace": "ns",
                "topic": "router",
                "decision": "old llm",
                "decided_by": "llm",
                "updated_at": (now - timedelta(days=1)).isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            },
            {
                "namespace": "ns",
                "topic": "router",
                "decision": "human override",
                "decided_by": "human",
                "updated_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            },
            {
                "namespace": "other",
                "topic": "router",
                "decision": "ignore me",
                "decided_by": "llm",
                "updated_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            },
        ]

        selected = self.server._effective_decisions(rows, now=now, namespace="ns")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["decision"], "human override")
        self.assertFalse(selected[0]["expired"])
