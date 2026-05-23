# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

from source.tool_output_schemas import TOOL_OUTPUT_SCHEMAS, validate_against_schema
from tests.server_test_support import ServerToolsTestBase


class AgentSecurityDeltaReportTests(ServerToolsTestBase):
    def test_blocks_new_high_severity_agent_patch_finding(self):
        self.write_repo_text(
            "src/agent_feature.py",
            "import subprocess\n\n"
            "def run_user_command(command):\n"
            "    return subprocess.run(command, shell=True, check=False)\n",
        )

        report = self.server.agent_security_delta_report(
            base_ref="HEAD",
            head_ref="WORKTREE",
            export=False,
        )

        self.assertEqual(report["schema"], "agent_security_delta_report.v1")
        self.assertEqual(report["status"], "block")
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["summary"]["new_finding_count"], 1)
        self.assertTrue(report["gate"]["would_block"])
        self.assertTrue(
            any(finding["rule_id"] == "python-subprocess-shell-true" for finding in report["findings"])
        )
        self.assertNotIn(str(self.repo_path), self.server.json.dumps(report))
        validate_against_schema(report, TOOL_OUTPUT_SCHEMAS["agent_security_delta_report"])

    def test_detects_repository_relevant_security_fixture_pack(self):
        fixtures = [
            (
                "src/path_traversal.py",
                "from flask import request\n\n"
                "def read_user_file():\n"
                "    return open(request.args['path']).read()\n",
                "python-user-controlled-path-use",
                "CWE-22",
                "path_traversal",
            ),
            (
                "src/sql_injection.py",
                "def find_user(cursor, name):\n"
                "    return cursor.execute(f\"SELECT * FROM users WHERE name = '{name}'\")\n",
                "python-sql-string-construction",
                "CWE-89",
                "injection",
            ),
            (
                "src/missing_auth.py",
                "@app.get('/files')\n"
                "def list_files():\n"
                "    return {'files': ['a']}\n",
                "python-http-surface-missing-auth-check",
                "CWE-306",
                "missing_authentication",
            ),
            (
                "src/weak_tempfile.py",
                "import tempfile\n\n"
                "def temp_path():\n"
                "    return tempfile.mktemp()\n",
                "python-weak-tempfile-mktemp",
                "CWE-377",
                "weak_temporary_file",
            ),
            (
                "docker-compose.yml",
                "services:\n  app:\n    image: example/app\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
                "config-docker-socket-mount",
                "CWE-250",
                "overbroad_file_access",
            ),
        ]

        for rel_path, content, expected_rule, expected_cwe, expected_category in fixtures:
            with self.subTest(rule=expected_rule):
                self.write_repo_text(rel_path, content)
                report = self.server.agent_security_delta_report(
                    base_ref="HEAD",
                    head_ref="WORKTREE",
                    export=False,
                )
                matching = [
                    finding
                    for finding in report["findings"]
                    if finding["rule_id"] == expected_rule
                ]
                self.assertTrue(matching, expected_rule)
                finding = matching[0]
                self.assertEqual(finding["introduction"], "new")
                self.assertEqual(finding["cwe"], expected_cwe)
                self.assertEqual(finding["category"], expected_category)
                self.assertFalse(finding["evidence"]["raw_returned"])
                self.assertNotIn(str(self.repo_path), self.server.json.dumps(finding))

    def test_warns_on_new_medium_severity_weak_tempfile(self):
        self.write_repo_text(
            "src/weak_tempfile.py",
            "import tempfile\n\n"
            "def temp_path():\n"
            "    return tempfile.mktemp()\n",
        )

        report = self.server.agent_security_delta_report(
            base_ref="HEAD",
            head_ref="WORKTREE",
            export=False,
        )

        self.assertEqual(report["status"], "warn")
        self.assertTrue(report["ok"])
        self.assertFalse(report["gate"]["would_block"])
        self.assertEqual(report["summary"]["by_severity"], {"medium": 1})

    def test_classifies_pre_existing_and_removed_findings(self):
        self.write_repo_text(
            "src/legacy.py",
            "import subprocess\n\n"
            "def legacy(command):\n"
            "    return subprocess.run(command, shell=True, check=False)\n",
        )
        self.commit_all("add legacy unsafe fixture")

        self.write_repo_text(
            "src/legacy.py",
            "import subprocess\n\n"
            "def legacy(command):\n"
            "    # unrelated feature change keeps the legacy shell call in place\n"
            "    return subprocess.run(command, shell=True, check=False)\n",
        )
        pre_existing = self.server.agent_security_delta_report(
            base_ref="HEAD",
            head_ref="WORKTREE",
            export=False,
        )
        self.assertEqual(pre_existing["summary"]["new_finding_count"], 0)
        self.assertEqual(pre_existing["summary"]["pre_existing_finding_count"], 1)
        self.assertEqual(pre_existing["findings"][0]["introduction"], "pre_existing")

        self.write_repo_text(
            "src/legacy.py",
            "def legacy(command):\n"
            "    return ['safe', command]\n",
        )
        removed = self.server.agent_security_delta_report(
            base_ref="HEAD",
            head_ref="WORKTREE",
            export=False,
        )
        self.assertEqual(removed["summary"]["removed_finding_count"], 1)
        self.assertEqual(removed["removed_findings"][0]["introduction"], "removed")

    def test_agent_security_delta_alias_matches_report_schema(self):
        self.write_repo_text(
            "src/agent_feature.py",
            "import os\n\n"
            "def run_user_command(command):\n"
            "    return os.system(command)\n",
        )

        report = self.server.agent_security_delta(
            base_ref="HEAD",
            head_ref="WORKTREE",
            export=False,
        )

        self.assertEqual(report["schema"], "agent_security_delta_report.v1")
        self.assertEqual(report["status"], "block")
        self.assertTrue(any(finding["rule_id"] == "python-unsafe-shell-command" for finding in report["findings"]))

    def test_head_ref_reads_committed_blob_not_worktree(self):
        self.write_repo_text(
            "src/agent_feature.py",
            "def run_user_command(command):\n"
            "    return ['safe', command]\n",
        )
        self.commit_all("add safe fixture")
        self.write_repo_text(
            "src/agent_feature.py",
            "import os\n\n"
            "def run_user_command(command):\n"
            "    return os.system(command)\n",
        )

        report = self.server.agent_security_delta_report(
            base_ref="HEAD",
            head_ref="HEAD",
            export=False,
        )

        self.assertEqual(report["changed_files"], [])
        self.assertEqual(report["summary"]["finding_count"], 0)
        self.assertEqual(report["status"], "pass")

    def test_export_writes_redacted_json_markdown_sarif_and_provenance(self):
        self.write_repo_text(
            "src/agent_feature.py",
            "import os\n\n"
            "def run_user_command(command):\n"
            "    return os.system(command)\n",
        )

        report = self.server.agent_security_delta_report(
            base_ref="HEAD",
            head_ref="WORKTREE",
            export=True,
        )

        self.assertIn("json", report["exports"])
        self.assertIn("markdown", report["exports"])
        self.assertIn("sarif", report["exports"])
        self.assertIn(report["exports"]["sarif"], report["provenance"]["sidecars"])
        for rel_path in [*report["exports"].values(), *report["provenance"]["sidecars"].values()]:
            self.assertTrue((self.repo_path / rel_path).is_file(), rel_path)
        sarif_text = (self.repo_path / report["exports"]["sarif"]).read_text(encoding="utf-8")
        self.assertIn("agent_security_delta_report", sarif_text)
        self.assertNotIn(str(self.repo_path), sarif_text)
        validate_against_schema(report, TOOL_OUTPUT_SCHEMAS["agent_security_delta_report"])

    def test_release_readiness_includes_agent_security_delta_gate(self):
        self.write_repo_text(
            "src/agent_feature.py",
            "import subprocess\n\n"
            "def run_user_command(command):\n"
            "    return subprocess.run(command, shell=True, check=False)\n",
        )

        readiness = self.server.release_readiness(
            base_ref="HEAD",
            head_ref="WORKTREE",
            run_tests=False,
            run_docs_check=False,
            run_security_check=False,
            run_dependency_security_check=False,
            run_ci_workflow_security_check=False,
            run_secret_exposure_check=False,
            run_license_check=False,
            run_risk_check=False,
            run_impact_check=False,
            summary_mode="full",
        )

        self.assertIn("agent_security_delta", readiness["checks"])
        self.assertFalse(readiness["checks"]["agent_security_delta"]["ok"])
        self.assertFalse(readiness["ok"])
