# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio
import json
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.server_test_support import ServerToolsTestBase


class ServerHTTPSecurityTest(ServerToolsTestBase):
    def setUp(self):
        super().setUp()
        self._orig_auth_mode = self.server.MCP_HTTP_AUTH_MODE
        self._orig_token = self.server.MCP_HTTP_BEARER_TOKEN
        self._orig_token_scopes = self.server.MCP_HTTP_BEARER_TOKEN_SCOPES_RAW
        self._orig_authorization_servers = self.server.MCP_HTTP_AUTHORIZATION_SERVERS_RAW
        self._orig_allowed_origins = self.server.MCP_HTTP_ALLOWED_ORIGINS_RAW
        self._orig_supported_protocol_versions = self.server.MCP_HTTP_SUPPORTED_PROTOCOL_VERSIONS_RAW
        self._orig_rate_requests = self.server.MCP_HTTP_RATE_LIMIT_REQUESTS
        self._orig_rate_window = self.server.MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS
        self._orig_request_timeout = self.server.MCP_HTTP_REQUEST_TIMEOUT_SECONDS
        self._orig_audit_file = self.server.MCP_AUDIT_LOG_FILE
        self._orig_replay_guard_enabled = self.server.MCP_MUTATION_REPLAY_GUARD_ENABLED
        self._orig_replay_guard_file = self.server.MCP_MUTATION_REPLAY_GUARD_JOURNAL_FILE
        self._orig_replay_guard_max_entries = self.server.MCP_MUTATION_REPLAY_GUARD_MAX_ENTRIES
        self._orig_replay_guard_ttl = self.server.MCP_MUTATION_REPLAY_GUARD_TTL_SECONDS
        self.server._HTTP_RATE_LIMIT_BUCKETS.clear()
        self.audit_tmp = tempfile.TemporaryDirectory()
        self.server.MCP_AUDIT_LOG_FILE = Path(self.audit_tmp.name) / "audit.jsonl"
        self.server.MCP_MUTATION_REPLAY_GUARD_JOURNAL_FILE = Path(".codebase-tooling-mcp/audit/test-mutation-replay-journal.json")

    def tearDown(self):
        self.server.MCP_HTTP_AUTH_MODE = self._orig_auth_mode
        self.server.MCP_HTTP_BEARER_TOKEN = self._orig_token
        self.server.MCP_HTTP_BEARER_TOKEN_SCOPES_RAW = self._orig_token_scopes
        self.server.MCP_HTTP_AUTHORIZATION_SERVERS_RAW = self._orig_authorization_servers
        self.server.MCP_HTTP_ALLOWED_ORIGINS_RAW = self._orig_allowed_origins
        self.server.MCP_HTTP_SUPPORTED_PROTOCOL_VERSIONS_RAW = self._orig_supported_protocol_versions
        self.server.MCP_HTTP_RATE_LIMIT_REQUESTS = self._orig_rate_requests
        self.server.MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS = self._orig_rate_window
        self.server.MCP_HTTP_REQUEST_TIMEOUT_SECONDS = self._orig_request_timeout
        self.server.MCP_AUDIT_LOG_FILE = self._orig_audit_file
        self.server.MCP_MUTATION_REPLAY_GUARD_ENABLED = self._orig_replay_guard_enabled
        self.server.MCP_MUTATION_REPLAY_GUARD_JOURNAL_FILE = self._orig_replay_guard_file
        self.server.MCP_MUTATION_REPLAY_GUARD_MAX_ENTRIES = self._orig_replay_guard_max_entries
        self.server.MCP_MUTATION_REPLAY_GUARD_TTL_SECONDS = self._orig_replay_guard_ttl
        self.server._HTTP_RATE_LIMIT_BUCKETS.clear()
        self.audit_tmp.cleanup()
        super().tearDown()

    def _scope(
        self,
        token: str = "",
        client: str = "127.0.0.1",
        path: str = "/mcp",
        method: str = "POST",
        origin: str | None = None,
        protocol_version: str | None = None,
        session_id: str = "",
    ):
        headers = []
        if token:
            headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
        if origin is not None:
            headers.append((b"origin", origin.encode("latin-1")))
        if protocol_version is not None:
            headers.append((b"mcp-protocol-version", protocol_version.encode("latin-1")))
        if session_id:
            headers.append((b"mcp-session-id", session_id.encode("latin-1")))
        return {"type": "http", "path": path, "method": method, "headers": headers, "client": (client, 12345)}

    def _middleware_json_response(self, scope, downstream_calls: list[dict] | None = None):
        messages = []

        async def app(_scope, _receive, send):
            if downstream_calls is not None:
                downstream_calls.append(_scope)
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

    @contextmanager
    def _authorized_http_tool_context(
        self,
        *,
        session_id: str = "session-1",
        idempotency_key: str = "",
        scopes: frozenset[str] | None = None,
    ):
        auth_token = self.server._HTTP_REQUEST_AUTHORIZED.set(True)
        scope_token = self.server._HTTP_REQUEST_GRANTED_SCOPES.set(
            scopes or frozenset({self.server.MCP_SCOPE_READ, self.server.MCP_SCOPE_MUTATE})
        )
        session_token = self.server._STREAMABLE_HTTP_SESSION_ID.set(session_id)
        idempotency_token = self.server._HTTP_IDEMPOTENCY_KEY.set(idempotency_key)
        try:
            yield
        finally:
            self.server._HTTP_IDEMPOTENCY_KEY.reset(idempotency_token)
            self.server._STREAMABLE_HTTP_SESSION_ID.reset(session_token)
            self.server._HTTP_REQUEST_GRANTED_SCOPES.reset(scope_token)
            self.server._HTTP_REQUEST_AUTHORIZED.reset(auth_token)

    def test_http_bearer_auth_scope_accepts_valid_token(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"

        self.assertEqual(self.server._http_authenticate_scope(self._scope()), (False, 401, "missing bearer token"))
        self.assertEqual(self.server._http_authenticate_scope(self._scope("wrong"))[1], 403)
        self.assertEqual(self.server._http_authenticate_scope(self._scope("secret-token")), (True, 200, "authorized"))

    def test_local_bearer_scope_config_defaults_to_current_full_access(self):
        self.server.MCP_HTTP_BEARER_TOKEN_SCOPES_RAW = ""

        self.assertEqual(
            self.server._local_bearer_token_granted_scopes(),
            frozenset({self.server.MCP_SCOPE_READ, self.server.MCP_SCOPE_MUTATE}),
        )

    def test_local_bearer_scope_config_can_grant_read_only(self):
        self.server.MCP_HTTP_BEARER_TOKEN_SCOPES_RAW = "mcp:read"

        self.assertEqual(
            self.server._local_bearer_token_granted_scopes(),
            frozenset({self.server.MCP_SCOPE_READ}),
        )
        self.assertEqual(self.server._http_bearer_token_scope_config_error(), "")

    def test_local_bearer_scope_config_rejects_unknown_scopes(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"
        self.server.MCP_HTTP_BEARER_TOKEN_SCOPES_RAW = "mcp:read admin"

        allowed, status, reason = self.server._http_authenticate_scope(self._scope("secret-token"))

        self.assertFalse(allowed)
        self.assertEqual(status, 403)
        self.assertIn("MCP_HTTP_BEARER_TOKEN_SCOPES", reason)
        self.assertIn("admin", reason)

    def test_protected_mcp_allows_missing_and_default_loopback_origins(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"

        cases = [
            ("/mcp", None),
            ("/mcp", "http://localhost:8000"),
            ("/mcp", "http://127.0.0.1:8000"),
            ("/mcp", "http://127.42.0.1:9000"),
            ("/mcp", "http://[::1]:8000"),
            ("/sse", "http://localhost:8000"),
        ]
        for path, origin in cases:
            with self.subTest(path=path, origin=origin):
                downstream_calls = []
                start, payload = self._middleware_json_response(
                    self._scope("secret-token", path=path, origin=origin), downstream_calls
                )
                self.assertEqual(start["status"], 200)
                self.assertEqual(payload, {"downstream": True})
                self.assertEqual(len(downstream_calls), 1)

        self.assertFalse(self.server.MCP_AUDIT_LOG_FILE.exists())

    def test_invalid_origin_is_rejected_and_audited_without_raw_origin(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"
        bad_origin = "https://evil.example.test"

        for path in ["/mcp", "/sse"]:
            with self.subTest(path=path):
                downstream_calls = []
                start, payload = self._middleware_json_response(
                    self._scope("secret-token", path=path, origin=bad_origin), downstream_calls
                )

                self.assertEqual(start["status"], 403)
                self.assertEqual(payload["error"], "forbidden")
                self.assertIn("Origin", payload["detail"])
                self.assertEqual(downstream_calls, [])

        audit_text = self.server.MCP_AUDIT_LOG_FILE.read_text(encoding="utf-8")
        self.assertNotIn(bad_origin, audit_text)
        events = [json.loads(line) for line in audit_text.splitlines()]
        self.assertEqual([event["arguments"]["path"] for event in events], ["/mcp", "/sse"])
        for event in events:
            self.assertEqual(event["tool_name"], "http_request")
            self.assertFalse(event["success"])
            self.assertEqual(event["arguments"]["origin"], "<redacted>")
            self.assertIn("Origin", event["reason"])

    def test_configured_origin_allowlist_supports_exact_and_port_wildcard(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"
        self.server.MCP_HTTP_ALLOWED_ORIGINS_RAW = "https://mcp.example.test,http://localhost:*"

        for origin in ["https://mcp.example.test", "http://localhost:5173"]:
            with self.subTest(origin=origin):
                downstream_calls = []
                start, payload = self._middleware_json_response(
                    self._scope("secret-token", origin=origin), downstream_calls
                )
                self.assertEqual(start["status"], 200)
                self.assertEqual(payload, {"downstream": True})
                self.assertEqual(len(downstream_calls), 1)

    def test_protocol_version_accepts_absent_and_supported_values(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"

        for protocol_version in [None, "2024-11-05", "2025-11-25"]:
            with self.subTest(protocol_version=protocol_version):
                downstream_calls = []
                start, payload = self._middleware_json_response(
                    self._scope(
                        "secret-token",
                        origin="http://localhost:8000",
                        protocol_version=protocol_version,
                    ),
                    downstream_calls,
                )
                self.assertEqual(start["status"], 200)
                self.assertEqual(payload, {"downstream": True})
                self.assertEqual(len(downstream_calls), 1)

    def test_protocol_version_rejects_malformed_and_unsupported_before_downstream(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"

        cases = [
            ("not-a-date", "malformed MCP-Protocol-Version header"),
            ("2099-01-01", "unsupported MCP-Protocol-Version header"),
        ]
        for protocol_version, expected_detail in cases:
            with self.subTest(protocol_version=protocol_version):
                downstream_calls = []
                start, payload = self._middleware_json_response(
                    self._scope("secret-token", protocol_version=protocol_version), downstream_calls
                )
                self.assertEqual(start["status"], 400)
                self.assertEqual(payload, {"error": "bad_request", "detail": expected_detail})
                self.assertEqual(downstream_calls, [])

        events = self._audit_events()
        self.assertEqual([event["reason"] for event in events], [detail for _, detail in cases])
        for event in events:
            self.assertEqual(event["arguments"], {"path": "/mcp", "mcp_protocol_version": "<redacted>"})

    def test_mcp_session_id_without_bearer_token_does_not_authorize(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"
        downstream_calls = []

        start, payload = self._middleware_json_response(
            self._scope(session_id="not-a-credential"), downstream_calls
        )

        self.assertEqual(start["status"], 401)
        self.assertEqual(payload["error"], "unauthorized")
        self.assertIn("bearer token", payload["detail"])
        self.assertEqual(downstream_calls, [])
        headers = dict(start["headers"])
        self.assertIn(b"www-authenticate", headers)
        challenge = headers[b"www-authenticate"].decode("latin-1")
        self.assertIn('scope="mcp:read"', challenge)
        event = self._audit_events()[0]
        self.assertEqual(event["arguments"], {"path": "/mcp"})
        self.assertEqual(event["reason"], "missing bearer token")

    def test_mcp_session_id_with_invalid_bearer_token_is_still_forbidden(self):
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"

        start, payload = self._middleware_json_response(
            self._scope("wrong", session_id="not-a-credential")
        )

        self.assertEqual(start["status"], 403)
        self.assertEqual(payload["error"], "forbidden")
        event = self._audit_events()[0]
        self.assertEqual(event["reason"], "invalid bearer token")

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
        self.assertEqual(transports["/mcp"]["auth"]["scopes_supported"], ["mcp:read", "mcp:mutate"])
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
        self.assertIn("required_scope", task_router)
        self.assertEqual(task_router["required_scope"], "mcp:read")
        self.assertIn("annotations", task_router)
        self.assertIn("modes", task_router)
        task_mode = next(mode for mode in task_router["modes"] if mode["mode"] == "task")
        self.assertEqual(task_mode["required_scope"], "mcp:mutate")

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
        self.assertEqual(payload["scopes_supported"], ["mcp:read", "mcp:mutate"])
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
        self.assertIn('scope="mcp:read"', challenge)
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

    def test_read_scoped_http_request_can_run_read_only_tools(self):
        auth_token = self.server._HTTP_REQUEST_AUTHORIZED.set(True)
        scope_token = self.server._HTTP_REQUEST_GRANTED_SCOPES.set(
            frozenset({self.server.MCP_SCOPE_READ})
        )
        try:
            categories = self.server._require_tool_security_gate("task_router", {"mode": "status"})
        finally:
            self.server._HTTP_REQUEST_GRANTED_SCOPES.reset(scope_token)
            self.server._HTTP_REQUEST_AUTHORIZED.reset(auth_token)

        self.assertEqual(categories, ["read-only"])
        self.assertFalse(self.server.MCP_AUDIT_LOG_FILE.exists())

    def test_read_scoped_http_mutating_tool_is_denied_before_mutation_flag(self):
        self.server.ALLOW_MUTATIONS = False
        auth_token = self.server._HTTP_REQUEST_AUTHORIZED.set(True)
        scope_token = self.server._HTTP_REQUEST_GRANTED_SCOPES.set(
            frozenset({self.server.MCP_SCOPE_READ})
        )
        try:
            with self.assertRaises(self.server.HTTPInsufficientScopeError) as raised:
                self.server._require_tool_security_gate("workspace_transaction", {"mode": "write"})
        finally:
            self.server._HTTP_REQUEST_GRANTED_SCOPES.reset(scope_token)
            self.server._HTTP_REQUEST_AUTHORIZED.reset(auth_token)

        self.assertEqual(raised.exception.required_scope, self.server.MCP_SCOPE_MUTATE)
        event = self._audit_events()[0]
        self.assertEqual(event["reason"], "insufficient_scope")
        self.assertEqual(event["required_scope"], "mcp:mutate")
        self.assertEqual(event["granted_scopes"], ["mcp:read"])

    def test_read_scoped_http_sensitive_tool_is_denied_with_scope_evidence(self):
        self.server.ALLOW_MUTATIONS = True
        token_value = "secret-token-never-logged"
        self.server.MCP_HTTP_BEARER_TOKEN = token_value
        auth_token = self.server._HTTP_REQUEST_AUTHORIZED.set(True)
        scope_token = self.server._HTTP_REQUEST_GRANTED_SCOPES.set(
            frozenset({self.server.MCP_SCOPE_READ})
        )
        try:
            with self.assertRaises(self.server.HTTPInsufficientScopeError) as raised:
                self.server.command_runner(command=["cat", "README.md"])
        finally:
            self.server._HTTP_REQUEST_GRANTED_SCOPES.reset(scope_token)
            self.server._HTTP_REQUEST_AUTHORIZED.reset(auth_token)

        self.assertEqual(raised.exception.required_scope, self.server.MCP_SCOPE_MUTATE)
        self.assertIn('scope="mcp:mutate"', raised.exception.challenge)
        event = self._audit_events()[0]
        self.assertEqual(event["tool_name"], "command_runner")
        self.assertFalse(event["success"])
        self.assertEqual(event["reason"], "insufficient_scope")
        self.assertEqual(event["required_scope"], "mcp:mutate")
        self.assertEqual(event["granted_scopes"], ["mcp:read"])
        audit_text = self.server.MCP_AUDIT_LOG_FILE.read_text(encoding="utf-8")
        self.assertNotIn(token_value, audit_text)

    def test_middleware_insufficient_scope_returns_403_challenge(self):
        self.server.ALLOW_MUTATIONS = True
        self.server.MCP_HTTP_AUTH_MODE = "token"
        self.server.MCP_HTTP_BEARER_TOKEN = "secret-token"
        self.server.MCP_HTTP_BEARER_TOKEN_SCOPES_RAW = "mcp:read"
        messages = []

        async def app(_scope, _receive, _send):
            self.server._require_tool_security_gate("command_runner", {"command": ["cat", "README.md"]})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        asyncio.run(self.server.MCPHTTPAuthMiddleware(app)(self._scope("secret-token"), receive, send))

        start = next(message for message in messages if message["type"] == "http.response.start")
        body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        payload = json.loads(body.decode("utf-8"))
        headers = dict(start["headers"])
        challenge = headers[b"www-authenticate"].decode("latin-1")
        self.assertEqual(start["status"], 403)
        self.assertEqual(payload["error"], "insufficient_scope")
        self.assertEqual(payload["required_scope"], "mcp:mutate")
        self.assertEqual(payload["granted_scopes"], ["mcp:read"])
        self.assertIn('error="insufficient_scope"', challenge)
        self.assertIn('scope="mcp:mutate"', challenge)

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

    def test_mutation_replay_guard_disabled_preserves_existing_http_behavior(self):
        self.server.ALLOW_MUTATIONS = True
        self.server.MCP_MUTATION_REPLAY_GUARD_ENABLED = False

        with self._authorized_http_tool_context(session_id="replay-disabled", idempotency_key="same-key"):
            first = self.server.workspace_transaction(
                mode="write",
                path="guard-disabled.txt",
                content="first",
                overwrite=False,
            )
            with self.assertRaises(FileExistsError):
                self.server.workspace_transaction(
                    mode="write",
                    path="guard-disabled.txt",
                    content="first",
                    overwrite=False,
                )

        self.assertEqual(first["mode"], "write")
        self.assertFalse((self.repo_path / self.server.MCP_MUTATION_REPLAY_GUARD_JOURNAL_FILE).exists())

    def test_mutation_replay_guard_suppresses_duplicate_workspace_write(self):
        self.server.ALLOW_MUTATIONS = True
        self.server.MCP_MUTATION_REPLAY_GUARD_ENABLED = True

        with self._authorized_http_tool_context(session_id="replay-session", idempotency_key="patch-1"):
            first = self.server.workspace_transaction(
                mode="write",
                path="guarded-write.txt",
                content="hello",
                overwrite=False,
            )
            duplicate = self.server.workspace_transaction(
                mode="write",
                path="guarded-write.txt",
                content="hello",
                overwrite=False,
            )

        self.assertEqual(first["mode"], "write")
        self.assertEqual(duplicate["schema"], "mutation_replay_guard.duplicate.v1")
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual((self.repo_path / "guarded-write.txt").read_text(encoding="utf-8"), "hello")
        journal = json.loads(
            (self.repo_path / self.server.MCP_MUTATION_REPLAY_GUARD_JOURNAL_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(journal["schema"], "mutation_replay_journal.v1")
        self.assertEqual(len(journal["entries"]), 1)
        entry = journal["entries"][0]
        self.assertEqual(entry["tool_name"], "workspace_transaction")
        self.assertEqual(entry["mode"], "write")
        self.assertEqual(entry["duplicate_count"], 1)
        journal_text = json.dumps(journal, sort_keys=True)
        self.assertNotIn("hello", journal_text)
        self.assertNotIn(str(self.repo_path), journal_text)
        decisions = [event["arguments"].get("replay_guard", {}).get("decision") for event in self._audit_events()]
        self.assertIn("recorded", decisions)
        self.assertIn("duplicate_suppressed", decisions)

    def test_mutation_replay_guard_suppresses_duplicate_apply_diff_patch(self):
        self.server.ALLOW_MUTATIONS = True
        self.server.MCP_MUTATION_REPLAY_GUARD_ENABLED = True
        diff_text = """diff --git a/docs/a.md b/docs/a.md
--- a/docs/a.md
+++ b/docs/a.md
@@ -1 +1 @@
-hello world
+hello replay
"""

        with self._authorized_http_tool_context(session_id="patch-session", idempotency_key="patch-1"):
            first = self.server.apply_unified_diff(diff_text=diff_text, check_only=False)
            duplicate = self.server.apply_unified_diff(diff_text=diff_text, check_only=False)

        self.assertTrue(first["ok"])
        self.assertEqual(duplicate["schema"], "mutation_replay_guard.duplicate.v1")
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual((self.repo_path / "docs" / "a.md").read_text(encoding="utf-8"), "hello replay\n")
        journal_text = (self.repo_path / self.server.MCP_MUTATION_REPLAY_GUARD_JOURNAL_FILE).read_text(encoding="utf-8")
        self.assertNotIn("hello world", journal_text)
        self.assertNotIn("hello replay", journal_text)

    def test_mutation_replay_guard_failed_original_duplicate_stays_failed(self):
        self.server.ALLOW_MUTATIONS = True
        self.server.MCP_MUTATION_REPLAY_GUARD_ENABLED = True

        with self._authorized_http_tool_context(
            session_id="failed-replay",
            idempotency_key="bad-patch",
        ):
            first = self.server.apply_unified_diff(
                diff_text="not a patch",
                check_only=False,
            )
            duplicate = self.server.apply_unified_diff(
                diff_text="not a patch",
                check_only=False,
            )

        self.assertFalse(first["ok"])
        self.assertEqual(duplicate["schema"], "mutation_replay_guard.duplicate.v1")
        self.assertFalse(duplicate["ok"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["replay_guard"]["original_status"], "failed")
        self.assertEqual(
            duplicate["error"],
            "original mutating request did not complete successfully",
        )
        events = [
            event
            for event in self._audit_events()
            if event["tool_name"] == "mutation_replay_guard"
        ]
        self.assertFalse(events[-1]["success"])
        self.assertEqual(
            events[-1]["arguments"]["replay_guard"]["decision"],
            "duplicate_suppressed",
        )

    def test_mutation_replay_guard_exception_original_duplicate_stays_failed(self):
        self.server.ALLOW_MUTATIONS = True
        self.server.MCP_MUTATION_REPLAY_GUARD_ENABLED = True
        arguments = {
            "mode": "write",
            "path": "raised-before-write.txt",
            "content": "hello",
        }

        def raise_before_mutation():
            raise RuntimeError("simulated mutation failure")

        with self._authorized_http_tool_context(
            session_id="exception-replay",
            idempotency_key="raising-write",
        ):
            with self.assertRaises(RuntimeError):
                self.server._run_with_tool_security_audit(
                    "workspace_transaction",
                    arguments,
                    raise_before_mutation,
                )
            duplicate = self.server._run_with_tool_security_audit(
                "workspace_transaction",
                arguments,
                lambda: self.fail("duplicate mutating action should not run"),
            )

        self.assertFalse((self.repo_path / "raised-before-write.txt").exists())
        self.assertEqual(duplicate["schema"], "mutation_replay_guard.duplicate.v1")
        self.assertFalse(duplicate["ok"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["replay_guard"]["original_status"], "failed")
        self.assertEqual(
            duplicate["error"],
            "original mutating request did not complete successfully",
        )
        events = [
            event
            for event in self._audit_events()
            if event["tool_name"] == "mutation_replay_guard"
        ]
        self.assertFalse(events[-1]["success"])
        self.assertEqual(
            events[-1]["arguments"]["replay_guard"]["decision"],
            "duplicate_suppressed",
        )

    def test_mutation_replay_guard_pending_original_duplicate_stays_failed(self):
        self.server.ALLOW_MUTATIONS = True
        self.server.MCP_MUTATION_REPLAY_GUARD_ENABLED = True

        with self._authorized_http_tool_context(
            session_id="pending-replay",
            idempotency_key="pending-write",
        ):
            categories = self.server._require_tool_security_gate(
                "workspace_transaction",
                {"mode": "write", "path": "pending.txt", "content": "hello"},
            )
            first_guard = self.server._mutation_replay_guard_begin(
                "workspace_transaction",
                {"mode": "write", "path": "pending.txt", "content": "hello"},
                categories,
            )
            duplicate_guard = self.server._mutation_replay_guard_begin(
                "workspace_transaction",
                {"mode": "write", "path": "pending.txt", "content": "hello"},
                categories,
            )

        self.assertTrue(first_guard["enabled"])
        self.assertFalse(first_guard.get("duplicate", False))
        self.assertTrue(duplicate_guard["duplicate"])
        duplicate = duplicate_guard["response"]
        self.assertFalse(duplicate["ok"])
        self.assertEqual(duplicate["replay_guard"]["original_status"], "started")
        self.assertEqual(
            duplicate["error"],
            "original mutating request did not complete successfully",
        )

    def test_mutation_replay_guard_denies_key_reuse_with_different_digest(self):
        self.server.ALLOW_MUTATIONS = True
        self.server.MCP_MUTATION_REPLAY_GUARD_ENABLED = True

        with self._authorized_http_tool_context(session_id="replay-session"):
            self.server.workspace_transaction(
                mode="write",
                path="first-key-use.txt",
                content="one",
                overwrite=False,
                request_metadata={"idempotency_key": "same-key"},
            )
            with self.assertRaises(PermissionError):
                self.server.workspace_transaction(
                    mode="write",
                    path="second-key-use.txt",
                    content="two",
                    overwrite=False,
                    request_metadata={"idempotency_key": "same-key"},
                )

        events = self._audit_events()
        conflict = [event for event in events if event["tool_name"] == "mutation_replay_guard" and not event["success"]][0]
        self.assertEqual(conflict["arguments"]["replay_guard"]["decision"], "idempotency_key_conflict")
        self.assertEqual(conflict["reason"], "idempotency key reused for different mutating digest")
        audit_text = self.server.MCP_AUDIT_LOG_FILE.read_text(encoding="utf-8")
        self.assertNotIn("one", audit_text)
        self.assertNotIn("two", audit_text)

    def test_mutation_replay_guard_read_only_categories_do_not_journal(self):
        self.server.MCP_MUTATION_REPLAY_GUARD_ENABLED = True

        with self._authorized_http_tool_context(
            session_id="read-only-session",
            scopes=frozenset({self.server.MCP_SCOPE_READ}),
        ):
            categories = self.server._require_tool_security_gate("workspace_transaction", {"mode": "validate"})
            guard = self.server._mutation_replay_guard_begin("workspace_transaction", {"mode": "validate"}, categories)

        self.assertEqual(categories, ["read-only"])
        self.assertFalse(guard["enabled"])
        self.assertFalse((self.repo_path / self.server.MCP_MUTATION_REPLAY_GUARD_JOURNAL_FILE).exists())

    def test_mutation_replay_guard_prunes_by_ttl_and_count(self):
        self.server.MCP_MUTATION_REPLAY_GUARD_MAX_ENTRIES = 2
        self.server.MCP_MUTATION_REPLAY_GUARD_TTL_SECONDS = 60
        now = datetime.now(timezone.utc)
        entries = [
            {"guard_id": "old", "created_at": (now - timedelta(seconds=120)).isoformat()},
            {"guard_id": "a", "created_at": (now - timedelta(seconds=3)).isoformat()},
            {"guard_id": "b", "created_at": (now - timedelta(seconds=2)).isoformat()},
            {"guard_id": "c", "created_at": (now - timedelta(seconds=1)).isoformat()},
        ]

        pruned = self.server._mutation_replay_prune_entries(entries, now=now)

        self.assertEqual([entry["guard_id"] for entry in pruned], ["b", "c"])

    def test_governance_audit_summary_counts_replay_guard_decisions(self):
        events = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool_name": "mutation_replay_guard",
                "categories": ["audit", "write"],
                "success": True,
                "reason": "recorded",
                "arguments": {"replay_guard": {"decision": "recorded"}},
            },
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool_name": "mutation_replay_guard",
                "categories": ["audit", "write"],
                "success": True,
                "reason": "duplicate_suppressed",
                "arguments": {"replay_guard": {"decision": "duplicate_suppressed"}},
            },
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool_name": "mutation_replay_guard",
                "categories": ["audit", "write"],
                "success": False,
                "reason": "idempotency key reused for different mutating digest",
                "arguments": {"replay_guard": {"decision": "idempotency_key_conflict"}},
            },
        ]

        summary = self.server._aggregate_audit_events(events)["mutation_replay_guard"]

        self.assertEqual(summary["event_count"], 3)
        self.assertEqual(summary["recorded_count"], 1)
        self.assertEqual(summary["duplicate_suppressed_count"], 1)
        self.assertEqual(summary["conflict_count"], 1)
        self.assertEqual(summary["by_decision"]["idempotency_key_conflict"], 1)

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
