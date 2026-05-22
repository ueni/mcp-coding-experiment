# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

from tests.server_test_support import ServerToolsTestBase


class WorkflowReminderTests(ServerToolsTestBase):
    def test_missing_rollback_before_mutation_points_to_snapshot_and_guard(self):
        out = self.server.workflow_reminder(
            task_summary="Patch one source file; create rollback/snapshot before mutation.",
            intended_next_action={
                "tool": "workspace_transaction",
                "mode": "write",
                "args": {"path": "source/server.py"},
                "mutates": True,
            },
        )

        self.assertEqual(out["schema"], "workflow_reminder.v1")
        self.assertTrue(out["read_only"])
        self.assertTrue(out["advisory_only"])
        self.assertTrue(out["emitted"])
        self.assertEqual(out["trigger"], "missing_rollback_before_mutation")
        self.assertEqual(out["required_next_gate"]["tool"], "state_snapshot")
        self.assertEqual(out["required_next_gate"]["follow_up_gate"], "mutation_step_guard")
        self.assertTrue(out["suppress_if_already_satisfied"])
        self.assertIn("rollback", {item["id"] for item in out["remembered_constraints"]})
        self.assertFalse(out["security"]["grants_permission"])

    def test_workspace_transaction_write_step_does_not_suppress_rollback_reminder(self):
        out = self.server.workflow_reminder(
            task_summary="Patch one source file; create rollback/snapshot before mutation.",
            intended_next_action={"tool": "apply_unified_diff", "mode": "apply", "mutates": True},
            recent_steps=[
                {
                    "tool": "workspace_transaction",
                    "mode": "write",
                    "success": True,
                    "status": "wrote draft changes",
                }
            ],
        )

        self.assertTrue(out["emitted"])
        self.assertEqual(out["trigger"], "missing_rollback_before_mutation")
        self.assertEqual(out["required_next_gate"]["tool"], "state_snapshot")
        self.assertFalse(out["evidence"]["suppression"])

    def test_stale_or_missing_tests_before_readiness_points_to_change_impact(self):
        out = self.server.workflow_reminder(
            task_summary="Run tests before release readiness.",
            intended_next_action={"tool": "release_readiness", "mode": "release"},
            last_gate_results={"context_metadata": {"tests_fresh": False}},
        )

        self.assertTrue(out["emitted"])
        self.assertEqual(out["trigger"], "stale_or_missing_tests_before_readiness")
        self.assertEqual(out["required_next_gate"]["tool"], "change_impact_gate")
        self.assertEqual(out["required_next_gate"]["follow_up_gate"], "release_readiness")
        self.assertIn("validation", {item["id"] for item in out["remembered_constraints"]})

    def test_secret_sensitive_action_points_to_secret_exposure_report_and_redacts(self):
        out = self.server.workflow_reminder(
            task_summary="Do not expose secrets or credentials.",
            intended_next_action={
                "tool": "read_snippet",
                "mode": "read",
                "args": {"path": ".env", "authorization": "Bearer ghp_1234567890abcdefTOKEN"},
            },
        )

        self.assertTrue(out["emitted"])
        self.assertEqual(out["trigger"], "secret_sensitive_action")
        self.assertEqual(out["required_next_gate"]["tool"], "secret_exposure_report")
        encoded = self.server.json.dumps(out, sort_keys=True)
        self.assertNotIn("ghp_1234567890abcdefTOKEN", encoded)
        self.assertIn("secret_safety", {item["id"] for item in out["remembered_constraints"]})

    def test_scope_expansion_points_to_workflow_policy_plan_before_mutation(self):
        out = self.server.workflow_reminder(
            task_summary="Only docs are in scope for this read-only review.",
            intended_next_action={
                "tool": "apply_unified_diff",
                "mode": "apply",
                "args": {"path": "source/server.py"},
                "mutates": True,
            },
        )

        self.assertTrue(out["emitted"])
        self.assertEqual(out["trigger"], "scope_expansion")
        self.assertEqual(out["required_next_gate"]["tool"], "workflow_policy_plan")
        self.assertIn("scope", {item["id"] for item in out["remembered_constraints"]})

    def test_repeated_failed_mutation_attempts_points_to_workflow_diagnostics(self):
        out = self.server.workflow_reminder(
            task_summary="Apply the approved patch only after gates pass.",
            intended_next_action={"tool": "apply_unified_diff", "mode": "apply", "mutates": True},
            recent_steps=[
                {"tool": "mutation_step_guard", "success": False, "error": "ok_to_mutate=false needs_snapshot"},
                {"tool": "apply_unified_diff", "success": False, "error": "mutation disabled"},
            ],
        )

        self.assertTrue(out["emitted"])
        self.assertEqual(out["trigger"], "repeated_failed_mutation_attempts")
        self.assertEqual(out["required_next_gate"]["tool"], "workflow_diagnostics")
        self.assertEqual(out["required_next_gate"]["follow_up_gate"], "mutation_step_guard")
        self.assertTrue(out["evidence"]["emission"])

    def test_suppresses_when_required_gate_evidence_is_already_satisfied_and_is_deterministic(self):
        kwargs = {
            "task_summary": "Run tests before readiness and keep /home/user/private path redacted.",
            "intended_next_action": {"tool": "release_readiness", "mode": "release"},
            "last_gate_results": {
                "change_impact_gate": {"ok": True, "selected_tests": ["tests/test_workflow_reminder.py"]}
            },
        }
        first = self.server.workflow_reminder(**kwargs)
        second = self.server.workflow_reminder(**kwargs)

        self.assertFalse(first["emitted"])
        self.assertEqual(first["trigger"], "none")
        self.assertTrue(first["evidence"]["suppression"])
        self.assertEqual(first["reminder_id"], second["reminder_id"])
        self.assertEqual(first, second)
        self.assertNotIn("/home/user/private", self.server.json.dumps(first, sort_keys=True))
