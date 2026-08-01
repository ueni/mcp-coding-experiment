# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

import json

from tests.server_test_support import ServerToolsTestBase


class WorkflowPhaseTelemetryTests(ServerToolsTestBase):
    def _report(self, rows):
        return self.server._workflow_phase_telemetry_impl(workflow_summary=rows)

    def _rule_ids(self, report):
        return {item["rule_id"] for item in report["anomalies"]}

    def test_read_heavy_exploration_reports_phase_counts_and_cache_signals(self):
        report = self._report(
            [
                {"tool_name": "find_paths", "phase": "discover/read", "duration_ms": 10, "cacheable": True, "cache_hit": True, "input_fingerprint": "tree"},
                {"tool_name": "grep", "phase": "discover/read", "duration_ms": 20, "cacheable": True, "cache_hit": False, "input_fingerprint": "query-a"},
                {"tool_name": "read_snippet", "phase": "discover/read", "duration_ms": 30, "cacheable": True, "cache_hit": False, "input_fingerprint": "snippet-a"},
                {"name": "mcp.tool.workflow_policy_plan", "duration_ms": 40},
            ]
        )

        self.assertEqual(report["schema"], "workflow_phase_telemetry.v1")
        self.assertTrue(report["read_only"])
        self.assertEqual(report["summary"]["phase_counts"]["discover_read"], 3)
        self.assertEqual(report["summary"]["phase_counts"]["analyze_plan"], 1)
        self.assertEqual(report["summary"]["cacheable_count"], 3)
        self.assertEqual(report["summary"]["cache_hit_count"], 1)
        self.assertEqual(self._rule_ids(report), set())

    def test_flags_premature_mutation_before_read_evidence(self):
        report = self._report(
            [
                {"tool_name": "workspace_transaction", "phase": "mutate/write", "arguments": {"mode": "write"}},
                {"tool_name": "read_snippet", "phase": "discover/read"},
                {"tool_name": "quality_router", "phase": "verify/test", "arguments": {"mode": "self_test"}},
            ]
        )

        self.assertIn("early_mutation_before_read_evidence", self._rule_ids(report))
        ordering = report["signals"]["ordering"]
        self.assertFalse(ordering["write_after_read_ok"])
        self.assertTrue(ordering["post_write_verification_present"])

    def test_flags_missing_post_mutation_verification(self):
        report = self._report(
            [
                {"tool_name": "find_paths", "phase": "discover/read"},
                {"tool_name": "workflow_policy_plan", "phase": "analyze/plan"},
                {"tool_name": "workspace_transaction", "phase": "mutate/write", "arguments": {"mode": "apply"}},
            ]
        )

        self.assertIn("missing_verification_after_write", self._rule_ids(report))
        self.assertFalse(report["signals"]["ordering"]["post_write_verification_present"])

    def test_flags_write_after_earlier_verification_without_new_test(self):
        report = self._report(
            [
                {"tool_name": "find_paths", "phase": "discover/read"},
                {"tool_name": "workflow_policy_plan", "phase": "analyze/plan"},
                {"tool_name": "workspace_transaction", "phase": "mutate/write", "arguments": {"mode": "apply"}},
                {"tool_name": "quality_router", "phase": "verify/test", "arguments": {"mode": "self_test"}},
                {"tool_name": "workspace_transaction", "phase": "mutate/write", "arguments": {"mode": "apply"}},
            ]
        )

        self.assertIn("missing_verification_after_write", self._rule_ids(report))
        self.assertFalse(report["signals"]["ordering"]["post_write_verification_present"])

    def test_flags_repeated_uncached_reads(self):
        report = self._report(
            [
                {"tool_name": "grep", "phase": "discover/read", "cacheable": True, "cache_hit": False, "input_fingerprint": "same-heavy-read"},
                {"tool_name": "grep", "phase": "discover/read", "cacheable": True, "cache_hit": False, "input_fingerprint": "same-heavy-read"},
                {"tool_name": "read_snippet", "phase": "discover/read", "cacheable": True, "cache_hit": False, "input_fingerprint": "same-heavy-read"},
            ]
        )

        self.assertIn("repeated_uncached_heavy_reads", self._rule_ids(report))
        cache = report["signals"]["cacheability"]
        self.assertEqual(cache["repeated_read_count"], 2)
        self.assertEqual(cache["repeated_uncached_heavy_read_count"], 2)

    def test_healthy_read_write_verify_release_has_markers_without_anomalies(self):
        report = self._report(
            [
                {"tool_name": "find_paths", "phase": "discover/read", "trace_id": "abc123"},
                {"tool_name": "workflow_policy_plan", "phase": "analyze/plan", "checkpoint": "planned"},
                {"tool_name": "workspace_transaction", "phase": "mutate/write", "arguments": {"mode": "apply"}},
                {"tool_name": "quality_router", "phase": "verify/test", "arguments": {"mode": "self_test"}},
                {"tool_name": "release_readiness", "phase": "review/release"},
            ]
        )

        self.assertEqual(self._rule_ids(report), set())
        self.assertTrue(report["signals"]["ordering"]["write_after_read_ok"])
        self.assertTrue(report["signals"]["ordering"]["post_write_verification_present"])
        self.assertTrue(report["signals"]["ordering"]["release_after_verify_ok"])
        self.assertEqual(report["trace_context"]["trace_id_count"], 1)
        self.assertEqual(report["trace_context"]["workflow_checkpoint_count"], 1)
        self.assertTrue(any(hint["hint_id"] == "healthy_release_gate_ordering" for hint in report["optimization_hints"]))

    def test_public_tool_accepts_json_and_excludes_raw_private_content(self):
        rows = [
            {
                "tool_name": "read_snippet",
                "phase": "discover/read",
                "arguments": {
                    "path": "/home/alice/private/repo/secrets.py",
                    "raw_prompt": "please inspect SUPER-PRIVATE-PROMPT",
                    "api_token": "ghp_1234567890abcdefTOKEN",
                },
                "tool_output": "file contents: password='open-sesame'",
                "trace_id": "trace-secret-value",
            },
            {"tool_name": "workflow_policy_plan", "phase": "analyze/plan"},
            {"tool_name": "workspace_transaction", "phase": "mutate/write"},
            {"tool_name": "quality_router", "phase": "verify/test"},
        ]

        report = self.server.workflow_phase_telemetry(workflow_summary_json=json.dumps({"tool_calls": rows}))
        rendered = json.dumps(report, sort_keys=True)

        self.assertEqual(report["schema"], "workflow_phase_telemetry.v1")
        self.assertTrue(report["privacy"]["raw_prompts_excluded"])
        self.assertTrue(report["privacy"]["raw_tool_outputs_excluded"])
        self.assertTrue(report["privacy"]["absolute_host_paths_excluded"])
        self.assertTrue(report["privacy"]["trace_ids_hashed"])
        self.assertNotIn("SUPER-PRIVATE-PROMPT", rendered)
        self.assertNotIn("open-sesame", rendered)
        self.assertNotIn("ghp_1234567890abcdefTOKEN", rendered)
        self.assertNotIn("/home/alice/private/repo", rendered)
        self.assertNotIn("trace-secret-value", rendered)
