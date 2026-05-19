# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

from tests.server_test_support import ServerToolsTestBase


class WorkflowPolicyPlanTests(ServerToolsTestBase):
    def test_benign_read_only_workflow_allows(self):
        out = self.server.workflow_policy_plan(
            intent="Inspect repo state and summarize findings",
            execution_mode="offline-onboard-only",
            allowed_targets=["."],
            planned_steps=[
                {"tool": "repo_info", "mode": "read", "args": {}},
                {"tool": "git_status", "mode": "read", "args": {}},
                {"tool": "grep", "mode": "search", "args": {"pattern": "alpha", "path": "src"}},
            ],
        )

        self.assertEqual(out["schema"], "workflow_policy_plan.v1")
        self.assertEqual(out["decision"], "allow")
        self.assertTrue(out["ok"])
        self.assertTrue(out["read_only"])
        self.assertFalse(out["executed_plan"])
        self.assertTrue(out["plan_id"].startswith("workflow-plan-"))
        self.assertEqual(out["blocking_policies"], [])
        self.assertNotIn(str(self.repo_path), self.server.json.dumps(out))

    def test_scoped_mutation_with_snapshot_allows(self):
        out = self.server.workflow_policy_plan(
            intent="Patch src/sample.py within the approved source scope",
            execution_mode="mutation",
            allowed_targets=["src"],
            planned_steps=[
                {"tool": "workspace_transaction", "mode": "snapshot", "args": {"label": "pre-edit"}, "expected_artifacts": ["snapshot"]},
                {"tool": "apply_unified_diff", "mode": "apply", "args": {"path": "src/sample.py"}, "mutates": True},
            ],
        )

        self.assertEqual(out["decision"], "allow")
        self.assertEqual(out["required_preconditions"], [])

    def test_release_workflow_missing_test_and_snapshot_gates_needs_approval(self):
        out = self.server.workflow_policy_plan(
            intent="Prepare release readiness summary",
            execution_mode="online-cloud-assisted",
            allowed_targets=["."],
            planned_steps=[
                {"tool": "release_readiness", "mode": "release_readiness", "args": {"run_tests": False}},
            ],
        )

        self.assertEqual(out["decision"], "needs_approval")
        codes = {item["code"] for item in out["findings"]}
        self.assertIn("missing_snapshot_gate", codes)
        self.assertIn("missing_test_gate", codes)
        preconditions = {item["code"] for item in out["required_preconditions"]}
        self.assertIn("snapshot_or_rollback", preconditions)
        self.assertIn("test_or_change_impact_gate", preconditions)

    def test_data_read_to_network_exfiltration_sequence_denies(self):
        out = self.server.workflow_policy_plan(
            intent="Read private config then summarize through a network tool",
            execution_mode="online-cloud-assisted",
            data_classification="sensitive",
            allowed_targets=["."],
            planned_steps=[
                {"tool": "read_snippet", "mode": "read", "args": {"path": "config/secrets.env"}},
                {"tool": "model_assisted_summary", "mode": "summary", "network": True, "args": {"purpose": "summarize"}},
            ],
        )

        self.assertEqual(out["decision"], "deny")
        self.assertIn("dataflow", out["blocking_policies"])
        codes = {item["code"] for item in out["findings"]}
        self.assertIn("data_read_to_network", codes)
        self.assertNotIn("secrets.env", self.server.json.dumps(out["steps"][0]["args_shape"]))

    def test_scope_creep_denies_out_of_scope_target(self):
        out = self.server.workflow_policy_plan(
            intent="Edit only source files",
            execution_mode="mutation",
            allowed_targets=["src"],
            planned_steps=[
                {"tool": "workspace_transaction", "mode": "snapshot", "expected_artifacts": ["snapshot"]},
                {"tool": "apply_unified_diff", "mode": "apply", "args": {"path": "docs/a.md"}, "mutates": True},
            ],
        )

        self.assertEqual(out["decision"], "deny")
        self.assertIn("scope", out["blocking_policies"])
        self.assertIn("scope_creep", {item["code"] for item in out["findings"]})

    def test_shadow_unregistered_tool_use_denies(self):
        out = self.server.workflow_policy_plan(
            intent="Use an undeclared helper",
            execution_mode="auto",
            allowed_targets=["."],
            planned_steps=[{"tool": "unknown_shadow_server.upload_repo", "mode": "run"}],
        )

        self.assertEqual(out["decision"], "deny")
        self.assertIn("shadow_tool", out["blocking_policies"])
        self.assertIn("shadow_tool", {item["code"] for item in out["findings"]})

    def test_plan_id_is_deterministic_and_redacted(self):
        kwargs = {
            "intent": "Inspect repository status with token=abc123secretvalue before summary",
            "execution_mode": "auto",
            "allowed_targets": ["/home/user/source/private-repo/src"],
            "planned_steps": [{"tool": "read_snippet", "args": {"path": "/home/user/source/private-repo/src/a.py", "api_key": "secret"}}],
        }
        first = self.server.workflow_policy_plan(**kwargs)
        second = self.server.workflow_policy_plan(**kwargs)

        self.assertEqual(first["plan_id"], second["plan_id"])
        text = self.server.json.dumps(first)
        self.assertNotIn("/home/user/source", text)
        self.assertNotIn("abc123secretvalue", text)
        self.assertNotIn("api_key\": \"secret", text)

    def test_governance_and_release_readiness_surface_stored_preflight_evidence(self):
        preflight = self.server.workflow_policy_plan(
            intent="Release without gates",
            execution_mode="online-cloud-assisted",
            allowed_targets=["."],
            planned_steps=[{"tool": "release_readiness", "mode": "release_readiness"}],
        )
        handle = self.server.result_handle(mode="store", tool="workflow_policy_plan", value=preflight)

        governance = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=False)
        self.assertEqual(governance["workflow_policy_plan"]["result_id"], handle["result_id"])
        self.assertEqual(governance["workflow_policy_plan"]["decision"], "needs_approval")
        self.assertIn("workflow_policy_plan", governance["governance_hooks"])

        readiness = self.server.release_readiness(
            base_ref="HEAD",
            head_ref="HEAD",
            run_tests=False,
            run_docs_check=False,
            run_security_check=False,
            run_dependency_security_check=False,
            run_license_check=False,
            run_risk_check=False,
            run_impact_check=False,
        )
        check = readiness["checks"]["workflow_policy_plan"]
        self.assertEqual(check["result_id"], handle["result_id"])
        self.assertEqual(check["decision"], "needs_approval")
        self.assertTrue(check["warning"])
