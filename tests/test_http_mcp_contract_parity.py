# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import tempfile
import unittest
from pathlib import Path

from source.http_mcp_contract_parity import (
    REPORT_SCHEMA,
    generate_http_mcp_contract_parity_report,
    schema_digest,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "http_mcp_contract_parity" / "contract-parity.json"


class HttpMcpContractParityReportTests(unittest.TestCase):
    def _catalog_fixture(self):
        return {
            "tools": [
                {
                    "name": "repo_info",
                    "metadata": {
                        "list_tools": {
                            "input_schema": {"type": "object", "properties": {}},
                            "output_schema": {
                                "type": "object",
                                "properties": {"schema": {"type": "string"}},
                                "required": ["schema"],
                            },
                        }
                    },
                },
                {
                    "name": "git_status",
                    "metadata": {
                        "list_tools": {
                            "input_schema": {
                                "type": "object",
                                "properties": {"short": {"type": "boolean"}},
                            },
                            "output_schema": {
                                "type": "object",
                                "properties": {"status": {"type": "string"}},
                            },
                        }
                    },
                },
            ]
        }

    def test_fixture_reports_one_pass_and_one_drift_without_raw_schema_evidence(self):
        report = generate_http_mcp_contract_parity_report(
            FIXTURE,
            repo_root=REPO_ROOT,
            tool_catalog=self._catalog_fixture(),
            include_passes=True,
        )

        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["summary"]["endpoints"], 2)
        self.assertEqual(report["summary"]["matched"], 1)
        self.assertEqual(report["summary"]["drifted"], 1)
        self.assertEqual(report["summary"]["findings"], 1)
        statuses = {row["endpoint_id"]: row["status"] for row in report["comparisons"]}
        self.assertEqual(statuses["fixture-pass-repo-info"], "pass")
        self.assertEqual(statuses["fixture-drift-git-status"], "drift")

        finding = report["findings"][0]
        self.assertEqual(finding["kind"], "response_schema_drift")
        self.assertEqual(finding["mcp_tool"], "git_status")
        self.assertEqual(
            finding["evidence"]["contract_path"],
            "tests/fixtures/http_mcp_contract_parity/contract-parity.json",
        )
        serialized_finding = json.dumps(finding, sort_keys=True)
        self.assertNotIn('"type": "integer"', serialized_finding)
        self.assertNotIn('"type": "string"', serialized_finding)
        self.assertTrue(report["security"]["repo_relative_paths_only"])
        self.assertFalse(report["security"]["raw_schemas_embedded"])

    def test_digest_only_expectations_can_pass(self):
        request_schema = {"type": "object", "properties": {}}
        response_schema = {
            "type": "object",
            "properties": {"schema": {"type": "string"}},
            "required": ["schema"],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            contract = root / "contracts" / "parity.json"
            contract.parent.mkdir(parents=True)
            contract.write_text(
                json.dumps(
                    {
                        "schema": "http_mcp_contract_expectations.v1",
                        "endpoints": [
                            {
                                "id": "digest-pass",
                                "path": "/mcp#tools/call:repo_info",
                                "mcp_tool": "repo_info",
                                "request_schema_digest": schema_digest(request_schema),
                                "response_schema_digest": schema_digest(response_schema),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = generate_http_mcp_contract_parity_report(
                contract,
                repo_root=root,
                tool_catalog=self._catalog_fixture(),
                include_passes=True,
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["matched"], 1)
        self.assertEqual(report["summary"]["findings"], 0)

    def test_missing_contract_docs_are_advisory_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report = generate_http_mcp_contract_parity_report(
                "contracts/missing.json",
                repo_root=Path(tmpdir),
                tool_catalog=self._catalog_fixture(),
            )

        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertEqual(report["status"], "missing-contract-docs")
        self.assertTrue(report["ok"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["network_used"])
        self.assertEqual(report["findings"], [])


if __name__ == "__main__":
    unittest.main()
