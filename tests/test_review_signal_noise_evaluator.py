# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from source.review_signal_noise_evaluator import (
    EVALUATION_SCHEMA,
    evaluate_review_fixtures,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "review_evaluation"


class ReviewSignalNoiseEvaluatorTests(unittest.TestCase):
    def test_default_fixture_pack_reports_clean_signal_noise_metrics(self):
        result = evaluate_review_fixtures(FIXTURE_DIR, repo_root=REPO_ROOT)

        self.assertEqual(result["schema"], EVALUATION_SCHEMA)
        self.assertTrue(result["read_only"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"]["fixtures"], 3)
        self.assertEqual(result["summary"]["expected_findings"], 1)
        self.assertEqual(result["summary"]["true_positives"], 1)
        self.assertEqual(result["summary"]["missed_findings"], 0)
        self.assertEqual(result["summary"]["spurious_findings"], 0)
        self.assertEqual(result["summary"]["precision"], 1.0)
        self.assertEqual(result["summary"]["recall"], 1.0)
        self.assertEqual(result["spurious_findings"], [])
        self.assertIn(
            "tests/fixtures/review_evaluation/true_positive_missing_auth/diff.patch",
            result["evidence_paths"],
        )

    def test_noisy_review_output_is_counted_as_spurious(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            (output_dir / "false-positive-safe-refactor.json").write_text(
                json.dumps(
                    {
                        "schema": "review_output_findings.v1",
                        "findings": [
                            {
                                "id": "auth-regression",
                                "severity": "blocker",
                                "title": "Authorization blocker in admin helper rename",
                                "message": "The admin authorization check might have been removed.",
                                "path": "source/auth.py",
                                "line": 9,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = evaluate_review_fixtures(
                FIXTURE_DIR,
                actual_output_dir=output_dir,
                repo_root=REPO_ROOT,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["summary"]["spurious_findings"], 1)
        self.assertEqual(result["summary"]["precision"], 0.5)
        self.assertEqual(result["summary"]["recall"], 1.0)
        self.assertEqual(
            result["spurious_findings"][0]["fixture_id"],
            "false-positive-safe-refactor",
        )
        self.assertEqual(
            result["spurious_findings"][0]["matched_should_not_flag_ids"],
            ["safe-auth-helper-rename"],
        )
        self.assertFalse(result["threshold_status"]["checks"]["spurious_findings"])

    def test_missed_true_positive_fails_recall_threshold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            (output_dir / "true-positive-missing-auth.json").write_text(
                json.dumps({"schema": "review_output_findings.v1", "findings": []}),
                encoding="utf-8",
            )

            result = evaluate_review_fixtures(
                FIXTURE_DIR,
                actual_output_dir=output_dir,
                repo_root=REPO_ROOT,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["summary"]["missed_findings"], 1)
        self.assertEqual(result["summary"]["recall"], 0.0)
        self.assertFalse(result["threshold_status"]["checks"]["recall"])
        missed = result["fixtures"][0]["missed_findings"][0]
        self.assertEqual(missed["id"], "authz-delete-user")
        self.assertIn("diff.patch", missed["evidence_paths"][0])

    def test_cli_prints_structured_json_and_passes_clean_pack(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "review_signal_noise_evaluator.py"),
                "--fixture-dir",
                str(FIXTURE_DIR),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["schema"], EVALUATION_SCHEMA)
        self.assertTrue(result["ok"])
        self.assertIn("spurious_findings", result)


if __name__ == "__main__":
    unittest.main()
