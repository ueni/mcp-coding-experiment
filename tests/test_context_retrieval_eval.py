# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import subprocess
import sys
import unittest
from pathlib import Path

from scripts import context_retrieval_eval
from tests.server_test_support import load_server_module


class ContextRetrievalEvalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server_module()
        cls.repo_root = Path(__file__).resolve().parents[1]

    def test_fixture_set_shape_and_required_coverage(self):
        fixture_set = context_retrieval_eval.load_fixture_set()

        self.assertEqual(fixture_set["schema"], "context_retrieval_fixture_set.v1")
        fixtures = fixture_set["fixtures"]
        self.assertGreaterEqual(len(fixtures), 5)
        coverage = {fixture["coverage"] for fixture in fixtures}
        self.assertTrue(
            {
                "review",
                "release-readiness",
                "test-impact",
                "devcontainer-health",
                "rollback-snapshot-routing",
            }.issubset(coverage)
        )
        for fixture in fixtures:
            self.assertTrue(fixture["prompt"].strip())
            self.assertTrue(fixture["task"].strip())
            self.assertTrue(fixture["gold_context_anchors"])
            self.assertTrue(fixture["expected_top_workflow_card"].strip())

    def test_evaluator_reports_context_retrieval_metrics(self):
        report = context_retrieval_eval.evaluate_context_retrieval(
            route_fn=self.server.task_router
        )

        self.assertEqual(report["schema"], "context_retrieval_regression_report.v1")
        self.assertEqual(report["summary"]["fixture_count"], 5)
        self.assertGreaterEqual(report["summary"]["mean_recall"], 0.8)
        self.assertGreaterEqual(report["summary"]["mean_efficiency"], 0.55)
        self.assertEqual(report["summary"]["top_workflow_card_accuracy"], 1.0)
        self.assertTrue(report["summary"]["passed_thresholds"])
        for result in report["results"]:
            self.assertIn("top_workflow_card", result)
            self.assertTrue(result["top_workflow_card_match"])
            self.assertTrue(result["retrieved_context_anchors"])
            metrics = result["metrics"]
            for key in ("recall", "precision", "efficiency"):
                self.assertIn(key, metrics)
                self.assertGreaterEqual(metrics[key], 0.0)
                self.assertLessEqual(metrics[key], 1.0)

    def test_cli_outputs_deterministic_json_report(self):
        script = self.repo_root / "scripts" / "context_retrieval_eval.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--fail-on-threshold", "--indent", "2"],
            check=True,
            capture_output=True,
            cwd=self.repo_root,
            text=True,
        )
        report = json.loads(completed.stdout)

        self.assertEqual(report["schema"], "context_retrieval_regression_report.v1")
        self.assertTrue(report["summary"]["passed_thresholds"])


if __name__ == "__main__":
    unittest.main()
