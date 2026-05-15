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
        self._orig_authorization_servers = self.server.MCP_HTTP_AUTHORIZATION_SERVERS_RAW
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
        self.server.MCP_HTTP_AUTHORIZATION_SERVERS_RAW = self._orig_authorization_servers
        self.server.MCP_HTTP_RATE_LIMIT_REQUESTS = self._orig_rate_requests
        self.server.MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS = self._orig_rate_window
        self.server.MCP_HTTP_REQUEST_TIMEOUT_SECONDS = self._orig_request_timeout
        self.server.MCP_AUDIT_LOG_FILE = self._orig_audit_file
        self.server._HTTP_RATE_LIMIT_BUCKETS.clear()
        self.audit_tmp.cleanup()
        super().tearDown()

    def _scope(self, token: str = "", client: str = "127.0.0.1", path: str = "/mcp", method: str = "POST"):
        headers = []
        if token:
            headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
        return {"type": "http", "path": path, "method": method, "headers": headers, "client": (client, 12345)}

    def _middleware_json_response(self, scope):
        messages = []

        async def app(_scope, _receive, send):
            response = self.server.JSONResponse({"downstream": True})
            await response(_scope, _receive, send)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        asyncio.run(self.server.MCPHTTPAuthMiddleware(app)(scope, receive, send))
        start = next(message for message in messages if message["type"] == "http.response.start")
        body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return start, json.loads(body.decode("utf-8"))

    def _audit_events(self):
        return [
            json.loads(line)
            for line in self.server.MCP_AUDIT_LOG_FILE.read_text(encoding="utf-8").splitlines()
        ]

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

    def test_well_known_mcp_manifest_is_public_and_allowlisted(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "super-secret-token"

        start, payload = self._middleware_json_response(
            self._scope(path="/.well-known/mcp-server.json", method="GET")
        )

        self.assertEqual(start["status"], 200)
        self.assertIn((b"content-type", b"application/json"), start["headers"])
        self.assertEqual(payload["schema"], "mcp-server-manifest.provisional.v1")
        self.assertEqual(payload["schema_version"], "provisional-2026-05")
        self.assertEqual(payload["server"]["name"], "codebase-tooling-mcp")
        self.assertIn("non-final SEP", payload["specification_status"])
        self.assertEqual(payload["health"], {"liveness": "/healthz", "readiness": "/healthz"})

        transports = {entry["endpoint"]: entry for entry in payload["transports"]}
        self.assertTrue(transports["/mcp"]["auth_required"])
        self.assertEqual(transports["/mcp"]["auth"]["schemes"], ["bearer"])
        self.assertIn("/.well-known/oauth-protected-resource", transports["/mcp"]["auth"]["oauth_protected_resource_metadata"])

        tool_names = {tool["name"] for tool in payload["capabilities"]["tools"]}
        self.assertIn("task_router", tool_names)
        self.assertIn("tool_annotations", tool_names)
        self.assertIn("tool_output_contracts", tool_names)
        output_contracts = payload["contracts"]["tool_output_contracts"]
        self.assertEqual(
            output_contracts["documentation"],
            {"title": "MCP Output Schemas", "path": "docs/mcp-output-schemas.md"},
        )
        self.assertIn("release_readiness", output_contracts["schema_backed_tools"])
        task_router = next(tool for tool in payload["capabilities"]["tools"] if tool["name"] == "task_router")
        self.assertIn("categories", task_router)
        self.assertIn("annotations", task_router)
        self.assertIn("modes", task_router)

        payload_text = json.dumps(payload, sort_keys=True)
        self.assertNotIn("super-secret-token", payload_text)
        self.assertNotIn(str(self.repo_path), payload_text)
        self.assertNotIn(str(Path.home()), payload_text)
        self.assertFalse(payload["privacy"]["contains_repository_contents"])
        self.assertFalse(payload["privacy"]["contains_bearer_tokens"])
        self.assertFalse(payload["privacy"]["contains_local_absolute_paths"])
        self.assertFalse(payload["privacy"]["contains_environment_values"])
        self.assertFalse(payload["privacy"]["contains_host_user_data"])
        self.assertFalse(payload["privacy"]["contains_secrets"])
        self.assertFalse(self.server.MCP_AUDIT_LOG_FILE.exists())

    def test_oauth_protected_resource_metadata_documents_local_bearer_mode(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "super-secret-token"
        self.server.MCP_HTTP_AUTHORIZATION_SERVERS_RAW = ""

        start, payload = self._middleware_json_response(
            self._scope(path="/.well-known/oauth-protected-resource", method="GET")
        )

        self.assertEqual(start["status"], 200)
        self.assertEqual(payload["resource"], "http://localhost:8000/mcp")
        self.assertEqual(payload["authorization_servers"], [])
        self.assertEqual(payload["bearer_methods_supported"], ["header"])
        self.assertEqual(payload["mcp_auth_mode"], "token")
        self.assertIn("local-bearer", payload["oauth_2_1_status"])
        payload_text = json.dumps(payload, sort_keys=True)
        self.assertNotIn("super-secret-token", payload_text)

    def test_oauth_resource_metadata_requires_and_returns_authorization_servers(self):
        self.server.MCP_HTTP_AUTH_MODE = "oauth-resource"
        self.server.MCP_HTTP_BEARER_TOKEN = "super-secret-token"
        self.server.MCP_HTTP_AUTHORIZATION_SERVERS_RAW = (
            '["https://auth.example.test", "https://backup.example.test"]'
        )

        start, payload = self._middleware_json_response(
            self._scope(path="/.well-known/oauth-protected-resource", method="GET")
        )

        self.assertEqual(start["status"], 200)
        self.assertEqual(
            payload["authorization_servers"],
            ["https://auth.example.test", "https://backup.example.test"],
        )
        self.assertEqual(payload["mcp_auth_mode"], "oauth-resource")
        self.assertNotIn("configuration_error", payload)
        self.assertIn("enabled", payload["oauth_2_1_status"])
        self.assertNotIn("super-secret-token", json.dumps(payload, sort_keys=True))

    def test_oauth_resource_mode_missing_authorization_servers_fails_closed(self):
        self.server.MCP_HTTP_AUTH_MODE = "oauth-resource"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"
        self.server.MCP_HTTP_AUTHORIZATION_SERVERS_RAW = ""

        start, payload = self._middleware_json_response(self._scope("secret-token"))

        self.assertEqual(start["status"], 403)
        self.assertEqual(payload["error"], "forbidden")
        self.assertIn("MCP_HTTP_AUTHORIZATION_SERVERS", payload["detail"])
        event = self._audit_events()[0]
        self.assertEqual(event["arguments"], {"path": "/mcp"})
        self.assertIn("MCP_HTTP_AUTHORIZATION_SERVERS", event["reason"])

    def test_oauth_resource_missing_authorization_servers_is_visible_in_health(self):
        self.server.MCP_HTTP_AUTH_MODE = "oauth-resource"
        self.server.MCP_HTTP_AUTHORIZATION_SERVERS_RAW = ""

        response = asyncio.run(self.server.healthz(None))
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(payload["auth"]["mode"], "oauth-resource")
        self.assertFalse(payload["auth"]["oauth_resource_configured"])
        self.assertIn("MCP_HTTP_AUTHORIZATION_SERVERS", payload["auth"]["configuration_error"])

    def test_unauthorized_http_response_includes_resource_metadata_challenge(self):
        self.server.MCP_HTTP_AUTH_MODE = "oauth-resource"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"
        self.server.MCP_HTTP_AUTHORIZATION_SERVERS_RAW = "https://auth.example.test"

        start, payload = self._middleware_json_response(self._scope(path="/mcp", method="POST"))

        self.assertEqual(start["status"], 401)
        headers = dict(start["headers"])
        self.assertIn(b"www-authenticate", headers)
        challenge = headers[b"www-authenticate"].decode("latin-1")
        self.assertIn('Bearer realm="mcp"', challenge)
        self.assertIn("resource_metadata=", challenge)
        self.assertIn("/.well-known/oauth-protected-resource", challenge)
        self.assertEqual(payload["error"], "unauthorized")

    def test_mcp_endpoint_auth_is_unchanged_when_manifest_is_public(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"

        start, payload = self._middleware_json_response(self._scope(path="/mcp", method="POST"))

        self.assertEqual(start["status"], 401)
        self.assertEqual(payload["error"], "unauthorized")
        event = self._audit_events()[0]
        self.assertEqual(event["tool_name"], "http_request")
        self.assertEqual(event["arguments"], {"path": "/mcp"})

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

    def test_direct_sensitive_tools_are_gated_for_unauthorized_http_sessions(self):
        self.server.ALLOW_MUTATIONS = True
        token = self.server._HTTP_REQUEST_AUTHORIZED.set(False)
        calls = [
            ("command_runner", lambda: self.server.command_runner(command=["cat", "README.md"])),
            ("docker_router", lambda: self.server.docker_router(mode="status")),
            ("vscode_router", lambda: self.server.vscode_router(mode="list")),
            (
                "apply_unified_diff",
                lambda: self.server.apply_unified_diff(diff_text="not a patch", check_only=True),
            ),
        ]
        try:
            for _, call in calls:
                with self.assertRaises(PermissionError):
                    call()
        finally:
            self.server._HTTP_REQUEST_AUTHORIZED.reset(token)

        events = self._audit_events()
        self.assertEqual([event["tool_name"] for event in events], [name for name, _ in calls])
        for event in events:
            self.assertFalse(event["success"])
            self.assertIn("HTTP session", event["reason"])

    def test_direct_sensitive_tools_audit_success_and_failure(self):
        self.server.ALLOW_MUTATIONS = True
        self.write_repo_text(
            ".vscode/tasks.json",
            '{"version":"2.0.0","tasks":[{"label":"noop","type":"shell","command":"echo ok"}]}',
        )
        valid_diff = """diff --git a/audit_added.txt b/audit_added.txt
new file mode 100644
index 0000000..257cc56
--- /dev/null
+++ b/audit_added.txt
@@ -0,0 +1 @@
+hello
"""

        success_calls = [
            "command_runner",
            "docker_router",
            "vscode_router",
            "apply_unified_diff",
        ]
        self.assertTrue(self.server.command_runner(command=["cat", "README.md"])["ok"])
        self.assertEqual(self.server.docker_router(mode="status")["schema"], "docker_router.v1")
        self.assertEqual(self.server.vscode_router(mode="list")["schema"], "vscode_router.v1")
        self.assertTrue(self.server.apply_unified_diff(diff_text=valid_diff, check_only=True)["ok"])

        self.assertFalse(self.server.command_runner(command=["cat", "missing-file"])["ok"])
        self.assertFalse(self.server.apply_unified_diff(diff_text="not a patch", check_only=True)["ok"])
        with self.assertRaises(ValueError):
            self.server.docker_router(mode="invalid")
        with self.assertRaises(ValueError):
            self.server.vscode_router(mode="invalid")

        events = self._audit_events()
        self.assertEqual([event["tool_name"] for event in events[:4]], success_calls)
        self.assertTrue(all(event["success"] for event in events[:4]))
        failure_events = events[4:]
        self.assertEqual(
            [event["tool_name"] for event in failure_events],
            ["command_runner", "apply_unified_diff", "docker_router", "vscode_router"],
        )
        self.assertTrue(all(not event["success"] for event in failure_events))
        self.assertIn("shell/process", events[0]["categories"])
        self.assertIn("git mutation", events[3]["categories"])

    def test_redacts_sensitive_audit_arguments_and_reason(self):
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
            "failed while reading example-secret-token",
        )

        audit_text = self.server.MCP_AUDIT_LOG_FILE.read_text(encoding="utf-8")
        self.assertNotIn("example-secret-token", audit_text)
        self.assertNotIn("secret-value", audit_text)
        self.assertNotIn("Bearer abc123", audit_text)
        event = json.loads(audit_text.splitlines()[0])
        self.assertEqual(event["reason"], "<redacted>")
        self.assertEqual(event["arguments"]["api_token"], "<redacted>")
        self.assertEqual(event["arguments"]["nested"]["password"], "<redacted>")
        self.assertEqual(event["arguments"]["safe"], "value")
        self.assertEqual(event["arguments"]["packages"], ["<redacted>"])
        self.assertEqual(event["arguments"]["prompt"], "<redacted>")
        self.assertEqual(event["arguments"]["headers"], ["<redacted>"])

    def test_command_runner_direct_failure_redacts_sensitive_audit_reason(self):
        self.server.ALLOW_MUTATIONS = True

        result = self.server.command_runner(command=["cat", "example-secret-token"])

        self.assertFalse(result["ok"])
        audit_text = self.server.MCP_AUDIT_LOG_FILE.read_text(encoding="utf-8")
        self.assertNotIn("example-secret-token", audit_text)
        event = json.loads(audit_text.splitlines()[0])
        self.assertEqual(event["tool_name"], "command_runner")
        self.assertFalse(event["success"])
        self.assertEqual(event["arguments"]["command"], ["cat", "<redacted>"])
        self.assertEqual(event["reason"], "<redacted>")
