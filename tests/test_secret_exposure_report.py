# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json

from tests.server_test_support import ServerToolsTestBase


class SecretExposureReportTests(ServerToolsTestBase):
    def _fake_github_token(self) -> str:
        return "ghp_" + ("A" * 36)

    def _fake_openai_key(self) -> str:
        return "sk-" + ("B" * 40)

    def _fake_aws_key(self) -> str:
        return "AKIA" + ("C" * 16)

    def _assert_report_is_redacted(self, report, *raw_values: str) -> None:
        encoded = json.dumps(report, sort_keys=True)
        for raw in raw_values:
            self.assertNotIn(raw, encoded)
        self.assertFalse(report["security"]["raw_secret_values_returned"])
        self.assertFalse(report["security"]["raw_file_lines_returned"])

    def test_clean_repository_reports_clean(self):
        report = self.server.secret_exposure_report(paths=["src"], baseline_ref="HEAD")

        self.assertEqual(report["schema"], "secret_exposure_report.v1")
        self.assertEqual(report["status"], "clean")
        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"]["finding_count"], 0)
        self.assertFalse(report["gate"]["would_block"])
        self.assertTrue(report["read_only"])

    def test_new_diff_finding_is_redacted_and_blocks_gate(self):
        raw_value = self._fake_openai_key()
        self.write_repo_text("src/new_client.py", "OPENAI_API_KEY = '" + raw_value + "'\n")

        report = self.server.secret_exposure_report(paths=["src"], baseline_ref="HEAD")

        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"]["new_high_confidence_count"], 1)
        self.assertTrue(report["gate"]["would_block"])
        finding = report["findings"][0]
        self.assertEqual(finding["path"], "src/new_client.py")
        self.assertEqual(finding["introduction"], "new")
        self.assertTrue(finding["fingerprint"].startswith("secretfp_sha256:"))
        self.assertIn("rotate", finding["remediation"].lower())
        self._assert_report_is_redacted(report, raw_value)

    def test_baseline_existing_and_generated_artifact_findings_are_classified(self):
        baseline_value = self._fake_aws_key()
        generated_value = "postgresql://user:" + ("D" * 30) + "@db.example/app"
        self.write_repo_text("src/legacy.py", "LEGACY_KEY = '" + baseline_value + "'\n")
        self.commit_all("add local scanner canary")
        self.write_repo_text(
            ".codebase-tooling-mcp/reports/generated.txt",
            "artifact_uri='" + generated_value + "'\n",
        )

        report = self.server.secret_exposure_report(paths=["."], baseline_ref="HEAD")
        by_path = {finding["path"]: finding for finding in report["findings"]}

        self.assertEqual(by_path["src/legacy.py"]["introduction"], "baseline")
        self.assertEqual(
            by_path[".codebase-tooling-mcp/reports/generated.txt"]["introduction"],
            "new",
        )
        self.assertEqual(report["summary"]["baseline_finding_count"], 1)
        self.assertEqual(report["summary"]["new_high_confidence_count"], 1)
        self._assert_report_is_redacted(report, baseline_value, generated_value)

    def test_allowlist_suppresses_test_canary_by_fingerprint(self):
        raw_value = self._fake_github_token()
        self.write_repo_text("tests/fixtures/local_canary.txt", raw_value + "\n")
        initial = self.server.secret_exposure_report(
            paths=["tests/fixtures/local_canary.txt"],
            baseline_ref="HEAD",
            block_on_high_confidence_new=True,
        )
        fingerprint = initial["findings"][0]["fingerprint"]
        self.write_repo_text(
            ".codebase-tooling-mcp/secret-exposure-allowlist.json",
            json.dumps({"allowlist": [{"fingerprint": fingerprint, "reason": "test canary"}]})
            + "\n",
        )

        report = self.server.secret_exposure_report(
            paths=["tests/fixtures/local_canary.txt"],
            baseline_ref="HEAD",
            allowlist_path=".codebase-tooling-mcp/secret-exposure-allowlist.json",
        )

        self.assertEqual(report["summary"]["finding_count"], 0)
        self.assertEqual(report["summary"]["suppressed_count"], 1)
        self.assertFalse(report["gate"]["would_block"])
        self._assert_report_is_redacted(report, raw_value)

    def test_large_and_binary_files_are_skipped(self):
        raw_value = self._fake_openai_key()
        binary_path = self.repo_path / "fixtures" / "binary.dat"
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        binary_path.write_bytes(b"\x00" + raw_value.encode("ascii"))
        self.write_repo_text("fixtures/large.txt", ("padding\n" * 200) + raw_value + "\n")

        report = self.server.secret_exposure_report(paths=["fixtures"], max_file_bytes=1024)
        reasons = {item["reason"] for item in report["skipped"]}

        self.assertEqual(report["summary"]["finding_count"], 0)
        self.assertIn("binary_or_unreadable", reasons)
        self.assertIn("large_file", reasons)
        self._assert_report_is_redacted(report, raw_value)

    def test_outside_repository_paths_are_skipped_without_host_path_exposure(self):
        raw_value = self._fake_openai_key()
        outside = self.repo_path.parent / "outside-secret.txt"
        outside.write_text(raw_value + "\n", encoding="utf-8")

        report = self.server.secret_exposure_report(paths=[str(outside)], baseline_ref="HEAD")

        self.assertEqual(report["summary"]["finding_count"], 0)
        self.assertEqual(report["inputs"]["paths"], ["<redacted:outside_repo>"])
        self.assertEqual(report["skipped"], [{"path": "<redacted:outside_repo>", "reason": "outside_repo_boundary"}])
        self.assertTrue(report["security"]["repo_boundary_enforced"])
        self.assertFalse(report["security"]["host_absolute_paths_exposed"])
        self.assertNotIn(str(outside), json.dumps(report, sort_keys=True))
        self._assert_report_is_redacted(report, raw_value)

    def test_outside_repository_paths_are_redacted_in_exports(self):
        raw_value = self._fake_openai_key()
        outside = self.repo_path.parent / "outside-export-secret.txt"
        outside.write_text(raw_value + "\n", encoding="utf-8")

        report = self.server.secret_exposure_report(paths=[str(outside)], baseline_ref="HEAD", export=True)

        exported = (self.repo_path / report["exports"]["json"]).read_text(encoding="utf-8")
        self.assertEqual(report["inputs"]["paths"], ["<redacted:outside_repo>"])
        self.assertNotIn(str(outside), exported)
        self.assertNotIn(raw_value, exported)
        self.assertFalse(report["security"]["host_absolute_paths_exposed"])

    def test_exports_are_redacted_and_linked(self):
        raw_value = self._fake_openai_key()
        self.write_repo_text("src/export_secret.py", "OPENAI_API_KEY = '" + raw_value + "'\n")

        report = self.server.secret_exposure_report(paths=["src"], baseline_ref="HEAD", export=True)

        self.assertIn("json", report["exports"])
        self.assertIn("markdown", report["exports"])
        self.assertEqual(len(report["resource_links"]), 2)
        for rel_path in report["exports"].values():
            exported = (self.repo_path / rel_path).read_text(encoding="utf-8")
            self.assertNotIn(raw_value, exported)
        self._assert_report_is_redacted(report, raw_value)

    def test_mutation_step_guard_blocks_only_in_scope_new_high_confidence_findings(self):
        raw_value = self._fake_openai_key()
        self.write_repo_text("src/secret_client.py", "OPENAI_API_KEY = '" + raw_value + "'\n")
        report = self.server.secret_exposure_report(paths=["src"], baseline_ref="HEAD")

        common = {
            "planned_tool": "workspace_transaction",
            "mode": "write",
            "argument_summary": {"path": "src/secret_client.py"},
            "declared_intent": "Update client code.",
            "expected_diff_shape": {"file_count": 1, "line_additions": 1, "line_deletions": 0},
            "selected_tests": ["tests/test_secret_exposure_report.py"],
            "invariant_audit_summary": {"ok_to_continue": True, "suspected_smells": []},
            "context_metadata": {"fresh": True, "tests_fresh": True},
            "secret_exposure": report,
        }
        blocked = self.server.mutation_step_guard(
            target_files=["src/secret_client.py"],
            **common,
        )
        unrelated = self.server.mutation_step_guard(
            target_files=["docs/a.md"],
            **{**common, "argument_summary": {"path": "docs/a.md"}},
        )

        self.assertEqual(blocked["decision"], "deny")
        self.assertIn(
            "high_confidence_new_secret_in_scope",
            blocked["decisive_deviation_risk"]["reasons"],
        )
        self.assertNotEqual(unrelated["decision"], "deny")
        self.assertFalse(unrelated["input_summary"]["secret_exposure"]["would_block"])
        self._assert_report_is_redacted(report, raw_value)

    def test_release_readiness_blocks_on_new_high_confidence_secret(self):
        raw_value = self._fake_openai_key()
        self.write_repo_text("src/release_secret.py", "OPENAI_API_KEY = '" + raw_value + "'\n")

        readiness = self.server.release_readiness(
            base_ref="HEAD",
            head_ref="HEAD",
            run_tests=False,
            run_docs_check=False,
            run_security_check=False,
            run_dependency_security_check=False,
            run_ci_workflow_security_check=False,
            run_secret_exposure_check=True,
            run_license_check=False,
            run_risk_check=False,
            run_impact_check=False,
        )

        self.assertFalse(readiness["ok"])
        secret_check = readiness["checks"]["secret_exposure"]
        self.assertFalse(secret_check["ok"])
        self.assertEqual(secret_check["status"], "blocked")
        self.assertEqual(secret_check["new_high_confidence_count"], 1)
        self.assertNotIn(raw_value, json.dumps(readiness, sort_keys=True))
