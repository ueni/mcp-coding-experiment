# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import unittest

from tests.server_test_support import load_server_module


class WorkflowSkillSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server_module()

    def test_seed_cards_use_versioned_schema_and_required_fields(self):
        required = {
            "id",
            "schema",
            "title",
            "intent",
            "entrypoints",
            "prerequisites",
            "risk_level",
            "mutation_mode",
            "outputs",
            "do_not_use_when",
            "caveats",
        }
        expected_ids = {
            "release-readiness",
            "devcontainer-health",
            "snapshot-before-refactor",
            "security-triage",
            "test-impact",
            "governance-report",
            "workflow-diagnostics",
        }
        cards = self.server.WORKFLOW_SKILL_CARDS
        self.assertEqual({card["id"] for card in cards}, expected_ids)
        for card in cards:
            with self.subTest(card=card["id"]):
                public = self.server._workflow_skill_public_card(card)
                self.assertEqual(set(public), required)
                self.assertEqual(public["schema"], "workflow_card.v1")
                self.assertIn(public["risk_level"], {"low", "medium", "high"})
                self.assertTrue(public["entrypoints"])
                self.assertTrue(public["prerequisites"])
                self.assertTrue(public["outputs"])
                self.assertTrue(public["do_not_use_when"])

    def test_representative_phrasings_rank_expected_workflow_first(self):
        cases = {
            "Are we ready to ship this release candidate?": "release-readiness",
            "VS Code devcontainer cannot reach the MCP endpoint on port 8000": "devcontainer-health",
            "Before a risky refactor, create a rollback snapshot": "snapshot-before-refactor",
            "Triage this auth bypass and possible leaked token": "security-triage",
            "Which focused tests should I run for these changed files?": "test-impact",
            "Export governance evidence and provenance for audit": "governance-report",
            "Diagnose why the workflow failed and suggest recovery": "workflow-diagnostics",
        }
        for prompt, expected in cases.items():
            with self.subTest(prompt=prompt):
                result = self.server.workflow_skill_search(prompt=prompt, top_k=3)
                self.assertEqual(result["schema"], "workflow_skill_search.v1")
                self.assertTrue(result["read_only"])
                self.assertEqual(result["matches"][0]["id"], expected)
                self.assertGreaterEqual(result["matches"][0]["confidence"], 0.5)

    def test_high_risk_tasks_surface_snapshot_clarification_and_release_gates(self):
        result = self.server.workflow_skill_search(
            prompt="Deploy to production after a broad refactor and security fix",
            top_k=3,
        )
        caveats = " ".join(result["caveats"])
        match_ids = {match["id"] for match in result["matches"]}
        self.assertIn("release-readiness", match_ids)
        self.assertIn("snapshot-before-refactor", match_ids)
        self.assertIn("clarification_gate", caveats)
        self.assertIn("snapshot/rollback", caveats)
        self.assertIn("release_readiness", caveats)
        self.assertTrue({"deploy", "production", "refactor", "security"}.intersection(result["high_risk_terms"]))

    def test_unknown_prompt_returns_read_only_low_confidence_caveat(self):
        result = self.server.workflow_skill_search(prompt="organize the thing", top_k=3)
        self.assertEqual(result["matches"], [])
        self.assertIn("task_router", " ".join(result["caveats"]))
        self.assertTrue(result["read_only"])


if __name__ == "__main__":
    unittest.main()
