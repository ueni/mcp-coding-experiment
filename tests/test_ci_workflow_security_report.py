# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

from tests.server_test_support import ServerToolsTestBase


FULL_SHA = "0123456789abcdef0123456789abcdef01234567"


class CiWorkflowSecurityReportTests(ServerToolsTestBase):
    def rule_ids(self, report):
        return {finding["rule_id"] for finding in report["findings"]}

    def test_clean_workflow_with_sha_pins_and_permissions(self):
        self.write_repo_text(
            ".github/workflows/ci.yml",
            f"""name: CI
on: [push]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{FULL_SHA}
      - run: python -m pytest
""",
        )

        report = self.server._ci_workflow_security_report_impl(export=False)

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["summary"]["checked_workflow_count"], 1)
        self.assertEqual(report["summary"]["finding_count"], 0)

    def test_flags_current_repository_workflow_shape(self):
        self.write_repo_text(
            ".github/workflows/devcontainer-image.yml",
            """name: Build
on:
  pull_request:
  workflow_dispatch:
jobs:
  test:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
      - uses: actions/upload-artifact@v4
        with:
          path: coverage.xml
      - run: docker create --privileged image
  publish:
    runs-on: self-hosted
    steps:
      - uses: docker/setup-buildx-action@v3
      - run: echo "${{ secrets.DOCKER_PASSWORD }}" | docker login --password-stdin
""",
        )

        report = self.server._ci_workflow_security_report_impl(export=False)

        self.assertFalse(report["ok"])
        ids = self.rule_ids(report)
        self.assertIn("missing-top-level-permissions", ids)
        self.assertIn("mutable-third-party-action-ref", ids)
        self.assertIn("self-hosted-runner", ids)
        self.assertIn("privileged-container-usage", ids)
        self.assertIn("secret-reference", ids)
        self.assertIn("weak-publish-step-gate", ids)
        self.assertEqual(report["summary"]["checked_workflow_count"], 1)

    def test_malformed_yaml_is_reported_without_throwing(self):
        self.write_repo_text(
            ".github/workflows/bad.yml",
            "name: Bad\njobs:\n  test: [unterminated\n",
        )

        report = self.server._ci_workflow_security_report_impl(export=False)

        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "parse-error")
        self.assertIn("malformed-workflow-yaml", self.rule_ids(report))

    def test_no_workflow_repository_distinguishes_missing_evidence(self):
        report = self.server._ci_workflow_security_report_impl(export=False)

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "no-workflows")
        self.assertEqual(report["summary"]["checked_workflow_count"], 0)
        self.assertIn("no-github-actions-workflows", self.rule_ids(report))

    def test_suppression_requires_rationale_and_unexpired_expiry(self):
        self.write_repo_text(
            ".github/workflows/ci.yml",
            """name: CI
on: [push]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
""",
        )
        self.write_repo_text(
            ".github/ci-workflow-security.yml",
            """suppressions:
  - id: mutable-third-party-action-ref
    path: .github/workflows/ci.yml
    rationale: Accepted temporarily while upstream pin automation is trialed.
    expires: 2099-01-01
  - id: mutable-third-party-action-ref
    rationale: Expired exception should not apply.
    expires: 2000-01-01
""",
        )

        report = self.server._ci_workflow_security_report_impl(export=False)

        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"]["finding_count"], 0)
        self.assertEqual(report["summary"]["suppressed_finding_count"], 1)
        self.assertEqual(report["suppressions"]["expired_count"], 1)

    def test_secret_names_and_host_paths_are_redacted_in_evidence(self):
        self.write_repo_text(
            ".github/workflows/secrets.yml",
            """name: Secrets
on: [push]
permissions:
  contents: read
jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ secrets.MY_TOKEN }}" >/home/user/token.txt
""",
        )

        report = self.server._ci_workflow_security_report_impl(export=False)
        excerpts = "\n".join(
            finding["evidence"]["excerpt"] for finding in report["findings"]
        )

        self.assertIn("secrets.<redacted>", excerpts)
        self.assertNotIn("MY_TOKEN", excerpts)
        self.assertNotIn("/home/user", excerpts)
