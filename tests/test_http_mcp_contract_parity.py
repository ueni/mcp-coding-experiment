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
FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "http_mcp_contract_parity"
    / "contract-parity.json"
)


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
                                "properties": {
                                    "short": {"type": "boolean", "default": True}
                                },
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

    def test_fixture_covers_matching_contract_stale_docs_and_default_drift(self):
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
        self.assertEqual(report["summary"]["doc_surfaces"], 3)
        self.assertEqual(report["summary"]["doc_surfaces_checked"], 3)
        self.assertEqual(report["summary"]["stale_doc_surfaces"], 1)
        self.assertEqual(report["summary"]["default_drifts"], 1)
        self.assertEqual(report["summary"]["findings"], 3)

        endpoint_statuses = {
            row["endpoint_id"]: row["status"] for row in report["comparisons"]
        }
        self.assertEqual(endpoint_statuses["fixture-pass-repo-info"], "pass")
        self.assertEqual(endpoint_statuses["fixture-drift-git-status"], "drift")

        surface_statuses = {
            row["surface_id"]: row["status"] for row in report["doc_surfaces"]
        }
        self.assertEqual(surface_statuses["fixture-vscode-json-pass"], "pass")
        self.assertEqual(surface_statuses["fixture-stale-onboarding-doc"], "stale-doc")
        self.assertEqual(surface_statuses["fixture-default-drift-doc"], "default-drift")

        finding_kinds = {finding["kind"] for finding in report["findings"]}
        self.assertEqual(
            finding_kinds,
            {
                "response_schema_drift",
                "repo_doc_surface_stale",
                "repo_doc_argument_default_drift",
            },
        )
        serialized_findings = json.dumps(report["findings"], sort_keys=True)
        self.assertNotIn('"type": "integer"', serialized_findings)
        self.assertNotIn('"type": "string"', serialized_findings)
        self.assertNotIn("http://localhost:8000/sse", serialized_findings)
        self.assertTrue(report["security"]["repo_relative_paths_only"])
        self.assertFalse(report["security"]["raw_schemas_embedded"])

    def test_digest_only_expectations_can_pass_with_repo_doc_inventory(self):
        request_schema = {"type": "object", "properties": {}}
        response_schema = {
            "type": "object",
            "properties": {"schema": {"type": "string"}},
            "required": ["schema"],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "docs").mkdir()
            (root / ".vscode").mkdir()
            (root / "docs" / "onboarding.md").write_text(
                "Use http://localhost:8000/mcp with MCP_HTTP_BEARER_TOKEN.\n",
                encoding="utf-8",
            )
            (root / ".vscode" / "mcp.example.json").write_text(
                json.dumps(
                    {
                        "servers": {
                            "codebase-tooling-mcp": {
                                "type": "http",
                                "url": "http://localhost:8000/mcp",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
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
                                "response_schema_digest": schema_digest(
                                    response_schema
                                ),
                            }
                        ],
                        "repo_doc_surfaces": [
                            {
                                "id": "tmp-vscode-example",
                                "path": ".vscode/mcp.example.json",
                                "expected_values": [
                                    {
                                        "pointer": "/servers/codebase-tooling-mcp/type",
                                        "equals": "http",
                                    },
                                    {
                                        "pointer": "/servers/codebase-tooling-mcp/url",
                                        "equals": "http://localhost:8000/mcp",
                                    },
                                ],
                            },
                            {
                                "id": "tmp-onboarding-doc",
                                "path": "docs/onboarding.md",
                                "required_text": [
                                    "http://localhost:8000/mcp",
                                    "MCP_HTTP_BEARER_TOKEN",
                                ],
                            },
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
        self.assertEqual(report["summary"]["doc_surfaces"], 2)
        self.assertEqual(report["summary"]["doc_surfaces_checked"], 2)
        self.assertEqual(report["summary"]["findings"], 0)
        self.assertEqual({row["status"] for row in report["doc_surfaces"]}, {"pass"})

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
        self.assertEqual(report["doc_surfaces"], [])
        self.assertEqual(report["findings"], [])


if __name__ == "__main__":
    unittest.main()
