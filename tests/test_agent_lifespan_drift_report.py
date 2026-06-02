# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

from source.tool_output_schemas import TOOL_OUTPUT_SCHEMAS, validate_against_schema
from tests.server_test_support import ServerToolsTestBase


class AgentLifespanDriftReportTests(ServerToolsTestBase):
    def test_deterministic_fixtures_cover_pass_warn_and_block_cases(self):
        report = self.server.agent_lifespan_drift_report(export=False)

        self.assertEqual(report["schema"], "agent_lifespan_drift_report.v1")
        self.assertTrue(report["read_only"])
        self.assertTrue(report["advisory_only"])
        self.assertEqual(report["status"], "block")
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["summary"]["pass_fixture_count"], 1)
        self.assertGreaterEqual(report["summary"]["warn_fixture_count"], 1)
        self.assertGreaterEqual(report["summary"]["block_fixture_count"], 1)
        rule_ids = {finding["rule_id"] for finding in report["findings"]}
        self.assertIn("stale_revision_not_superseded", rule_ids)
        self.assertIn("interference_overrode_current_fact", rule_ids)
        validate_against_schema(report, TOOL_OUTPUT_SCHEMAS["agent_lifespan_drift_report"])

    def test_findings_are_stage_attributed_and_redacted_repo_relative(self):
        report = self.server.agent_lifespan_drift_report(export=False)

        stages = report["summary"]["by_stage"]
        for stage in ("write", "retrieval", "revision", "utilization"):
            self.assertIn(stage, stages)
        mechanisms = report["summary"]["by_mechanism"]
        self.assertGreaterEqual(mechanisms["interference_aging"], 1)
        self.assertGreaterEqual(mechanisms["revision_aging"], 1)
        payload = self.server.json.dumps(report)
        self.assertNotIn(str(self.repo_path), payload)
        self.assertFalse(report["security"]["raw_memory_content_included"])
        self.assertFalse(report["security"]["host_absolute_paths_included"])
        for finding in report["findings"]:
            self.assertIn(finding["stage"], {"write", "retrieval", "revision", "utilization"})
            self.assertIn(finding["mechanism"], {"compression_aging", "interference_aging", "revision_aging", "maintenance_aging"})
            self.assertFalse(finding["evidence"]["raw_values_included"])
            for rel_path in finding["repo_paths"]:
                self.assertFalse(rel_path.startswith("/"), rel_path)
                self.assertNotIn("..", rel_path.split("/"), rel_path)

    def test_governance_report_includes_compact_additive_summary(self):
        report = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=False)

        self.assertIn("memory_governance", report)
        drift = report["agent_lifespan_drift"]
        self.assertEqual(drift["schema"], "agent_lifespan_drift_summary.v1")
        self.assertEqual(drift["status"], "block")
        self.assertIn("write", drift["by_stage"])
        self.assertIn("retrieval", drift["by_stage"])
        self.assertTrue(drift["redacted"])

    def test_export_writes_json_markdown_and_resource_links(self):
        report = self.server.agent_lifespan_drift_report(export=True)

        self.assertIn("json", report["exports"])
        self.assertIn("markdown", report["exports"])
        self.assertEqual(len(report["resource_links"]), 2)
        for rel_path in report["exports"].values():
            self.assertTrue((self.repo_path / rel_path).is_file(), rel_path)
