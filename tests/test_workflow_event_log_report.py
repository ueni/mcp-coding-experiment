# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

import json

from source.tool_output_schemas import TOOL_OUTPUT_SCHEMAS, validate_against_schema
from tests.server_test_support import ServerToolsTestBase


FIXTURE_DIR = "tests/fixtures/workflow_event_logs"


class WorkflowEventLogReportTests(ServerToolsTestBase):
    def _copy_fixture(self, name: str) -> str:
        source = self.server.Path(__file__).resolve().parent / "fixtures" / "workflow_event_logs" / name
        target = self.repo_path / FIXTURE_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return f"{FIXTURE_DIR}/{name}"

    def test_fixture_projection_schema_timeline_and_artifact_lineage_are_stable(self):
        event_log = self._copy_fixture("audit_workflow.jsonl")

        first = self.server.workflow_event_log_report(event_log_path=event_log)
        second = self.server.workflow_event_log_report(event_log_path=event_log)

        validate_against_schema(first, TOOL_OUTPUT_SCHEMAS["workflow_event_log_report"])
        self.assertEqual(first["schema"], "workflow_checkpoint_report.v1")
        self.assertTrue(first["ok"])
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["report_id"], second["report_id"])
        self.assertTrue(first["read_only"])
        self.assertEqual(first["projection"]["schema"], "workflow_event_projection.v1")
        self.assertTrue(first["projection"]["deterministic"])
        self.assertFalse(first["projection"]["reran_tools"])
        self.assertEqual(first["summary"]["event_count"], 12)
        self.assertGreaterEqual(first["summary"]["checkpoint_count"], 6)
        event_types = first["projection"]["event_type_counts"]
        for required_type in (
            "workflow.started",
            "workflow.checkpoint",
            "guard.decision",
            "test.gate",
            "release.gate",
            "release.readiness",
            "workflow.fork",
            "workflow.ended",
        ):
            self.assertIn(required_type, event_types)
        artifact_ids = {row["artifact_id"] for row in first["projection"]["artifact_lineage"]}
        self.assertIn("plan", artifact_ids)
        self.assertIn("test-report", artifact_ids)

    def test_fork_marker_diff_compares_parent_and_branch_without_rerun(self):
        event_log = self._copy_fixture("audit_workflow.jsonl")

        report = self.server.workflow_event_log_report(
            event_log_path=event_log,
            fork_id="safer-branch",
            parent_fork_id="main",
        )

        diff = report["fork_diff"]
        self.assertEqual(diff["schema"], "workflow_fork_diff.v1")
        self.assertTrue(diff["available"])
        self.assertEqual(diff["fork_id"], "safer-branch")
        self.assertEqual(diff["parent_fork_id"], "main")
        changed = {row["artifact_id"]: row for row in diff["changed_artifacts"]}
        self.assertEqual(changed["plan"]["change"], "changed")
        self.assertEqual(changed["test-report"]["change"], "changed")
        self.assertIn("evt-fork", diff["added_event_ids"])

    def test_missing_and_corrupt_event_logs_return_auditable_errors(self):
        missing = self.server.workflow_event_log_report(
            event_log_path=f"{FIXTURE_DIR}/does-not-exist.jsonl"
        )
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["status"], "missing")
        self.assertEqual(missing["summary"]["event_count"], 0)
        self.assertEqual(missing["errors"][0]["code"], "missing")

        corrupt_path = self._copy_fixture("corrupt_event_log.jsonl")
        corrupt = self.server.workflow_event_log_report(event_log_path=corrupt_path)
        self.assertFalse(corrupt["ok"])
        self.assertEqual(corrupt["status"], "corrupt")
        self.assertEqual(corrupt["summary"]["event_count"], 2)
        self.assertEqual(corrupt["errors"][0]["code"], "corrupt_json")
        self.assertNotIn("this is not json", json.dumps(corrupt, sort_keys=True))

    def test_privacy_redaction_removes_raw_prompts_tool_outputs_secrets_and_host_paths(self):
        event_log = self._copy_fixture("privacy_violation.jsonl")

        report = self.server.workflow_event_log_report(event_log_path=event_log)
        encoded = json.dumps(report, sort_keys=True)

        self.assertTrue(report["ok"])
        self.assertGreater(report["privacy"]["redaction_count"], 0)
        self.assertFalse(report["privacy"]["raw_prompts_persisted"])
        self.assertFalse(report["privacy"]["raw_tool_outputs_persisted"])
        self.assertFalse(report["privacy"]["secrets_persisted"])
        self.assertFalse(report["privacy"]["absolute_host_paths_persisted"])
        self.assertIn("sensitive_key", report["privacy"]["redaction_categories"])
        self.assertIn("absolute_path", report["privacy"]["redaction_categories"])
        self.assertNotIn("PRIVATE PROMPT", encoded)
        self.assertNotIn("RAW TOOL OUTPUT", encoded)
        self.assertNotIn("super-secret-token", encoded)
        self.assertNotIn("ghp_1234567890abcdef", encoded)
        self.assertNotIn("/home/user/private", encoded)
