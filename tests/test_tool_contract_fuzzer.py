# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

from pathlib import Path

from source.tool_contract_fuzzer import ToolFuzzCase, run_tool_contract_fuzz
from tests.server_test_support import ServerToolsTestBase


class ToolContractFuzzerTests(ServerToolsTestBase):
    def test_safe_default_corpus_is_deterministic_and_contract_checked(self):
        report_one = run_tool_contract_fuzz(seed=106, server_module=self.server)
        report_two = run_tool_contract_fuzz(seed=106, server_module=self.server)

        self.assertTrue(report_one["ok"], report_one["findings"])
        self.assertEqual(report_one, report_two)
        self.assertTrue(report_one["safe_default"])
        self.assertFalse(report_one["mutation"]["enabled"])
        self.assertGreaterEqual(report_one["summary"]["covered_public_surfaces"], 5)
        self.assertGreaterEqual(report_one["summary"]["schema_validations"], 5)
        self.assertGreaterEqual(report_one["summary"]["error_schema_validations"], 1)
        self.assertTrue(report_one["coverage"]["meets_initial_acceptance"])
        self.assertIn("repo_info", report_one["coverage"]["schema_backed_tools"])
        self.assertIn(
            "grep:invalid_regex", report_one["coverage"]["error_path_heavy_cases"]
        )
        for case in report_one["cases"]:
            self.assertTrue(case["read_only"])
            self.assertFalse(case["mutation"])

    def test_findings_include_minimized_replay_expected_and_actual(self):
        class BrokenServer:
            REPO_PATH = Path("/tmp/tool-contract-fuzzer-test")
            ALLOW_MUTATIONS = False

            @staticmethod
            def repo_info():
                return {"repo_exists": True}

        report = run_tool_contract_fuzz(
            seed=7,
            server_module=BrokenServer,
            cases=[
                ToolFuzzCase(
                    case_id="repo_info:broken-contract",
                    tool="repo_info",
                    schema_tool="repo_info",
                    allow_absolute_repo_path=True,
                )
            ],
        )

        self.assertFalse(report["ok"])
        finding = report["findings"][0]
        self.assertEqual(finding["schema"], "tool_contract_fuzz_finding.v1")
        self.assertEqual(finding["tool"], "repo_info")
        self.assertEqual(finding["seed"], 7)
        self.assertIn("expected", finding)
        self.assertIn("actual", finding)
        self.assertIn("contract_category", finding)
        self.assertIn("security_category", finding)
        self.assertEqual(finding["repro"]["case_id"], "repo_info:broken-contract")
        self.assertEqual(
            finding["minimized_replay"]["schema"], "tool_contract_fuzz_replay.v1"
        )

    def test_mutation_fuzzing_requires_explicit_snapshot_gate(self):
        with self.assertRaisesRegex(ValueError, "mutation snapshot label"):
            run_tool_contract_fuzz(
                seed=106,
                server_module=self.server,
                include_mutations=True,
            )
