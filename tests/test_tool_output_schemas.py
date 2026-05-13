# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

import asyncio

from source.tool_output_schemas import (
    ERROR_OUTPUT_SCHEMA,
    SCHEMA_BACKED_TOOL_NAMES,
    TOOL_OUTPUT_SCHEMAS,
    all_tool_output_contracts,
    make_tool_error,
    structured_tool_result,
    validate_against_schema,
)
from tests.server_test_support import ServerToolsTestBase


class ToolOutputSchemaContractTests(ServerToolsTestBase):
    def test_initial_schema_backed_tool_list_is_checked_in(self):
        self.assertEqual(
            SCHEMA_BACKED_TOOL_NAMES,
            (
                "repo_info",
                "runtime_state",
                "git_status",
                "grep",
                "find_paths",
                "read_snippet",
                "summarize_diff",
                "risk_scoring",
                "workspace_transaction",
                "policy_simulator",
                "clarification_gate",
                "release_readiness",
                "governance_report",
                "workflow_diagnostics",
            ),
        )
        contracts = all_tool_output_contracts()
        self.assertEqual(len(contracts["tools"]), len(SCHEMA_BACKED_TOOL_NAMES))
        self.assertEqual(set(TOOL_OUTPUT_SCHEMAS), set(SCHEMA_BACKED_TOOL_NAMES))

    def test_schema_documentation_tracks_checked_in_contracts(self):
        docs_root = self.server.Path(__file__).resolve().parents[1]
        schema_doc = (docs_root / "docs" / "mcp-output-schemas.md").read_text(encoding="utf-8")
        contracts = all_tool_output_contracts()
        by_tool = {entry["tool"]: entry for entry in contracts["tools"]}
        table_rows = {
            line.split("|", 3)[1].strip().strip("`"): line
            for line in schema_doc.splitlines()
            if line.startswith("| `")
        }

        for tool_name in SCHEMA_BACKED_TOOL_NAMES:
            with self.subTest(tool_name=tool_name):
                self.assertIn(f"- `{tool_name}`", schema_doc)
                row = table_rows[tool_name]
                for field in by_tool[tool_name]["stableFields"]:
                    if field.startswith("<"):
                        continue
                    self.assertIn(f"`{field}`", row)

    def test_representative_success_outputs_validate_against_schemas(self):
        self.write_repo_text("src/schema_contract.py", "def schema_marker():\n    return 'marker'\n")

        outputs = {
            "repo_info": self.server.repo_info(),
            "runtime_state": self.server.runtime_state(),
            "git_status": self.server.git_status(),
            "grep": self.server.grep(pattern="schema_marker", path="src"),
            "find_paths": self.server.find_paths(path="src", recursive=True),
            "read_snippet": self.server.read_snippet(
                path="src/schema_contract.py",
                start_line=1,
                end_line=2,
                output_profile="normal",
            ),
            "summarize_diff": self.server.summarize_diff(output_profile="normal"),
            "risk_scoring": self.server.risk_scoring(),
            "workspace_transaction": self.server.workspace_transaction(mode="begin", label="schema-contract"),
            "policy_simulator": self.server.policy_simulator(base_ref="HEAD", head_ref="HEAD"),
            "clarification_gate": self.server.clarification_gate(
                intent="prepare a safe release",
                target="HEAD",
                operation="release_readiness",
                risk_level="medium",
                rollback_plan="read-only check",
            ),
            "release_readiness": self.server.release_readiness(
                base_ref="HEAD",
                head_ref="HEAD",
                run_tests=False,
                run_docs_check=False,
                run_security_check=False,
                run_license_check=False,
                run_risk_check=False,
                run_impact_check=False,
            ),
            "governance_report": self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=False),
            "workflow_diagnostics": self.server.workflow_diagnostics(),
        }

        for tool_name, payload in outputs.items():
            with self.subTest(tool_name=tool_name):
                validate_against_schema(payload, TOOL_OUTPUT_SCHEMAS[tool_name])

    def test_fastmcp_advertises_checked_in_output_schemas(self):
        listed = asyncio.run(self.server.mcp.list_tools())
        by_name = {tool.name: tool for tool in listed}
        for tool_name in SCHEMA_BACKED_TOOL_NAMES:
            with self.subTest(tool_name=tool_name):
                self.assertIn(tool_name, by_name)
                self.assertEqual(by_name[tool_name].outputSchema, TOOL_OUTPUT_SCHEMAS[tool_name])

    def test_shared_error_envelope_validates_for_each_schema_backed_tool(self):
        for tool_name in SCHEMA_BACKED_TOOL_NAMES:
            with self.subTest(tool_name=tool_name):
                payload = make_tool_error(tool_name, ValueError("bad input"))
                validate_against_schema(payload, ERROR_OUTPUT_SCHEMA)

    def test_structured_result_preserves_text_json_for_legacy_clients(self):
        payload = self.server.grep(pattern="alpha", path="src", summary_mode="quick")
        envelope = structured_tool_result("grep", payload)
        validate_against_schema(envelope["structuredContent"], TOOL_OUTPUT_SCHEMAS["grep"])
        self.assertIn("structuredContent", envelope)
        self.assertEqual(envelope["content"][0]["type"], "text")
        self.assertIn("grep.quick.v1", envelope["content"][0]["text"])
