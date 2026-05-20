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
        self.assertTrue(transition["evidence"]["observed_notifications_tools_list_changed"])
        self.assertTrue(transition["evidence"]["observed_repeated_tools_list"])

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
        self.assertEqual(report["summary"]["high_uncovered_finding_count"], 2)
        self.assertIn(
            "high_uncovered_regression",
            {failure["type"] for failure in report["baseline"]["failures"]},
        )

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
