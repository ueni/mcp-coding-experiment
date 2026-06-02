# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from source.review_outcome_report import (
    OUTCOME_OPEN,
    OUTCOME_DISMISSED,
    OUTCOME_RESOLVED,
    OUTCOME_STALE,
    OUTCOME_UNVERIFIABLE,
    REPORT_SCHEMA,
    generate_review_outcome_report,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "review_outcomes"


class ReviewOutcomeReportTests(unittest.TestCase):
    def test_default_fixture_pack_classifies_resolution_outcomes(self):
        result = generate_review_outcome_report(FIXTURE_DIR, repo_root=REPO_ROOT)

        self.assertEqual(result["schema"], REPORT_SCHEMA)
        self.assertTrue(result["read_only"])
        self.assertFalse(result["network_used"])
        self.assertEqual(result["summary"]["fixtures"], 5)
        self.assertEqual(result["summary"]["findings"], 5)
        self.assertEqual(result["summary"]["outcome_counts"][OUTCOME_RESOLVED], 1)
        self.assertEqual(result["summary"]["outcome_counts"][OUTCOME_DISMISSED], 1)
        self.assertEqual(result["summary"]["outcome_counts"][OUTCOME_STALE], 1)
        self.assertEqual(result["summary"]["outcome_counts"][OUTCOME_OPEN], 1)
        self.assertEqual(result["summary"]["outcome_counts"][OUTCOME_UNVERIFIABLE], 1)
        self.assertEqual(result["summary"]["resolved_rate"], 0.2)
        self.assertEqual(result["summary"]["dismissed_noise_rate"], 0.2)
        self.assertEqual(result["summary"]["evidence_coverage"], 0.8)
        self.assertEqual(result["summary"]["line_evidence_coverage"], 0.8)
        self.assertEqual(result["summary"]["median_time_to_resolution_hours"], 2.5)
        self.assertTrue(result["gate_correlations"]["data_available"])
        self.assertEqual(
            result["self_optimization_inputs"]["resolution_metrics"]["dismissed_noise_rate"],
            0.2,
        )
        self.assertIn(
            "tests/fixtures/review_outcomes/resolved_feedback/metadata.json",
            result["evidence_paths"],
        )
        self.assertFalse(result["privacy"]["raw_review_comments_persisted"])
        self.assertFalse(result["privacy"]["raw_patch_text_persisted"])

    def test_unmatched_finding_with_evidence_is_left_open(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fixture_dir = root / "fixtures"
            case_dir = fixture_dir / "left_open"
            case_dir.mkdir(parents=True)
            (fixture_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": "review_outcome_manifest.v1",
                        "fixtures": ["left_open/fixture.json"],
                    }
                ),
                encoding="utf-8",
            )
            (case_dir / "fixture.json").write_text(
                json.dumps(
                    {
                        "schema": "review_outcome_fixture.v1",
                        "id": "left-open",
                        "findings": [
                            {
                                "id": "still-open",
                                "rule_id": "release-note",
                                "path": "docs/release.md",
                                "line": 5,
                                "comment_digest": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = generate_review_outcome_report(fixture_dir, repo_root=root)

        self.assertEqual(result["summary"]["outcome_counts"][OUTCOME_OPEN], 1)
        finding = result["findings"][0]
        self.assertEqual(finding["outcome"], OUTCOME_OPEN)
        self.assertEqual(finding["outcome_group"], "open")
        self.assertEqual(finding["reason"], "no terminal follow-up evidence")

    def test_export_writes_redacted_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            export_dir = Path(tmpdir) / "reports"
            result = generate_review_outcome_report(
                FIXTURE_DIR,
                repo_root=REPO_ROOT,
                export=True,
                export_dir=export_dir,
            )

            json_path = REPO_ROOT / result["exports"]["json"]
            md_path = REPO_ROOT / result["exports"]["markdown"]
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            exported = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(exported["schema"], REPORT_SCHEMA)
            self.assertIn("raw review comments", md_path.read_text(encoding="utf-8"))

    def test_cli_prints_structured_json(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "review_outcome_report.py"),
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
        self.assertEqual(result["schema"], REPORT_SCHEMA)
        self.assertEqual(result["summary"]["outcome_counts"][OUTCOME_RESOLVED], 1)


if __name__ == "__main__":
    unittest.main()
