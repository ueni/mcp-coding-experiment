# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

from tests.server_test_support import ServerToolsTestBase


class WorkflowFailureDiagnosticsTests(ServerToolsTestBase):
    def test_passing_replay_has_no_critical_failure_step(self):
        out = self.server.workflow_diagnostics(
            trajectory=[
                {
                    "step_id": "ctx-1",
                    "tool": "read_snippet",
                    "success": True,
                    "outputs": {"context_metadata": {"fresh": True, "tests_fresh": True}},
                },
                {
                    "step_id": "snap-1",
                    "tool": "state_snapshot",
                    "success": True,
                    "outputs": {"snapshot_id": "snap-fixture"},
                },
                {
                    "step_id": "mut-1",
                    "tool": "apply_unified_diff",
                    "mode": "write",
                    "success": True,
                    "args": {"path": "src/app.py", "rollback_snapshot_id": "snap-fixture"},
                },
                {
                    "step_id": "test-1",
                    "tool": "release_readiness",
                    "success": True,
                    "outputs": {"selected_tests": ["tests/test_workflow_failure_diagnostics.py"]},
                },
            ]
        )

        self.assertTrue(out["ok"])
        self.assertEqual(out["failure_category"], "none")
        self.assertEqual(out["critical_failure_step"], {})
        self.assertEqual(out["constraint_violations"], [])
        self.assertFalse(out["llm_judging"]["enabled"])

    def test_missing_clarification_localizes_first_critical_failure(self):
        out = self.server.workflow_diagnostics(
            trajectory=[
                {
                    "step_id": "plan-1",
                    "tool": "mutation_step_guard",
                    "success": False,
                    "error": "needs_clarification: user intent conflicts with target path token=secret-value",
                    "outputs": {"decision": "needs_clarification", "requires_clarification": True},
                },
                {
                    "step_id": "mut-1",
                    "tool": "apply_unified_diff",
                    "mode": "write",
                    "success": False,
                    "error": "blocked after missing clarification",
                },
            ]
        )

        self.assertFalse(out["ok"])
        self.assertEqual(out["failure_category"], "clarification")
        self.assertEqual(out["critical_failure_step"]["step_id"], "plan-1")
        self.assertGreaterEqual(out["critical_failure_step"]["confidence"], 0.8)
        self.assertIn("clarification", out["failure_taxonomy"])
        self.assertTrue(out["recommended_followup"])
        encoded = self.server.json.dumps(out, sort_keys=True)
        self.assertIn("<redacted>", encoded)
        self.assertNotIn("secret-value", encoded)

    def test_missing_snapshot_before_mutation_is_constraint_violation(self):
        out = self.server.workflow_diagnostics(
            trajectory=[
                {
                    "step_id": "ctx-1",
                    "tool": "read_snippet",
                    "success": True,
                    "outputs": {"context_metadata": {"fresh": True}},
                },
                {
                    "step_id": "mut-1",
                    "tool": "apply_unified_diff",
                    "mode": "write",
                    "success": True,
                    "args": {"path": "src/app.py"},
                    "outputs": {"patched": True},
                },
            ]
        )

        self.assertFalse(out["ok"])
        self.assertEqual(out["failure_category"], "mutation-snapshot")
        self.assertEqual(out["critical_failure_step"]["step_id"], "mut-1")
        self.assertEqual(out["critical_failure_step"]["constraint_id"], "snapshot_before_mutation")
        self.assertEqual(out["constraint_violations"][0]["failure_category"], "mutation-snapshot")
        self.assertIn("mutation-snapshot", out["failure_taxonomy"])
        self.assertTrue(any("snapshot" in item.lower() for item in out["recommended_followup"]))
