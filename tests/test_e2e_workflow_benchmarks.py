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
    DEFAULT_FIXTURE_DIR,
    REPORT_SCHEMA,
    TASK_SCHEMA,
    load_task_fixtures,
    run_benchmark_suite,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class E2EWorkflowBenchmarkTests(unittest.TestCase):
    def test_fixture_pack_declares_required_contract_fields(self):
        fixtures = load_task_fixtures(DEFAULT_FIXTURE_DIR, repo_root=REPO_ROOT)

        self.assertGreaterEqual(len(fixtures), 5)
        for fixture in fixtures:
            self.assertEqual(fixture["schema"], TASK_SCHEMA)
            self.assertTrue(fixture["prompt"])
            self.assertIsInstance(fixture["setup"]["files"], dict)
            self.assertIn("tools", fixture["allowed"])
            self.assertIn("mutations", fixture["allowed"])
            self.assertFalse(fixture["allowed"].get("network"))
            self.assertIsInstance(fixture["verification"]["commands"], list)
            self.assertIsInstance(fixture["verification"]["expected_artifacts"], list)
            self.assertIn("safety", fixture["invariants"])
            self.assertIn("trajectory", fixture["invariants"])
            self.assertEqual(fixture["baseline"]["runner"], "direct")
            self.assertTrue(fixture["baseline"]["actions"])

    def test_direct_baseline_runs_all_tasks_and_writes_redacted_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report = run_benchmark_suite(
                DEFAULT_FIXTURE_DIR,
                report_dir=tmpdir,
                repo_root=REPO_ROOT,
            )
            serialized = json.dumps(report, sort_keys=True)

            self.assertEqual(report["schema"], REPORT_SCHEMA)
            self.assertTrue(report["ok"])
            self.assertGreaterEqual(report["summary"]["tasks"], 5)
            self.assertEqual(report["summary"]["failed"], 0)
            self.assertGreater(report["summary"]["tool_calls"], 0)
            self.assertGreater(report["summary"]["estimated_tokens"], 0)
            self.assertGreater(report["summary"]["safety_gates_required"], 0)
            self.assertGreaterEqual(report["summary"]["snapshots_created"], 1)
            self.assertGreaterEqual(report["summary"]["rollbacks_restored"], 1)
            self.assertFalse(report["retention_policy"]["stores_raw_transcripts"])
            self.assertFalse(report["retention_policy"]["stores_host_absolute_paths"])
            self.assertNotIn(tmpdir, serialized)

            json_report = Path(tmpdir) / "E2E_MCP_WORKFLOW_BENCHMARKS.json"
            markdown_report = Path(tmpdir) / "E2E_MCP_WORKFLOW_BENCHMARKS.md"
            self.assertTrue(json_report.is_file())
            self.assertTrue(markdown_report.is_file())

            for task in report["tasks"]:
                metrics = task["metrics"]
                self.assertTrue(task["ok"], task["id"])
                self.assertIn("tool_calls", metrics)
                self.assertIn("approximate_volume", metrics)
                self.assertIn("retries_rework", metrics)
                self.assertIn("safety_gate_coverage", metrics)
                self.assertIn("snapshot_rollback", metrics)
                self.assertIn("test_gate", metrics)
                self.assertIn("trajectory_order_findings", metrics)
                self.assertIn("commands", task["verification"])
                self.assertIn("expected_artifacts", task["verification"])

    def test_cli_prints_structured_json_and_respects_failure_exit_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "evaluation.e2e_mcp_workflows.runner",
                    "--fixture-dir",
                    str(DEFAULT_FIXTURE_DIR),
                    "--report-dir",
                    tmpdir,
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
            self.assertIn("self_optimization_inputs", report)


if __name__ == "__main__":
    unittest.main()
