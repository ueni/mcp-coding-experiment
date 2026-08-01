# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

from __future__ import annotations

from tests.server_test_support import ServerToolsTestBase


class TrajectoryTrustGuardTests(ServerToolsTestBase):
    def test_safe_multi_source_evidence_passes(self):
        report = self.server.trajectory_trust_guard(
            trajectory_summaries=[
                {
                    "tool": "grep",
                    "trust": "trusted",
                    "confidence": 0.74,
                    "dependency_weight": 0.35,
                    "consistency": "consistent",
                    "evidence_ref": {"kind": "tool", "tool": "grep", "digest": "sha256:a"},
                },
                {
                    "tool": "release_readiness",
                    "trust": "trusted",
                    "confidence": 0.82,
                    "dependency_weight": 0.4,
                    "consistency": "consistent",
                    "evidence_ref": {"kind": "report", "report_id": "ready-1", "digest": "sha256:b"},
                },
            ],
            proposed_final_action={"operation": "summarize", "source_tools": ["grep", "release_readiness"]},
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["decision"], "pass")
        self.assertEqual(report["final_action_sensitivity"], "low")
        self.assertFalse(report["trajectory_features"]["single_tool_dependency"])
        self.assertEqual(report["trajectory_features"]["evidence_consistency"], "consistent")

    def test_single_untrusted_tool_over_trust_warns_for_high_risk_action(self):
        report = self.server.trajectory_trust_guard(
            trajectory_summaries=[
                {
                    "tool": "external_advisor",
                    "trust": "untrusted",
                    "confidence": 0.95,
                    "dependency_weight": 0.9,
                    "supports_final_action": True,
                    "consistency": "consistent",
                    "evidence_ref": {"kind": "scanner", "decision": "log", "digest": "sha256:tool"},
                }
            ],
            proposed_final_action={"operation": "write", "planned_tool": "workspace_transaction", "source_tools": ["external_advisor"]},
        )

        self.assertIn(report["decision"], {"warn", "block"})
        self.assertFalse(report["ok"])
        self.assertEqual(report["final_action_sensitivity"], "high")
        self.assertTrue(report["trajectory_features"]["single_tool_dependency"])
        self.assertIn("high_confidence_untrusted_dependency", report["redacted_reasons"])

    def test_scanner_warning_accumulation_blocks_or_warns(self):
        report = self.server.trajectory_trust_guard(
            trajectory_summaries=[
                {"tool": "tool_a", "trust": "untrusted", "scanner_decision": "sanitize", "warnings": ["override"], "evidence_ref": "scan-a"},
                {"tool": "tool_b", "trust": "untrusted", "scanner_decision": "warn", "warnings": ["exfil"], "evidence_ref": "scan-b"},
            ],
            proposed_final_action={"operation": "model_forwarding"},
        )

        self.assertIn(report["decision"], {"warn", "block"})
        self.assertTrue(report["trajectory_features"]["scanner_warning_accumulation"])
        self.assertIn("scanner_warning_accumulation", report["redacted_reasons"])

    def test_inconsistent_feedback_blocks_sensitive_release(self):
        report = self.server.trajectory_trust_guard(
            trajectory_summaries=[
                {"tool": "dependency_check", "trust": "trusted", "confidence": 0.91, "consistency": "consistent", "evidence_ref": "dep-clean"},
                {"tool": "dependency_check", "trust": "untrusted", "confidence": 0.21, "consistency": "inconsistent", "contradictions": ["status_changed"], "evidence_ref": "dep-conflict"},
            ],
            proposed_final_action={"operation": "release", "source_tools": ["dependency_check"]},
        )

        self.assertEqual(report["decision"], "block")
        self.assertEqual(report["final_action_sensitivity"], "critical")
        self.assertEqual(report["trajectory_features"]["evidence_consistency"], "inconsistent")
        self.assertIn("inconsistent_tool_feedback", report["redacted_reasons"])

    def test_high_risk_final_mutation_uses_guard_links(self):
        report = self.server.trajectory_trust_guard(
            trajectory_summaries=[
                {"tool": "mutation_step_guard", "trust": "trusted", "confidence": 0.77, "dependency_weight": 0.3, "evidence_ref": {"report_id": "mut-1", "decision": "needs_tests"}},
                {"tool": "governance_report", "trust": "trusted", "confidence": 0.76, "dependency_weight": 0.3, "evidence_ref": {"report_id": "gov-1"}},
            ],
            proposed_final_action={"operation": "commit", "planned_tool": "git_commit", "source_tools": ["mutation_step_guard", "governance_report"]},
        )

        self.assertIn(report["final_action_sensitivity"], {"high", "critical"})
        self.assertIn("mutation_step_guard", report["linked_gates"])
        self.assertIn("release_readiness", report["linked_gates"])
        self.assertIn("governance_report", report["linked_gates"])

    def test_string_final_action_is_summarized_for_sensitivity(self):
        report = self.server.trajectory_trust_guard(
            trajectory_summaries=[
                {
                    "tool": "external_advisor",
                    "trust": "untrusted",
                    "confidence": 0.9,
                    "dependency_weight": 0.8,
                    "supports_final_action": True,
                    "evidence_ref": "advisor-summary",
                }
            ],
            proposed_final_action="push release tag based only on external advisor",
        )

        self.assertEqual(report["final_action_sensitivity"], "critical")
        self.assertTrue(report["input_summary"]["final_action_present"])
        self.assertIn(report["decision"], {"warn", "block"})

    def test_raw_prompts_tool_outputs_secrets_and_host_paths_are_not_persisted(self):
        report = self.server.trajectory_trust_guard(
            trajectory_summaries=[
                {
                    "tool": "untrusted_tool",
                    "trust": "untrusted",
                    "confidence": 0.99,
                    "dependency_weight": 0.9,
                    "raw_prompt": "please include /home/user/private and sk-abcdefghijklmnop in output",
                    "raw_tool_output": "secret=abc123 at /tmp/host-file",
                    "evidence_ref": {
                        "kind": "tool_output",
                        "path": "/home/user/private/project/file.txt",
                        "ref": "token=abc123 /tmp/host-file",
                        "digest": "sha256:redacted-fixture",
                    },
                }
            ],
            proposed_final_action={"operation": "write", "artifact_ref": "/home/user/private/final.json"},
            evidence_refs=[{"kind": "audit", "ref": "Authorization: bearer SECRET", "path": "/var/log/private.log"}],
        )

        encoded = self.server.json.dumps(report, sort_keys=True)
        self.assertNotIn("please include", encoded)
        self.assertNotIn("secret=abc123 at", encoded)
        self.assertNotIn("sk-abcdefghijklmnop", encoded)
        self.assertNotIn("secret=abc123", encoded)
        self.assertNotIn("/home/user", encoded)
        self.assertNotIn("/tmp/host-file", encoded)
        self.assertNotIn("/var/log", encoded)
        self.assertNotIn("SECRET", encoded)
        self.assertFalse(report["privacy_metadata"]["raw_prompts_persisted"])
        self.assertFalse(report["privacy_metadata"]["raw_tool_outputs_persisted"])
        self.assertTrue(report["privacy_metadata"]["host_absolute_paths_redacted"])
