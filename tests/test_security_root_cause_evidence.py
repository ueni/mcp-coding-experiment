# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

from source.tool_output_schemas import TOOL_OUTPUT_SCHEMAS, validate_against_schema
from tests.server_test_support import ServerToolsTestBase


class SecurityRootCauseEvidenceTests(ServerToolsTestBase):
    def test_ranks_removed_security_delta_and_validator_fix_path(self):
        self.write_repo_text(
            "src/command_runner.py",
            "import os\n\n"
            "def run_user_command(command):\n"
            "    return os.system(command)\n",
        )
        self.commit_all("add unsafe command runner fixture")

        self.write_repo_text(
            "src/command_runner.py",
            "ALLOWED_COMMANDS = {'status'}\n\n"
            "def run_user_command(command):\n"
            "    if command not in ALLOWED_COMMANDS:\n"
            "        raise ValueError('command not allowed')\n"
            "    return ['status']\n",
        )
        self.write_repo_text(
            "tests/test_command_runner.py",
            "from src.command_runner import run_user_command\n\n"
            "def test_rejects_unallowed_command():\n"
            "    try:\n"
            "        run_user_command('rm -rf /')\n"
            "    except ValueError:\n"
            "        return\n"
            "    raise AssertionError('expected rejection')\n",
        )

        report = self.server.security_root_cause_evidence(
            base_ref="HEAD",
            head_ref="WORKTREE",
            vulnerability_hint="command injection shell",
        )

        self.assertEqual(report["schema"], "security_root_cause_evidence.v1")
        self.assertEqual(report["status"], "evidence_found")
        self.assertTrue(report["read_only"])
        self.assertTrue(report["advisory_only"])
        self.assertFalse(report["security"]["network_access"])
        self.assertFalse(report["security"]["auto_fix"])
        self.assertFalse(report["security"]["exploit_proof_claimed"])
        self.assertIn("src/command_runner.py", report["changed_files"])
        self.assertTrue(report["ranked_locations"])
        top = report["ranked_locations"][0]
        reason_types = {reason["type"] for reason in top["reasons"]}
        self.assertEqual(top["path"], "src/command_runner.py")
        self.assertIn("removed_security_delta_finding", reason_types)
        self.assertIn("validator_or_boundary_check", reason_types)
        self.assertTrue(any(test["path"] == "tests/test_command_runner.py" for test in report["related_tests"]))
        self.assertNotIn(str(self.repo_path), self.server.json.dumps(report))
        validate_against_schema(report, TOOL_OUTPUT_SCHEMAS["security_root_cause_evidence"])

    def test_flags_wrapper_symptom_only_fix_with_reachable_sink(self):
        self.write_repo_text(
            "src/shell_backend.py",
            "import os\n\n"
            "def run(command):\n"
            "    return os.system(command)\n",
        )
        self.write_repo_text(
            "src/command_service.py",
            "from src import shell_backend\n\n"
            "def run_user_command(command):\n"
            "    return shell_backend.run(command)\n",
        )
        self.commit_all("add unsafe wrapper fixture")

        self.write_repo_text(
            "src/command_service.py",
            "from src import shell_backend\n\n"
            "def run_user_command(command):\n"
            "    if command == 'rm -rf /':\n"
            "        raise ValueError('blocked command')\n"
            "    return shell_backend.run(command)\n",
        )

        report = self.server.security_root_cause_evidence(
            base_ref="HEAD",
            head_ref="WORKTREE",
            vulnerability_hint="command injection wrapper",
        )

        warning_types = {warning["type"] for warning in report["shallow_fix_warnings"]}
        self.assertIn("wrapper_symptom_only", warning_types)
        self.assertEqual(report["summary"]["shallow_fix_status"], "warn")
        self.assertTrue(any("src/command_service.py" in warning["paths"] for warning in report["shallow_fix_warnings"]))
        validate_against_schema(report, TOOL_OUTPUT_SCHEMAS["security_root_cause_evidence"])

    def test_flags_warning_suppression_only_change(self):
        self.write_repo_text(
            "src/command_runner.py",
            "import os\n\n"
            "def run_user_command(command):\n"
            "    return os.system(command)\n",
        )
        self.commit_all("add unsafe command fixture")

        self.write_repo_text(
            "src/command_runner.py",
            "import os\n\n"
            "def run_user_command(command):\n"
            "    return os.system(command)  # nosec: legacy command runner\n",
        )

        report = self.server.security_root_cause_evidence(
            base_ref="HEAD",
            head_ref="WORKTREE",
            vulnerability_hint="command injection",
        )

        warning_types = {warning["type"] for warning in report["shallow_fix_warnings"]}
        self.assertIn("warning_suppression_only", warning_types)
        self.assertEqual(report["summary"]["shallow_fix_status"], "warn")
        self.assertGreaterEqual(report["summary"]["shallow_fix_warning_count"], 1)
        validate_against_schema(report, TOOL_OUTPUT_SCHEMAS["security_root_cause_evidence"])

    def test_release_readiness_surfaces_compact_root_cause_summary(self):
        self.write_repo_text(
            "src/command_runner.py",
            "import os\n\n"
            "def run_user_command(command):\n"
            "    return os.system(command)\n",
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

        root_cause_check = readiness["checks"]["security_root_cause_evidence"]
        self.assertIn(root_cause_check["status"], {"pass", "warn", "needs-review"})
        self.assertIn("ranked_location_count", root_cause_check)
        self.assertIn("shallow_fix_warning_count", root_cause_check)

    def test_can_disable_generated_security_delta_input(self):
        self.write_repo_text(
            "src/path_guard.py",
            "def validate_path(path):\n"
            "    return path.strip()\n",
        )

        report = self.server.security_root_cause_evidence(
            base_ref="HEAD",
            head_ref="WORKTREE",
            include_security_delta=False,
        )

        self.assertEqual(report["evidence_inputs"]["security_delta"]["source"], "disabled")
        self.assertEqual(report["summary"]["security_delta_findings_used"], 0)
        self.assertTrue(report["security"]["read_only"])
        validate_against_schema(report, TOOL_OUTPUT_SCHEMAS["security_root_cause_evidence"])

    def test_accepts_provided_security_delta_report_without_exporting_or_network(self):
        self.write_repo_text(
            "src/upload.py",
            "def read_upload(path):\n"
            "    return open(path).read()\n",
        )
        provided = {
            "schema": "agent_security_delta_report.v1",
            "report_id": "provided-agent-security-delta",
            "findings": [
                {
                    "id": "finding-1",
                    "path": "src/upload.py",
                    "line_start": 2,
                    "rule_id": "python-user-controlled-path-use",
                    "evidence": {"redacted_excerpt": "return open(path).read()"},
                }
            ],
            "removed_findings": [],
        }

        report = self.server.security_root_cause_evidence(
            base_ref="HEAD",
            head_ref="WORKTREE",
            security_delta_report=provided,
        )

        self.assertEqual(report["evidence_inputs"]["security_delta"]["source"], "provided")
        self.assertTrue(
            any("finding-1" in location["security_delta_finding_ids"] for location in report["ranked_locations"])
        )
        self.assertEqual(report["security"]["network_access"], False)
        validate_against_schema(report, TOOL_OUTPUT_SCHEMAS["security_root_cause_evidence"])
