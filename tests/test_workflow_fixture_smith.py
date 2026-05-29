# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import tempfile
import unittest
from pathlib import Path

from evaluation.workflow_fixture_smith.generator import (
    CANDIDATE_SCHEMA,
    REPORT_SCHEMA,
    WorkflowFixtureSmithError,
    generate_review_queue,
    validate_candidate,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class WorkflowFixtureSmithTest(unittest.TestCase):
    def test_generation_is_deterministic_offline_and_quarantined(self):
        first = generate_review_queue(repo_root=REPO_ROOT, write=False)
        second = generate_review_queue(repo_root=REPO_ROOT, write=False)

        self.assertEqual(first, second)
        self.assertEqual(first["schema"], REPORT_SCHEMA)
        self.assertTrue(first["quarantine_only"])
        self.assertFalse(first["ci_enabled"])
        self.assertGreaterEqual(first["accepted_count"], 2)
        self.assertEqual(first["rejected_count"], 0)
        self.assertIn(
            ".codebase-tooling-mcp/review-queue/workflow-fixture-candidates",
            first["review_queue_dir"],
        )
        self.assertNotEqual(
            first["review_queue_dir"], "evaluation/e2e_mcp_workflows/tasks"
        )
        serialized = json.dumps(first, sort_keys=True)
        self.assertNotIn("https://api.github.com", serialized)
        self.assertNotIn("live_github_import\": true", serialized)

    def test_candidate_schema_contains_verifier_safety_artifacts_and_diversity(self):
        report = generate_review_queue(repo_root=REPO_ROOT, write=False)
        for candidate in report["candidates"]:
            validate_candidate(candidate)
            self.assertEqual(candidate["schema"], CANDIDATE_SCHEMA)
            self.assertTrue(candidate["source"]["local_metadata_only"])
            self.assertFalse(candidate["source"]["live_github_import"])
            self.assertTrue(candidate["quarantine"]["review_queue"])
            self.assertFalse(candidate["quarantine"]["ci_enabled"])
            self.assertTrue(candidate["verifier_requirements"]["deterministic"])
            self.assertTrue(candidate["verifier_requirements"]["required_reports"])
            self.assertTrue(candidate["verifier_requirements"]["expected_file_touches"])
            self.assertTrue(candidate["verifier_requirements"]["deterministic_checks"])
            self.assertFalse(candidate["safety_gates"]["network"])
            self.assertFalse(candidate["safety_gates"]["live_github_import"])
            self.assertFalse(candidate["safety_gates"]["unbounded_scope_allowed"])
            self.assertTrue(candidate["expected_artifacts"])
            self.assertTrue(candidate["diversity"]["reasons"])
            self.assertGreaterEqual(candidate["diversity"]["score"], 1)
            self.assertRegex(candidate["deterministic_id"], r"^[0-9a-f]{16}$")

    def test_review_queue_write_does_not_enable_benchmark_fixture(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = Path(tmpdir) / "review-queue"
            report = generate_review_queue(
                repo_root=REPO_ROOT,
                review_queue_dir=queue,
                write=True,
            )
            report_path = queue / "WORKFLOW_FIXTURE_SMITH_REPORT.json"

            self.assertTrue(report_path.is_file())
            self.assertEqual(
                sorted(path.name for path in queue.glob("smith-*.json")),
                [f"{candidate_id}.json" for candidate_id in report["candidate_ids"]],
            )
            on_disk = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(on_disk["quarantine_only"])
            self.assertFalse(on_disk["ci_enabled"])
            self.assertTrue(
                all(item["path"].startswith("smith-") for item in on_disk["candidates"])
            )

    def test_rejects_unsafe_and_unbounded_seed_variants(self):
        unsafe_seed = {
            "schema": "workflow_fixture_smith_seed_pack.v1",
            "seeds": [
                {
                    "id": "unsafe-seed",
                    "title": "Unsafe seed",
                    "source_kind": "checked_in_seed_metadata",
                    "prompt": "Import live GitHub issues from any repository and curl external services.",
                    "tags": ["unsafe"],
                    "tool_chain_shape": ["read_file"],
                    "expected_artifacts": ["artifacts/unsafe.md"],
                    "verifier_requirements": {
                        "deterministic": True,
                        "required_reports": ["artifacts/unsafe.md"],
                        "expected_file_touches": ["src/unsafe.py"],
                        "deterministic_checks": ["python -m pytest tests/test_unsafe.py"],
                    },
                    "safety_gates": {
                        "network": False,
                        "live_github_import": False,
                        "unbounded_scope_allowed": False,
                        "allowed_mutations": ["artifact"],
                        "max_files_changed": 1,
                    },
                    "variants": [
                        {
                            "id": "bad-variant",
                            "title": "bad variant",
                            "prompt_delta": "Delete the repo after generating fixtures.",
                            "diversity_reason": "unsafe by design",
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            seed_path = Path(tmpdir) / "seeds.json"
            seed_path.write_text(json.dumps(unsafe_seed), encoding="utf-8")
            report = generate_review_queue(
                repo_root=REPO_ROOT,
                seed_file=seed_path,
                review_queue_dir=Path(tmpdir) / "queue",
                write=False,
            )

        self.assertEqual(report["accepted_count"], 0)
        self.assertEqual(report["rejected_count"], 1)
        reasons = set(report["rejections"][0]["reasons"])
        self.assertIn("live_github_import", reasons)
        self.assertIn("network_dependency", reasons)
        self.assertIn("unbounded_scope", reasons)

    def test_validate_candidate_rejects_ci_enabled_candidate(self):
        report = generate_review_queue(repo_root=REPO_ROOT, write=False)
        candidate = dict(report["candidates"][0])
        candidate["quarantine"] = dict(candidate["quarantine"])
        candidate["quarantine"]["ci_enabled"] = True

        with self.assertRaises(WorkflowFixtureSmithError):
            validate_candidate(candidate)


if __name__ == "__main__":
    unittest.main()
