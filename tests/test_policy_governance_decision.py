# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

from tests.server_test_support import ServerToolsTestBase


class PolicyGovernanceDecisionTests(ServerToolsTestBase):
    def write_policy_bundle(self, rel_path=".config/codebase-tooling-mcp/policies/mcp-governance.example.json", **overrides):
        bundle = {
            "schema": "mcp_governance_policy_bundle.v1",
            "bundle_id": "test.mcp-governance",
            "version": "2026-05-23",
            "trust": {"source": "repository", "reviewed": True, "reviewed_by": "tests"},
            "rules": [
                {
                    "id": "deny.sensitive-read-to-network",
                    "effect": "deny",
                    "priority": 10,
                    "when": {"network": True, "data_classifications": ["sensitive", "secret", "restricted"]},
                    "rationale": "Sensitive repository data must not flow to network-capable steps.",
                    "evidence": ["network-capable planned step", "sensitive data classification"],
                    "safe_next_actions": ["Remove the network step or use a redacted offline artifact."],
                },
                {
                    "id": "approval.repo-mutation",
                    "effect": "requires_approval",
                    "priority": 20,
                    "when": {"mutates": True},
                    "rationale": "Repository mutation needs explicit approval and hard-gate checks.",
                    "evidence": ["mutation-capable planned step"],
                    "safe_next_actions": ["Run mutation_step_guard before mutating."],
                },
                {
                    "id": "allow.read-only-governance",
                    "effect": "allow",
                    "priority": 100,
                    "when": {"categories_any": ["read-only"], "mutates": False, "network": False},
                    "rationale": "Read-only repository inspection is allowed inside declared scope.",
                    "evidence": ["catalogued read-only MCP tool"],
                    "safe_next_actions": ["Proceed read-only; rerun if scope or tools change."],
                },
            ],
        }
        bundle.update(overrides)
        path = self.write_repo_text(rel_path, self.server.json.dumps(bundle, indent=2, sort_keys=True))
        return path

    def test_read_only_policy_bundle_allows_with_redacted_evidence(self):
        self.write_policy_bundle()

        out = self.server.policy_governance_decision(
            intent="Inspect repo with token=abc123secretvalue",
            execution_mode="offline-onboard-only",
            allowed_targets=["src"],
            data_classification="internal",
            planned_steps=[{"tool": "grep", "mode": "search", "args": {"pattern": "alpha", "path": "src/sample.py"}}],
        )

        self.assertEqual(out["schema"], "policy_governance_decision.v1")
        self.assertEqual(out["decision"], "allow")
        self.assertTrue(out["ok"])
        self.assertIn("allow.read-only-governance", out["matched_rule_ids"])
        self.assertTrue(out["read_only"])
        self.assertFalse(out["executed_plan"])
        text = self.server.json.dumps(out)
        self.assertNotIn("abc123secretvalue", text)
        self.assertNotIn(str(self.repo_path), text)

    def test_sensitive_network_sequence_denies(self):
        self.write_policy_bundle()

        out = self.server.policy_governance_decision(
            intent="Read config and send to network summary",
            execution_mode="online-cloud-assisted",
            allowed_targets=["."],
            data_classification="sensitive",
            planned_steps=[
                {"tool": "read_snippet", "args": {"path": "config/secrets.env"}},
                {"tool": "model_assisted_summary", "network": True, "args": {"prompt": "summarize"}},
            ],
        )

        self.assertEqual(out["decision"], "deny")
        self.assertFalse(out["ok"])
        self.assertIn("deny.sensitive-read-to-network", out["matched_rule_ids"])
        self.assertIn("hard_gate.workflow_policy_plan", out["matched_rule_ids"])
        self.assertNotIn("secrets.env", self.server.json.dumps(out["rule_results"]))

    def test_mutation_requires_approval_but_allow_mutations_false_denies(self):
        self.write_policy_bundle()
        self.server.ALLOW_MUTATIONS = False

        out = self.server.policy_governance_decision(
            intent="Patch one source file",
            execution_mode="mutation",
            allowed_targets=["src"],
            planned_steps=[
                {"tool": "workspace_transaction", "mode": "snapshot", "expected_artifacts": ["snapshot"]},
                {"tool": "apply_unified_diff", "args": {"path": "src/sample.py"}, "mutates": True},
            ],
        )

        self.assertEqual(out["decision"], "deny")
        self.assertIn("approval.repo-mutation", out["matched_rule_ids"])
        self.assertIn("hard_gate.allow_mutations", out["matched_rule_ids"])
        self.assertIn("allow_mutations_disabled", {finding["code"] for finding in out["findings"]})
        self.assertIn("mutation_step_guard", out["authoritative_hard_gates"])

    def test_malformed_unknown_missing_untrusted_and_out_of_repo_bundles_fail_closed(self):
        cases = [
            (".config/codebase-tooling-mcp/policies/bad-json.json", "{not json", "policy_bundle_malformed"),
            (
                ".config/codebase-tooling-mcp/policies/unknown.json",
                self.server.json.dumps({"schema": "mcp_governance_policy_bundle.v99"}),
                "policy_bundle_unknown_schema",
            ),
            (
                ".config/codebase-tooling-mcp/policies/missing-metadata.json",
                self.server.json.dumps({
                    "schema": "mcp_governance_policy_bundle.v1",
                    "trust": {"source": "repository", "reviewed": True},
                    "rules": [{"id": "allow.any", "effect": "allow"}],
                }),
                "policy_bundle_missing_metadata",
            ),
            (
                ".config/codebase-tooling-mcp/policies/untrusted.json",
                self.server.json.dumps({
                    "schema": "mcp_governance_policy_bundle.v1",
                    "bundle_id": "bad",
                    "version": "1",
                    "trust": {"source": "agent", "reviewed": False},
                    "rules": [{"id": "allow.any", "effect": "allow"}],
                }),
                "policy_bundle_untrusted",
            ),
        ]
        for rel_path, content, code in cases:
            with self.subTest(code=code):
                self.write_repo_text(rel_path, content)
                out = self.server.policy_governance_decision(
                    intent="Inspect",
                    planned_steps=[{"tool": "repo_info"}],
                    allowed_targets=["."],
                    policy_bundle_path=rel_path,
                )
                self.assertEqual(out["decision"], "deny")
                self.assertIn(code, {finding["code"] for finding in out["findings"]})

        out = self.server.policy_governance_decision(
            intent="Inspect",
            planned_steps=[{"tool": "repo_info"}],
            allowed_targets=["."],
            policy_bundle_path="../outside.json",
        )
        self.assertEqual(out["decision"], "deny")
        self.assertIn("policy_bundle_path_out_of_scope", {finding["code"] for finding in out["findings"]})

    def test_governance_and_release_readiness_surface_compact_latest_policy_decision(self):
        self.write_policy_bundle()
        decision = self.server.policy_governance_decision(
            intent="Inspect repo",
            planned_steps=[{"tool": "repo_info"}],
            allowed_targets=["."],
        )
        handle = self.server.result_handle(mode="store", tool="policy_governance_decision", value=decision)

        governance = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=False)
        compact = governance["policy_governance_decision"]
        self.assertEqual(compact["result_id"], handle["result_id"])
        self.assertEqual(compact["decision"], "allow")
        self.assertIn("policy_governance_decision", governance["governance_hooks"])
        self.assertNotIn(str(self.repo_path), self.server.json.dumps(compact))

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
        check = readiness["checks"]["policy_governance_decision"]
        self.assertEqual(check["result_id"], handle["result_id"])
        self.assertEqual(check["decision"], "allow")
        self.assertNotIn("rule_results", self.server.json.dumps(check))
