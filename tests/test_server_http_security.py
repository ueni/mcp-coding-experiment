# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio
import json
import tempfile
from pathlib import Path

from tests.server_test_support import ServerToolsTestBase


class ServerHTTPSecurityTest(ServerToolsTestBase):
    def setUp(self):
        super().setUp()
        self._orig_auth_mode = self.server.MCP_HTTP_AUTH_MODE
        self._orig_token = self.server.MCP_HTTP_BEARER_TOKEN
        self._orig_rate_requests = self.server.MCP_HTTP_RATE_LIMIT_REQUESTS
        self._orig_rate_window = self.server.MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS
        self._orig_request_timeout = self.server.MCP_HTTP_REQUEST_TIMEOUT_SECONDS
        self._orig_audit_file = self.server.MCP_AUDIT_LOG_FILE
        self.server._HTTP_RATE_LIMIT_BUCKETS.clear()
        self.audit_tmp = tempfile.TemporaryDirectory()
        self.server.MCP_AUDIT_LOG_FILE = Path(self.audit_tmp.name) / "audit.jsonl"

    def tearDown(self):
        self.server.MCP_HTTP_AUTH_MODE = self._orig_auth_mode
        self.server.MCP_HTTP_BEARER_TOKEN = self._orig_token
        self.server.MCP_HTTP_RATE_LIMIT_REQUESTS = self._orig_rate_requests
        self.server.MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS = self._orig_rate_window
        self.server.MCP_HTTP_REQUEST_TIMEOUT_SECONDS = self._orig_request_timeout
        self.server.MCP_AUDIT_LOG_FILE = self._orig_audit_file
        self.server._HTTP_RATE_LIMIT_BUCKETS.clear()
        self.audit_tmp.cleanup()
        super().tearDown()

    def _scope(self, token: str = "", client: str = "127.0.0.1"):
        headers = []
        if token:
            headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
        return {"type": "http", "path": "/mcp", "method": "POST", "headers": headers, "client": (client, 12345)}

    def test_http_bearer_auth_scope_accepts_valid_token(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"

        self.assertEqual(self.server._http_authenticate_scope(self._scope()), (False, 401, "missing bearer token"))
        self.assertEqual(self.server._http_authenticate_scope(self._scope("wrong"))[1], 403)
        self.assertEqual(self.server._http_authenticate_scope(self._scope("secret-token")), (True, 200, "authorized"))

    def test_insecure_local_mode_is_explicit_and_loopback_only(self):
        self.server.MCP_HTTP_AUTH_MODE = "insecure-local"

        self.assertTrue(self.server._http_authenticate_scope(self._scope(client="127.0.0.1"))[0])
        allowed, status, detail = self.server._http_authenticate_scope(self._scope(client="10.0.0.2"))
        self.assertFalse(allowed)
        self.assertEqual(status, 403)
        self.assertIn("loopback", detail)

    def test_rate_limit_returns_retry_after(self):
        self.server.MCP_HTTP_RATE_LIMIT_REQUESTS = 2
        self.server.MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS = 60
        scope = self._scope(client="127.0.0.8")

        self.assertEqual(self.server._http_rate_limit_allow(scope, now=100.0), (True, 0))
        self.assertEqual(self.server._http_rate_limit_allow(scope, now=101.0), (True, 0))
        allowed, retry_after = self.server._http_rate_limit_allow(scope, now=102.0)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)

    def test_http_middleware_timeout_returns_504_and_audits(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"
        self.server.MCP_HTTP_REQUEST_TIMEOUT_SECONDS = 0.01
        messages = []

        async def slow_app(scope, receive, send):
            await asyncio.sleep(1)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        scope = self._scope("secret-token")

        asyncio.run(self.server.MCPHTTPAuthMiddleware(slow_app)(scope, receive, send))

        self.assertEqual(messages[0]["type"], "http.response.start")
        self.assertEqual(messages[0]["status"], 504)
        self.assertEqual(messages[1]["type"], "http.response.body")
        self.assertIn(b"timeout", messages[1]["body"])

        rows = self.server.MCP_AUDIT_LOG_FILE.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 1)
        event = json.loads(rows[0])
        self.assertEqual(event["tool_name"], "http_request")
        self.assertFalse(event["success"])
        self.assertEqual(event["reason"], "request timeout")
        self.assertEqual(event["arguments"], {"path": "/mcp"})

    def test_read_only_tool_path_is_allowed_without_http_auth_context(self):
        self.server.ALLOW_MUTATIONS = False

        categories = self.server._require_tool_security_gate("task_router", {"mode": "status"})

        self.assertEqual(categories, ["read-only"])
        self.assertFalse(self.server.MCP_AUDIT_LOG_FILE.exists())

    def test_unauthorized_http_sensitive_tool_is_denied_and_audited(self):
        self.server.ALLOW_MUTATIONS = True
        token = self.server._HTTP_REQUEST_AUTHORIZED.set(False)
        try:
            with self.assertRaises(PermissionError):
                self.server.task_router(mode="coding_check", check_profile="quick", check_target=".")
        finally:
            self.server._HTTP_REQUEST_AUTHORIZED.reset(token)

        rows = self.server.MCP_AUDIT_LOG_FILE.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 1)
        event = json.loads(rows[0])
        self.assertEqual(event["tool_name"], "task_router")
        self.assertFalse(event["success"])
        self.assertIn("shell/process", event["categories"])
        self.assertIn("HTTP session", event["reason"])

    def test_mutating_tool_requires_allow_mutations_even_when_authorized(self):
        self.server.ALLOW_MUTATIONS = False
        token = self.server._HTTP_REQUEST_AUTHORIZED.set(True)
        try:
            with self.assertRaises(PermissionError):
                self.server.task_router(mode="coding_pip", packages=["example-secret-token"])
        finally:
            self.server._HTTP_REQUEST_AUTHORIZED.reset(token)

        event = json.loads(self.server.MCP_AUDIT_LOG_FILE.read_text(encoding="utf-8").splitlines()[0])
        self.assertFalse(event["success"])
        self.assertIn("write", event["categories"])
        audit_text = self.server.MCP_AUDIT_LOG_FILE.read_text(encoding="utf-8")
        self.assertNotIn("example-secret-token", audit_text)
        self.assertEqual(event["arguments"]["packages"], ["<redacted>"])

    def test_redacts_sensitive_audit_arguments(self):
        self.server._append_audit_event(
            "unit_tool",
            ["secret-sensitive"],
            False,
            {
                "api_token": "abc",
                "nested": {"password": "pw"},
                "safe": "value",
                "packages": ["example-secret-token"],
                "prompt": "download from https://example.invalid/pkg?token=secret-value",
                "headers": ["Authorization: Bearer abc123"],
            },
            "unit",
        )

        audit_text = self.server.MCP_AUDIT_LOG_FILE.read_text(encoding="utf-8")
        self.assertNotIn("example-secret-token", audit_text)
        self.assertNotIn("secret-value", audit_text)
        self.assertNotIn("Bearer abc123", audit_text)
        event = json.loads(audit_text.splitlines()[0])
        self.assertEqual(event["arguments"]["api_token"], "<redacted>")
        self.assertEqual(event["arguments"]["nested"]["password"], "<redacted>")
        self.assertEqual(event["arguments"]["safe"], "value")
        self.assertEqual(event["arguments"]["packages"], ["<redacted>"])
        self.assertEqual(event["arguments"]["prompt"], "<redacted>")
        self.assertEqual(event["arguments"]["headers"], ["<redacted>"])
