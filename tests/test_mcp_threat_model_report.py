# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

from __future__ import annotations

import json
from pathlib import Path

from tests.server_test_support import ServerToolsTestBase


class McpThreatModelReportTests(ServerToolsTestBase):
    def _copy_fixture(self, name: str) -> str:
        fixture = Path(__file__).parent / "fixtures" / name
        rel = f"fixtures/{name}"
        self.write_repo_text(rel, fixture.read_text(encoding="utf-8"))
        return rel

    def test_report_models_stride_dread_controls_and_fixture_findings(self):
        fixture_path = self._copy_fixture("mcp_poisoned_tools.json")
        baseline_path = self._copy_fixture("mcp_threat_model_baseline.json")

        report = self.server.mcp_threat_model_report(
            fixture_path=fixture_path,
            baseline_path=baseline_path,
            export=False,
        )

        self.assertEqual(report["schema"], "mcp_threat_model_report.v1")
        self.assertTrue(report["read_only"])
        self.assertTrue(report["ok"], report["baseline"])
        self.assertEqual(report["status"], "findings")
        self.assertGreaterEqual(report["summary"]["component_count"], 5)
        self.assertGreaterEqual(report["summary"]["trust_boundary_count"], 4)
        self.assertIn("Tampering", report["summary"]["stride_counts"])
        self.assertIn("tool_metadata_poisoning", {row["id"] for row in report["threats"]})

        rule_ids = {finding["rule_id"] for finding in report["findings"]}
        self.assertIn("poisoned-tool-metadata", rule_ids)
        self.assertIn("ambiguous-parameter-visibility", rule_ids)
        self.assertIn("annotation-category-mismatch", rule_ids)
        self.assertIn("client-transparency-control-gap", rule_ids)
        self.assertIn("temporal-tool-catalog-mutation", rule_ids)
        self.assertEqual(report["fixtures"]["tool_count"], 4)
        self.assertEqual(report["fixtures"]["transition_count"], 1)
        self.assertEqual(report["baseline"]["newly_introduced_high_uncovered_finding_ids"], [])
        self.assertFalse(report["security"]["network_access"])
        expected_dread_rubric = {
            "schema": "mcp_threat_model_dread_rubric.v1",
            "version": 1,
            "fields": ["damage", "reproducibility", "exploitability", "affected_users", "discoverability"],
            "field_range": {"min": 0, "max": 10, "type": "integer"},
            "scoring": "clamp each field to 0..10 as an integer, then sum fields in the listed order",
            "severity_thresholds": [
                {"severity": "high", "min_score": 35, "max_score": 50},
                {"severity": "medium", "min_score": 23, "max_score": 34},
                {"severity": "low", "min_score": 1, "max_score": 22},
                {"severity": "info", "min_score": 0, "max_score": 0},
            ],
        }
        self.assertEqual(report["dread_rubric"], expected_dread_rubric)

        threat_dread_scores = {row["id"]: row["dread"]["score"] for row in report["threats"]}
        self.assertEqual(
            threat_dread_scores,
            {
                "tool_metadata_poisoning": 39,
                "ambiguous_parameter_visibility": 28,
                "cross_boundary_secret_exfiltration": 36,
                "unauthorized_repository_mutation": 31,
                "audit_repudiation": 24,
            },
        )
        transition = next(
            finding
            for finding in report["findings"]
            if finding["rule_id"] == "temporal-tool-catalog-mutation"
        )
        self.assertEqual(transition["dread"]["score"], 38)
        self.assertEqual(transition["uncovered_controls"], ["temporal_catalog_delta_audit"])
        self.assertTrue(transition["evidence"]["observed_notifications_tools_list_changed"])
        self.assertTrue(transition["evidence"]["observed_repeated_tools_list"])

        report["dread_rubric"]["fields"].append("mutated_by_caller")
        repeated = self.server.mcp_threat_model_report(
            fixture_path=fixture_path,
            baseline_path=baseline_path,
            export=False,
        )
        self.assertEqual(repeated["dread_rubric"], expected_dread_rubric)

    def test_high_uncovered_fixture_regression_is_deterministic(self):
        fixture_path = self._copy_fixture("mcp_poisoned_tools.json")
        strict_baseline = {
            "schema": "mcp_threat_model_baseline.v1",
            "allowed_high_uncovered_finding_count": 0,
            "allowed_high_uncovered_finding_ids": [],
            "required_fixture_ids": ["ambiguous_parameter_visibility"],
            "required_fixture_rule_ids": {
                "ambiguous_parameter_visibility": ["ambiguous-parameter-visibility"]
            },
        }
        self.write_repo_text(
            "fixtures/strict_mcp_threat_model_baseline.json",
            json.dumps(strict_baseline, indent=2, sort_keys=True) + "\n",
        )

        report = self.server.mcp_threat_model_report(
            fixture_path=fixture_path,
            baseline_path="fixtures/strict_mcp_threat_model_baseline.json",
            export=False,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "regression")
        self.assertEqual(report["summary"]["high_uncovered_finding_count"], 3)
        self.assertIn(
            "high_uncovered_regression",
            {failure["type"] for failure in report["baseline"]["failures"]},
        )


    def test_oauth_proxy_hardening_fixture_outcomes_are_redacted(self):
        fixture_path = self._copy_fixture("mcp_oauth_proxy_hardening.json")

        report = self.server.mcp_threat_model_report(
            fixture_path=fixture_path,
            export=False,
        )

        oauth = report["mcp_oauth_proxy_hardening"]
        self.assertEqual(oauth["schema"], "mcp_oauth_proxy_hardening.v1")
        self.assertEqual(oauth["status"], "block")
        self.assertFalse(oauth["ok"])
        self.assertEqual(oauth["summary"]["detected_config_count"], 3)
        outcomes = {row["id"]: row["outcome"] for row in oauth["configs"]}
        self.assertEqual(outcomes["safe_oauth_proxy"], "pass")
        self.assertEqual(outcomes["warning_oauth_proxy"], "warn")
        self.assertEqual(outcomes["blocked_passthrough_proxy"], "block")
        self.assertEqual(outcomes["local_bearer_only"], "not_applicable")

        rule_ids = {finding["rule_id"] for finding in oauth["findings"]}
        self.assertIn("oauth-token-passthrough", rule_ids)
        self.assertIn("oauth-confused-deputy-validation-disabled", rule_ids)
        self.assertIn("oauth-inline-secret-material", rule_ids)
        self.assertIn("oauth-pkce-not-required", rule_ids)
        self.assertIn("oauth-state-not-required", rule_ids)
        self.assertIn("oauth-broad-origin-or-redirect", rule_ids)
        self.assertIn("oauth-token-passthrough", {finding["rule_id"] for finding in report["findings"]})

        serialized = json.dumps(oauth, sort_keys=True)
        self.assertNotIn("s3cr3t-client-secret-do-not-log", serialized)
        self.assertNotIn("eyJunsafeDoNotLog", serialized)
        self.assertNotIn("auth-code-do-not-log", serialized)
        self.assertNotIn("/home/user/private", serialized)
        self.assertFalse(oauth["security"]["tokens_or_secrets_included"])
        self.assertFalse(oauth["security"]["host_absolute_paths_included"])

    def test_release_readiness_includes_oauth_proxy_compact_result(self):
        self.write_repo_text(
            ".codebase-tooling-mcp/mcp-oauth-proxy.json",
            json.dumps(
                {
                    "id": "repo_oauth_proxy",
                    "auth_mode": "oauth-resource",
                    "authorization_servers": ["https://issuer.example.test"],
                    "expected_audience": "mcp://codebase-tooling-mcp",
                    "resource_validation": True,
                    "require_pkce": True,
                    "require_state": True,
                    "token_passthrough": False,
                    "client_secret_ref": "env:MCP_OAUTH_CLIENT_SECRET",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )

        readiness = self.server.release_readiness(
            run_tests=False,
            run_docs_check=False,
            run_security_check=False,
            run_dependency_security_check=False,
            run_ci_workflow_security_check=False,
            run_agent_security_delta_check=False,
            run_secret_exposure_check=False,
            run_agent_quality_delta_check=False,
            run_license_check=False,
            run_risk_check=False,
            run_impact_check=False,
            summary_mode="quick",
        )

        check = readiness["checks"]["mcp_oauth_proxy_hardening"]
        self.assertTrue(check["ok"], check)
        self.assertEqual(check["status"], "pass")
        self.assertEqual(check["applicability"], "applicable")
        self.assertEqual(check["detected_config_count"], 1)
        self.assertEqual(check["finding_count"], 0)

    def test_local_bearer_token_mode_oauth_hardening_not_applicable(self):
        report = self.server._mcp_oauth_proxy_hardening_report(
            [{"id": "local", "auth_mode": "bearer", "bearer_token_only": True}]
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "not_applicable")
        self.assertEqual(report["applicability"], "not_applicable")
        self.assertEqual(report["summary"]["detected_config_count"], 0)
        self.assertEqual(report["configs"][0]["outcome"], "not_applicable")

    def test_export_writes_json_and_markdown_without_mutating_sources(self):
        fixture_path = self._copy_fixture("mcp_poisoned_tools.json")

        report = self.server.mcp_threat_model_report(
            fixture_path=fixture_path,
            export=True,
        )

        json_path = self.repo_path / report["exports"]["json"]
        markdown_path = self.repo_path / report["exports"]["markdown"]
        self.assertTrue(json_path.exists())
        self.assertTrue(markdown_path.exists())
        exported = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(exported["schema"], "mcp_threat_model_report.v1")
        markdown = markdown_path.read_text(encoding="utf-8")
        self.assertIn("# MCP threat-model report", markdown)
        self.assertIn("## STRIDE coverage", markdown)
        self.assertEqual(len(report["resource_links"]), 2)
