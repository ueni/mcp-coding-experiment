# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evaluation.e2e_mcp_workflows.runner import (
    REPORT_SCHEMA,
    TASK_SCHEMA,
    load_task_fixtures,
    run_benchmark_suite,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class E2EMcpWorkflowBenchmarksTest(unittest.TestCase):
    def test_fixture_pack_shape_and_coverage(self):
        fixtures = load_task_fixtures(repo_root=REPO_ROOT)

        self.assertGreaterEqual(len(fixtures), 5)
        fixture_ids = {fixture["id"] for fixture in fixtures}
        self.assertTrue(
            {
                "read-only-api-triage",
                "one-file-edit-with-tests",
                "snapshot-rollback-config",
                "release-readiness-summary",
                "dependency-security-gate",
            }.issubset(fixture_ids)
        )
        self.assertEqual(len(fixture_ids), len(fixtures))
        for fixture in fixtures:
            self.assertEqual(fixture["schema"], TASK_SCHEMA)
            self.assertTrue(fixture["prompt"].strip())
            self.assertTrue(fixture["setup"]["files"])
            self.assertFalse(fixture["allowed"].get("network"))
            self.assertTrue(fixture["allowed"]["tools"])
            self.assertIn("repo", fixture["allowed"]["mutations"])
            self.assertIn("artifact", fixture["allowed"]["mutations"])
            self.assertIsInstance(fixture["verification"]["commands"], list)
            self.assertTrue(fixture["verification"]["expected_artifacts"])
            self.assertEqual(fixture["baseline"]["runner"], "direct")
            self.assertTrue(fixture["baseline"]["actions"])

    def test_direct_baseline_reports_required_metrics_without_private_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report = run_benchmark_suite(report_dir=tmpdir, repo_root=REPO_ROOT)

        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"]["passed"], report["summary"]["tasks"])
        self.assertGreaterEqual(report["summary"]["tasks"], 5)
        self.assertGreater(report["summary"]["tool_calls"], 0)
        self.assertGreater(report["summary"]["estimated_tokens"], 0)
        self.assertGreaterEqual(report["summary"]["safety_gates_required"], 5)
        self.assertEqual(
            report["summary"]["safety_gates_satisfied"],
            report["summary"]["safety_gates_required"],
        )
        self.assertGreaterEqual(report["summary"]["snapshots_created"], 1)
        self.assertGreaterEqual(report["summary"]["rollbacks_restored"], 1)
        self.assertGreaterEqual(report["summary"]["test_gate_passed"], 1)
        self.assertFalse(report["retention_policy"]["stores_raw_transcripts"])
        self.assertFalse(report["retention_policy"]["stores_command_output"])
        self.assertTrue(report["self_optimization_inputs"]["safe_for_local_retention"])
        self.assertFalse(report["self_optimization_inputs"]["raw_transcripts_persisted"])
        self.assertFalse(report["self_optimization_inputs"]["repo_external_paths_persisted"])
        self.assertIn("json", report["report_paths"])
        self.assertFalse(report["report_paths"]["json"].startswith("/"))

        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("/tmp/mcp-e2e-", serialized)
        self.assertNotIn("Authorization: Bearer", serialized)
        self.assertNotIn("BEGIN PRIVATE KEY", serialized)

    def test_agent_profile_without_hook_reports_configured_blocker(self):
        report = run_benchmark_suite(
            task_ids=["read-only-api-triage"],
            runner="offline-onboard-only",
            repo_root=REPO_ROOT,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["tasks"][0]["status"], "hook_not_configured")
        self.assertEqual(
            report["tasks"][0]["failures"][0]["reason"],
            "agent_hook_not_configured",
        )

    def test_cli_outputs_structured_report_for_selected_task(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "e2e_mcp_workflow_benchmarks.py"),
                "--task-id",
                "read-only-api-triage",
                "--fail-on-benchmark-failure",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertTrue(report["ok"])
        self.assertEqual(report["fixture_count"], 1)
        self.assertEqual(report["tasks"][0]["id"], "read-only-api-triage")


if __name__ == "__main__":
    unittest.main()
