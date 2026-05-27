# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio

from mcp import types as mcp_types
from mcp.server.fastmcp.exceptions import ToolError

from tests.server_test_support import ServerToolsTestBase


class SEP1303ToolValidationTest(ServerToolsTestBase):
    def _call_tool(self, name: str, arguments: dict):
        return asyncio.run(self.server.mcp.call_tool(name, arguments))

    def assert_validation_tool_error(self, name: str, arguments: dict, expected: str = ""):
        result = self._call_tool(name, arguments)
        self.assertTrue(result.isError)
        self.assertEqual(result.content[0].type, "text")
        text = result.content[0].text
        self.assertIn(f"Tool argument validation failed for `{name}`", text)
        self.assertIn("Remediation: correct the arguments", text)
        if expected:
            self.assertIn(expected, text)
        return text

    def test_invalid_well_formed_arguments_are_model_visible_for_public_tools(self):
        cases = [
            ("grep", {"pattern": "alpha", "summary_mode": "verbose"}, "summary_mode"),
            ("find_paths", {"max_entries": 0}, "max_entries"),
            (
                "read_snippet",
                {"path": "/home/user/private.txt", "start_line": 1, "end_line": 1},
                "repository root",
            ),
            ("task_router", {"mode": "unknown"}, "mode must be one of"),
            ("workflow_task", {"action": "unknown"}, "action must be one of"),
            ("release_readiness", {"summary_mode": "verbose"}, "summary_mode"),
        ]
        for name, arguments, expected in cases:
            with self.subTest(tool=name):
                self.assert_validation_tool_error(name, arguments, expected)

    def test_validation_errors_are_redacted_and_bounded(self):
        secret = "Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz123456"
        host_path = "/home/user/private/project/secret.txt"

        text = self.assert_validation_tool_error(
            "tool_annotations",
            {"tool_name": f"{secret} at {host_path}"},
            "unknown public MCP tool",
        )

        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", text)
        self.assertNotIn("Authorization: Bearer", text)
        self.assertNotIn(host_path, text)
        self.assertNotIn(str(self.repo_path), text)
        self.assertLessEqual(len(text), 560)

    def test_non_object_arguments_and_unknown_tools_remain_protocol_errors(self):
        with self.assertRaises(ToolError):
            asyncio.run(self.server.mcp.call_tool("grep", []))
        with self.assertRaises(ToolError):
            self._call_tool("not_a_public_tool", {})

    def test_low_level_call_handler_returns_validation_error_but_raises_unknown_tool(self):
        handler = self.server.mcp._mcp_server.request_handlers[mcp_types.CallToolRequest]
        request = mcp_types.CallToolRequest(
            params=mcp_types.CallToolRequestParams(
                name="grep",
                arguments={"pattern": "alpha", "summary_mode": "verbose"},
            )
        )

        response = asyncio.run(handler(request))

        self.assertTrue(response.root.isError)
        self.assertIn("Tool argument validation failed", response.root.content[0].text)

        unknown = mcp_types.CallToolRequest(
            params=mcp_types.CallToolRequestParams(name="not_a_public_tool", arguments={})
        )
        with self.assertRaises(ToolError):
            asyncio.run(handler(unknown))

    def test_permission_denials_do_not_become_validation_tool_results(self):
        token = self.server._HTTP_REQUEST_AUTHORIZED.set(False)
        try:
            with self.assertRaises(ToolError) as cm:
                self._call_tool("workspace_transaction", {"mode": "snapshot"})
        finally:
            self.server._HTTP_REQUEST_AUTHORIZED.reset(token)
        self.assertIsInstance(cm.exception.__cause__, PermissionError)
