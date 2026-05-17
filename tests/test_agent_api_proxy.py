# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio
import json
from pathlib import Path

from tests.server_test_support import ServerToolsTestBase


class FakeRequest:
    def __init__(self, payload=None, *, query_params=None, invalid_json=False):
        self._payload = payload
        self.query_params = query_params or {}
        self.invalid_json = invalid_json
        self.disconnect_after = False

    async def json(self):
        if self.invalid_json:
            raise ValueError("invalid json")
        return self._payload

    async def is_disconnected(self):
        return self.disconnect_after


async def collect_stream_text(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


class AgentAPIProxyTest(ServerToolsTestBase):
    def setUp(self):
        super().setUp()
        self.proxy_attrs = [
            "MCP_AGENT_PROXY_ENABLED",
            "MCP_AGENT_PROXY_ALLOW_ONLINE",
            "MCP_AGENT_PROXY_NO_NETWORK",
            "MCP_AGENT_PROXY_PROVIDER_NAME",
            "MCP_AGENT_PROXY_PROVIDER_BASE_URL",
            "MCP_AGENT_PROXY_PROVIDER_CHAT_COMPLETIONS_URL",
            "MCP_AGENT_PROXY_PROVIDER_API_KEY",
            "MCP_AGENT_PROXY_PROVIDER_AUTH_HEADER",
            "MCP_AGENT_PROXY_MODEL_ALLOWLIST_RAW",
            "MCP_AGENT_PROXY_LOCAL_MODELS_RAW",
            "MCP_AGENT_PROXY_PREFER_LOCAL",
            "MCP_AGENT_PROXY_TIMEOUT_SECONDS",
            "MCP_AGENT_PROXY_MAX_INPUT_TOKENS",
            "MCP_AGENT_PROXY_MAX_OUTPUT_TOKENS",
            "MCP_AGENT_PROXY_MAX_COST_USD",
            "MCP_AGENT_PROXY_COST_PER_1K_INPUT_USD",
            "MCP_AGENT_PROXY_COST_PER_1K_OUTPUT_USD",
            "MCP_AGENT_PROXY_ANONYMIZE_TERMS_RAW",
            "MCP_AGENT_PROXY_STRICT_DISCLOSURE_AUDIT",
            "MCP_AGENT_PROXY_AUDIT_EMERGENCY_ALLOW",
            "MCP_AGENT_PROXY_DISCLOSURE_AUDIT_FILE",
            "MCP_AGENT_PROXY_MEMORY_CAPTURE_ENABLED",
            "MCP_AGENT_PROXY_MEMORY_CAPTURE_REQUIRE_MUTATIONS",
            "MCP_AUDIT_LOG_FILE",
            "AGENT_EXECUTION_MODE_ENV",
        ]
        self.orig_proxy_values = {name: getattr(self.server, name) for name in self.proxy_attrs}
        self.orig_post_json = self.server._agent_proxy_http_post_json
        self.orig_stream_json = self.server._agent_proxy_http_stream_json
        self.server.MCP_AGENT_PROXY_DISCLOSURE_AUDIT_FILE = Path(
            ".codebase-tooling-mcp/audit/proxy-disclosures.jsonl"
        )
        self.server.MCP_AUDIT_LOG_FILE = self.repo_path / ".codebase-tooling-mcp/audit/security.jsonl"
        self.server.MCP_AGENT_PROXY_TIMEOUT_SECONDS = 5
        self.server.MCP_AGENT_PROXY_MAX_INPUT_TOKENS = 2000
        self.server.MCP_AGENT_PROXY_MAX_OUTPUT_TOKENS = 2000
        self.server.MCP_AGENT_PROXY_MAX_COST_USD = 0
        self.server.MCP_AGENT_PROXY_COST_PER_1K_INPUT_USD = 0
        self.server.MCP_AGENT_PROXY_COST_PER_1K_OUTPUT_USD = 0
        self.server.MCP_AGENT_PROXY_AUDIT_EMERGENCY_ALLOW = False
        self.server.MCP_AGENT_PROXY_STRICT_DISCLOSURE_AUDIT = True
        self.server.AGENT_EXECUTION_MODE_ENV = "online"

    def tearDown(self):
        self.server._agent_proxy_http_post_json = self.orig_post_json
        self.server._agent_proxy_http_stream_json = self.orig_stream_json
        for name, value in self.orig_proxy_values.items():
            setattr(self.server, name, value)
        super().tearDown()

    def base_payload(self, **overrides):
        payload = {
            "model": "gpt-proxy-test",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 64,
        }
        payload.update(overrides)
        return payload

    def response_json(self, response):
        return json.loads(response.body.decode("utf-8"))

    def disclosure_text(self):
        path = self.server._agent_proxy_resolve_audit_path(
            self.server.MCP_AGENT_PROXY_DISCLOSURE_AUDIT_FILE
        )
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def enable_online(self):
        self.server.MCP_AGENT_PROXY_ENABLED = True
        self.server.MCP_AGENT_PROXY_ALLOW_ONLINE = True
        self.server.MCP_AGENT_PROXY_NO_NETWORK = False
        self.server.MCP_AGENT_PROXY_PROVIDER_BASE_URL = "https://provider.example/v1"
        self.server.MCP_AGENT_PROXY_PROVIDER_CHAT_COMPLETIONS_URL = ""
        self.server.MCP_AGENT_PROXY_MODEL_ALLOWLIST_RAW = "gpt-proxy-test"
        self.server.MCP_AGENT_PROXY_LOCAL_MODELS_RAW = "local-*"
        self.server.MCP_AGENT_PROXY_PREFER_LOCAL = False

    def test_proxy_disabled_by_default_blocks_chat_completions(self):
        self.server.MCP_AGENT_PROXY_ENABLED = False

        response = asyncio.run(
            self.server.openai_chat_completions(FakeRequest(self.base_payload()))
        )
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "agent_proxy_disabled")
        self.assertTrue(self.server._http_path_is_protected_mcp("/v1/chat/completions"))

    def test_online_non_streaming_anonymizes_redacts_audits_and_deanonymizes(self):
        self.enable_online()
        self.server.MCP_AGENT_PROXY_ANONYMIZE_TERMS_RAW = "Acme Corp"
        captured = {}

        def fake_post(url, payload, timeout):
            captured["url"] = url
            captured["payload"] = payload
            text = json.dumps(payload)
            term_placeholder = self.server._AGENT_PROXY_PLACEHOLDER_RE.findall(text)[0]
            secret_placeholder = [
                p
                for p in self.server._AGENT_PROXY_PLACEHOLDER_RE.findall(text)
                if "REDACTED_SECRET" in p
            ][0]
            return {
                "id": "upstream-id",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": f"Hello {term_placeholder}; secret {secret_placeholder}",
                        },
                        "finish_reason": "stop",
                    }
                ],
            }

        self.server._agent_proxy_http_post_json = fake_post
        request = FakeRequest(
            self.base_payload(
                messages=[
                    {
                        "role": "user",
                        "content": "Ask Acme Corp via admin@example.com with api_key=sk-1234567890abcdef",
                    }
                ]
            )
        )

        response = asyncio.run(self.server.openai_chat_completions(request))
        payload = self.response_json(response)
        forwarded = json.dumps(captured["payload"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["url"], "https://provider.example/v1/chat/completions")
        self.assertNotIn("Acme Corp", forwarded)
        self.assertNotIn("admin@example.com", forwarded)
        self.assertNotIn("sk-1234567890abcdef", forwarded)
        self.assertIn("Acme Corp", payload["choices"][0]["message"]["content"])
        self.assertIn("[REDACTED_SECRET]", payload["choices"][0]["message"]["content"])
        self.assertEqual(payload["agent_proxy"]["routing"]["backend"], "online")

        audit = self.disclosure_text()
        self.assertIn('"phase": "request"', audit)
        self.assertIn('"phase": "response"', audit)
        self.assertNotIn("Acme Corp", audit)
        self.assertNotIn("admin@example.com", audit)
        self.assertNotIn("sk-1234567890abcdef", audit)
        summary = self.server._agent_proxy_disclosure_summary({})
        self.assertGreaterEqual(summary["disclosure_categories"].get("term", 0), 1)
        self.assertGreaterEqual(summary["disclosure_categories"].get("email", 0), 1)
        self.assertGreaterEqual(summary["disclosure_categories"].get("opaque_redactions", 0), 1)

    def test_online_streaming_uses_sse_and_restores_split_placeholders(self):
        self.enable_online()
        self.server.MCP_AGENT_PROXY_ANONYMIZE_TERMS_RAW = "Acme Corp"

        def fake_stream(url, payload, timeout):
            text = json.dumps(payload)
            placeholder = [
                p
                for p in self.server._AGENT_PROXY_PLACEHOLDER_RE.findall(text)
                if "ANON_TERM" in p
            ][0]
            midpoint = len(placeholder) // 2
            yield {"choices": [{"index": 0, "delta": {"role": "assistant"}}]}
            yield {"choices": [{"index": 0, "delta": {"content": "Hi " + placeholder[:midpoint]}}]}
            yield {"choices": [{"index": 0, "delta": {"content": placeholder[midpoint:] + "!"}}]}
            yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}

        self.server._agent_proxy_http_stream_json = fake_stream
        response = asyncio.run(
            self.server.openai_chat_completions(
                FakeRequest(
                    self.base_payload(
                        stream=True,
                        messages=[{"role": "user", "content": "Hello Acme Corp"}],
                    )
                )
            )
        )
        body = asyncio.run(collect_stream_text(response))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "text/event-stream")
        self.assertIn("data: [DONE]", body)
        self.assertIn("Acme Corp", body)
        self.assertNotIn("__MCP_ANON_TERM", body)

    def test_strict_disclosure_audit_failure_blocks_online_call(self):
        self.enable_online()
        called = {"count": 0}
        blocked_path = self.repo_path / "audit-as-directory"
        blocked_path.mkdir()
        self.server.MCP_AGENT_PROXY_DISCLOSURE_AUDIT_FILE = blocked_path

        def fake_post(url, payload, timeout):
            called["count"] += 1
            return {}

        self.server._agent_proxy_http_post_json = fake_post
        response = asyncio.run(
            self.server.openai_chat_completions(FakeRequest(self.base_payload()))
        )
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(payload["error"]["code"], "agent_proxy_disclosure_audit_failed")
        self.assertEqual(called["count"], 0)

    def test_no_network_mode_routes_locally_without_provider_call(self):
        self.enable_online()
        self.server.MCP_AGENT_PROXY_NO_NETWORK = True
        called = {"count": 0}

        def fake_post(url, payload, timeout):
            called["count"] += 1
            return {}

        self.server._agent_proxy_http_post_json = fake_post
        response = asyncio.run(
            self.server.openai_chat_completions(FakeRequest(self.base_payload()))
        )
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(called["count"], 0)
        self.assertEqual(payload["agent_proxy"]["routing"]["backend"], "local")
        self.assertEqual(payload["agent_proxy"]["routing"]["reason"], "offline_no_network")

    def test_model_allowlist_blocks_unapproved_online_model(self):
        self.enable_online()
        self.server.MCP_AGENT_PROXY_MODEL_ALLOWLIST_RAW = "approved-model"

        response = asyncio.run(
            self.server.openai_chat_completions(
                FakeRequest(self.base_payload(model="other-model"))
            )
        )
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "agent_proxy_route_blocked")
        self.assertIn("model_not_allowlisted", payload["error"]["message"])

    def test_memory_capture_is_policy_gated_and_redacted(self):
        self.server.MCP_AGENT_PROXY_ENABLED = True
        self.server.MCP_AGENT_PROXY_ALLOW_ONLINE = False
        self.server.MCP_AGENT_PROXY_NO_NETWORK = True
        self.server.MCP_AGENT_PROXY_MEMORY_CAPTURE_ENABLED = True
        self.server.MCP_AGENT_PROXY_MEMORY_CAPTURE_REQUIRE_MUTATIONS = True
        self.server.ALLOW_MUTATIONS = False

        response = asyncio.run(
            self.server.openai_chat_completions(
                FakeRequest(
                    self.base_payload(
                        model="local-micro",
                        messages=[
                            {
                                "role": "user",
                                "content": "Do not store raw Secret Project text",
                            }
                        ],
                    )
                )
            )
        )
        payload = self.response_json(response)
        self.assertFalse(payload["agent_proxy"]["memory"]["captured"])
        self.assertEqual(payload["agent_proxy"]["memory"]["reason"], "mutations_disabled")

        self.server.ALLOW_MUTATIONS = True
        response = asyncio.run(
            self.server.openai_chat_completions(
                FakeRequest(
                    self.base_payload(
                        model="local-micro",
                        messages=[
                            {
                                "role": "user",
                                "content": "Do not store raw Secret Project text",
                            }
                        ],
                    )
                )
            )
        )
        payload = self.response_json(response)
        self.assertTrue(payload["agent_proxy"]["memory"]["captured"])
        memory_text = (
            self.repo_path / ".codebase-tooling-mcp/memory/context_memory.json"
        ).read_text(encoding="utf-8")
        self.assertNotIn("Secret Project", memory_text)
        self.assertIn("prompt_digest", memory_text)
