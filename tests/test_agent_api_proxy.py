# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio
import json
import os
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
        self.proxy_env_names = [
            "MCP_AGENT_PROXY_ENABLED",
            "MCP_AGENT_PROXY_CONFIG_FILE",
            "MCP_AGENT_PROXY_ALLOW_ONLINE",
            "MCP_AGENT_PROXY_NO_NETWORK",
            "MCP_AGENT_PROXY_PROVIDER_NAME",
            "MCP_AGENT_PROXY_PROVIDER_BASE_URL",
            "MCP_AGENT_PROXY_PROVIDER_CHAT_COMPLETIONS_URL",
            "MCP_AGENT_PROXY_MODEL_ALLOWLIST",
            "MCP_AGENT_PROXY_DEFAULT_MODEL",
            "MCP_AGENT_PROXY_LOCAL_MODELS",
            "MCP_AGENT_PROXY_PREFER_LOCAL",
        ]
        self.orig_proxy_env_values = {
            name: os.environ.get(name) for name in self.proxy_env_names
        }
        for name in self.proxy_env_names:
            os.environ.pop(name, None)
        self.proxy_attrs = [
            "MCP_AGENT_PROXY_ENABLED",
            "MCP_AGENT_PROXY_CONFIG_RELATIVE_PATH",
            "MCP_AGENT_PROXY_DEFAULT_MODEL",
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
        for name, value in self.orig_proxy_env_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
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

    def disclosure_events(self):
        return [json.loads(line) for line in self.disclosure_text().splitlines() if line.strip()]

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

    def test_online_non_streaming_redacts_full_bearer_authorization_before_forwarding(self):
        self.enable_online()
        self.server.MCP_AGENT_PROXY_MEMORY_CAPTURE_ENABLED = True
        self.server.MCP_AGENT_PROXY_MEMORY_CAPTURE_REQUIRE_MUTATIONS = False
        captured = {}
        bearer_secret = "abcDEF1234567890suffix"
        bearer_header = f"Authorization: Bearer {bearer_secret}"

        def fake_post(url, payload, timeout):
            captured["payload"] = payload
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ]
            }

        self.server._agent_proxy_http_post_json = fake_post
        response = asyncio.run(
            self.server.openai_chat_completions(
                FakeRequest(
                    self.base_payload(
                        messages=[
                            {
                                "role": "user",
                                "content": f"Call upstream with {bearer_header}",
                            }
                        ]
                    )
                )
            )
        )

        self.assertEqual(response.status_code, 200)
        forwarded = json.dumps(captured["payload"], sort_keys=True)
        memory_text = (
            self.repo_path / ".codebase-tooling-mcp/memory/context_memory.json"
        ).read_text(encoding="utf-8")
        combined = "\n".join([forwarded, self.disclosure_text(), memory_text])
        self.assertIn("__MCP_REDACTED_SECRET", forwarded)
        self.assertNotIn(bearer_secret, combined)
        self.assertNotIn(bearer_secret[-12:], combined)
        self.assertNotIn(f"Bearer {bearer_secret}", combined)
        summary = self.server._agent_proxy_disclosure_summary({})
        self.assertGreaterEqual(summary["disclosure_categories"].get("opaque_redactions", 0), 1)

    def test_online_call_writes_auditor_evidence_packet_without_raw_sensitive_text(self):
        self.enable_online()
        self.server.MCP_AGENT_PROXY_ANONYMIZE_TERMS_RAW = "NDA Project"
        self.server.MCP_AGENT_PROXY_MEMORY_CAPTURE_ENABLED = True
        self.server.MCP_AGENT_PROXY_MEMORY_CAPTURE_REQUIRE_MUTATIONS = False
        captured = {}

        def fake_post(url, payload, timeout):
            captured["payload"] = payload
            return {
                "id": "upstream-id",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "review complete"},
                        "finish_reason": "stop",
                    }
                ],
            }

        self.server._agent_proxy_http_post_json = fake_post
        request_payload = self.base_payload(
            metadata={"workflow_task_id": "wf-123"},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "repo_lookup",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            tool_choice="auto",
            messages=[
                {
                    "role": "user",
                    "content": "Review NDA Project in /home/user/repo with password=supersecret",
                }
            ],
        )

        response = asyncio.run(
            self.server.openai_chat_completions(FakeRequest(request_payload))
        )
        payload = self.response_json(response)
        trace_id = payload["agent_proxy"]["trace_id"]
        response_event = next(
            event for event in self.disclosure_events() if event.get("phase") == "response"
        )
        packet = response_event["evidence_packet"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(packet["schema"], "mcp_agent_proxy.provider_call_evidence.v1")
        self.assertEqual(packet["audience"], "buyer_auditor")
        self.assertEqual(packet["trace_id"], trace_id)
        self.assertEqual(packet["provider_route"]["provider"], "openai-compatible")
        self.assertEqual(packet["provider_route"]["model"], "gpt-proxy-test")
        self.assertTrue(packet["policy_decision"]["online_allowed"])
        self.assertEqual(
            packet["policy_decision"]["anonymizer_profile"],
            self.server.MCP_AGENT_PROXY_ANONYMIZATION_PROFILE,
        )
        self.assertFalse(packet["policy_decision"]["offline_controls"]["no_network"])
        self.assertEqual(
            packet["input"]["canonical_input_sha256"],
            self.server._agent_proxy_digest(request_payload),
        )
        self.assertEqual(
            packet["input"]["provider_input_sha256"],
            self.server._agent_proxy_digest(captured["payload"]),
        )
        self.assertNotEqual(
            packet["input"]["canonical_input_sha256"],
            packet["input"]["provider_input_sha256"],
        )
        self.assertEqual(
            packet["output"]["response_sha256"],
            response_event["disclosure"]["response_digest"],
        )
        self.assertTrue(packet["memory_admission"]["admitted"])
        self.assertEqual(packet["memory_admission"]["state"], "admitted")
        self.assertFalse(packet["memory_admission"]["raw_conversation_stored"])
        self.assertTrue(packet["context_boundary"]["tool_boundary"]["tools_present"])
        self.assertFalse(packet["context_boundary"]["repo_boundary"]["repo_path_disclosed"])
        self.assertFalse(
            packet["context_boundary"]["repo_boundary"]["raw_repo_files_attached_by_proxy"]
        )
        self.assertEqual(packet["review_cure"]["review_state"], "not_reviewed")
        self.assertFalse(packet["review_cure"]["disclosure_violation_found"])
        self.assertEqual(len(packet["disclosure_receipt"]["stable_digest"]), 64)

        second_response = asyncio.run(
            self.server.openai_chat_completions(FakeRequest(request_payload))
        )
        self.assertEqual(second_response.status_code, 200)
        response_events = [
            event for event in self.disclosure_events() if event.get("phase") == "response"
        ]
        second_packet = response_events[-1]["evidence_packet"]
        self.assertNotEqual(second_packet["trace_id"], trace_id)
        self.assertEqual(second_packet["provider_route"], packet["provider_route"])
        self.assertEqual(second_packet["policy_decision"], packet["policy_decision"])
        self.assertEqual(
            second_packet["disclosure_receipt"]["stable_digest"],
            packet["disclosure_receipt"]["stable_digest"],
        )

        audit = self.disclosure_text()
        self.assertNotIn("NDA Project", audit)
        self.assertNotIn("supersecret", audit)
        self.assertNotIn("/home/user/repo", audit)
        summary = self.server._agent_proxy_disclosure_summary({"trace_id": trace_id})
        self.assertEqual(summary["event_count"], 2)
        self.assertEqual(summary["evidence_packet_count"], 2)
        self.assertIn(
            packet["disclosure_receipt"]["stable_digest"], summary["disclosure_receipts"]
        )

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

    def test_online_streaming_redacts_full_bearer_authorization_before_forwarding(self):
        self.enable_online()
        captured = {}
        bearer_secret = "streamABCDEF1234567890suffix"
        bearer_header = f"Authorization: Bearer {bearer_secret}"

        def fake_stream(url, payload, timeout):
            captured["payload"] = payload
            yield {"choices": [{"index": 0, "delta": {"role": "assistant"}}]}
            yield {"choices": [{"index": 0, "delta": {"content": "stream ok"}}]}
            yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}

        self.server._agent_proxy_http_stream_json = fake_stream
        response = asyncio.run(
            self.server.openai_chat_completions(
                FakeRequest(
                    self.base_payload(
                        stream=True,
                        messages=[
                            {
                                "role": "user",
                                "content": f"Stream with {bearer_header}",
                            }
                        ],
                    )
                )
            )
        )
        body = asyncio.run(collect_stream_text(response))

        self.assertEqual(response.status_code, 200)
        forwarded = json.dumps(captured["payload"], sort_keys=True)
        combined = "\n".join([forwarded, self.disclosure_text(), body])
        self.assertIn("__MCP_REDACTED_SECRET", forwarded)
        self.assertNotIn(bearer_secret, combined)
        self.assertNotIn(bearer_secret[-12:], combined)
        self.assertNotIn(f"Bearer {bearer_secret}", combined)
        self.assertIn("data: [DONE]", body)

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

    def test_runtime_toml_configures_forwarding_without_env_routing_vars(self):
        self.server.MCP_AGENT_PROXY_ENABLED = False
        self.server.MCP_AGENT_PROXY_ALLOW_ONLINE = False
        self.server.MCP_AGENT_PROXY_PROVIDER_BASE_URL = ""
        self.server.MCP_AGENT_PROXY_MODEL_ALLOWLIST_RAW = ""
        self.server.MCP_AGENT_PROXY_PREFER_LOCAL = False
        self.write_repo_text(
            ".codebase-tooling-mcp/agent-proxy.local.toml",
            "[agent_proxy]\n"
            "enabled = true\n"
            "allow_online = true\n"
            "provider = \"openai-compatible\"\n"
            "model = \"gpt-proxy-test\"\n"
            "apiBase = \"https://toml-provider.example/v1\"\n",
        )
        captured = {}

        def fake_post(url, payload, timeout):
            captured["url"] = url
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ]
            }

        self.server._agent_proxy_http_post_json = fake_post
        response = asyncio.run(
            self.server.openai_chat_completions(FakeRequest(self.base_payload()))
        )
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["url"], "https://toml-provider.example/v1/chat/completions")
        self.assertEqual(payload["agent_proxy"]["routing"]["backend"], "online")
        self.assertTrue(self.server._agent_proxy_status_payload()["config"]["loaded"])

    def test_env_routing_vars_override_runtime_toml_config(self):
        self.server.MCP_AGENT_PROXY_ENABLED = False
        self.server.MCP_AGENT_PROXY_ALLOW_ONLINE = False
        self.server.MCP_AGENT_PROXY_PROVIDER_BASE_URL = ""
        self.server.MCP_AGENT_PROXY_MODEL_ALLOWLIST_RAW = ""
        self.server.MCP_AGENT_PROXY_PREFER_LOCAL = False
        self.write_repo_text(
            ".codebase-tooling-mcp/agent-proxy.local.toml",
            "[agent_proxy]\n"
            "enabled = true\n"
            "allow_online = true\n"
            "provider = \"openai-compatible\"\n"
            "model = \"toml-model\"\n"
            "apiBase = \"https://toml-provider.example/v1\"\n",
        )
        os.environ["MCP_AGENT_PROXY_PROVIDER_BASE_URL"] = "https://env-provider.example/v1"
        os.environ["MCP_AGENT_PROXY_MODEL_ALLOWLIST"] = "env-model"
        captured = {}

        def fake_post(url, payload, timeout):
            captured["url"] = url
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ]
            }

        self.server._agent_proxy_http_post_json = fake_post
        response = asyncio.run(
            self.server.openai_chat_completions(
                FakeRequest(self.base_payload(model="env-model"))
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["url"], "https://env-provider.example/v1/chat/completions")

    def test_agent_proxy_runtime_config_is_gitignored_and_example_is_committed(self):
        repo_root = Path(__file__).resolve().parents[1]
        gitignore_text = (repo_root / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("/.codebase-tooling-mcp/agent-proxy.local.toml", gitignore_text)
        example = repo_root / "docs/examples/agent-proxy.local.toml.example"
        self.assertTrue(example.is_file())
        example_text = example.read_text(encoding="utf-8")
        self.assertIn("apiKey = \"${{ secrets.AGENT_PROXY_API_KEY }}\"", example_text)
        self.assertNotIn("sk-", example_text)
        self.write_repo_text(".gitignore", gitignore_text)
        self.assertTrue(
            self.git("check-ignore", ".codebase-tooling-mcp/agent-proxy.local.toml").stdout.strip()
        )

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

    def test_policy_limits_block_before_provider_call(self):
        self.enable_online()
        called = {"count": 0}

        def fake_post(url, payload, timeout):
            called["count"] += 1
            return {}

        self.server._agent_proxy_http_post_json = fake_post
        self.server.MCP_AGENT_PROXY_MAX_OUTPUT_TOKENS = 10
        response = asyncio.run(
            self.server.openai_chat_completions(FakeRequest(self.base_payload(max_tokens=11)))
        )
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "agent_proxy_policy_denied")
        self.assertEqual(called["count"], 0)

        self.server.MCP_AGENT_PROXY_MAX_OUTPUT_TOKENS = 100
        self.server.MCP_AGENT_PROXY_MAX_COST_USD = 0.0001
        self.server.MCP_AGENT_PROXY_COST_PER_1K_OUTPUT_USD = 1
        response = asyncio.run(
            self.server.openai_chat_completions(FakeRequest(self.base_payload(max_tokens=64)))
        )
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "agent_proxy_policy_denied")
        self.assertEqual(called["count"], 0)

    def test_disclosure_summary_filters_by_trace_and_time_range(self):
        self.enable_online()
        self.server.MCP_AGENT_PROXY_ANONYMIZE_TERMS_RAW = "Acme Corp"

        def fake_post(url, payload, timeout):
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ]
            }

        self.server._agent_proxy_http_post_json = fake_post
        response = asyncio.run(
            self.server.openai_chat_completions(
                FakeRequest(
                    self.base_payload(messages=[{"role": "user", "content": "Hello Acme Corp"}])
                )
            )
        )
        trace_id = self.response_json(response)["agent_proxy"]["trace_id"]

        summary = self.server._agent_proxy_disclosure_summary(
            {
                "trace_id": trace_id,
                "since": "1970-01-01T00:00:00+00:00",
                "until": "2999-01-01T00:00:00+00:00",
            }
        )
        self.assertEqual(summary["event_count"], 2)
        self.assertEqual(summary["trace_count"], 1)
        self.assertEqual(summary["filters"]["trace_id"], trace_id)
        self.assertGreaterEqual(summary["disclosure_categories"].get("term", 0), 1)

        future = self.server._agent_proxy_disclosure_summary(
            {"since": "2999-01-01T00:00:00+00:00"}
        )
        self.assertEqual(future["event_count"], 0)

        endpoint_response = asyncio.run(
            self.server.agent_proxy_disclosures(
                FakeRequest(
                    query_params={
                        "trace_id": trace_id,
                        "since": "1970-01-01T00:00:00Z",
                    }
                )
            )
        )
        endpoint_payload = self.response_json(endpoint_response)
        self.assertEqual(endpoint_payload["event_count"], 2)
        self.assertFalse(endpoint_payload["privacy"]["raw_prompts_returned"])

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

    def test_model_fallback_chat_assists_continue_configuration(self):
        response = asyncio.run(
            self.server.continue_model_fallback_chat_completions(
                FakeRequest(
                    {
                        "model": "model-fallback",
                        "messages": [{"role": "user", "content": "help configure Continue"}],
                    }
                )
            )
        )
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 200)
        content = payload["choices"][0]["message"]["content"]
        self.assertIn("I can set up Continue", content)
        self.assertIn("MCP_HTTP_BEARER_TOKEN", content)
        self.assertIn("/v1/model-fallback/configure", content)
        self.assertIn("not the real coding model", content)
        self.assertEqual("continue_model_fallback.status.v1", payload["model_fallback"]["schema"])
        self.assertTrue(payload["model_fallback"]["default_profiles"])
        self.assertTrue(payload["model_fallback"]["mcp_servers"])
        self.assertTrue(
            any(
                server.get("uses_mcp_http_bearer_token_secret")
                for server in payload["model_fallback"]["mcp_servers"]
            )
        )
        self.assertEqual(
            "bundled_default",
            payload["model_fallback"]["routing"]["source"],
        )
        self.assertTrue(self.server._http_path_is_protected_mcp("/v1/model-fallback/configure"))

    def test_model_fallback_uses_detected_continue_default_and_stays_setup_wizard(self):
        self.write_repo_text(
            ".continue/model-routing.yaml",
            "schema: v1\n"
            "router:\n"
            "  model: qwen2.5-coder:1.5b\n"
            "  file: .continue/models/coding-qwen2.5-coder-1.5b.yaml\n",
        )
        self.write_repo_text(
            ".continue/models/coding-qwen2.5-coder-1.5b.yaml",
            "name: coding-qwen2.5-coder-1.5b\n"
            "version: 0.0.1\n"
            "schema: v1\n"
            "models:\n"
            "  - name: Coding Micro - Qwen2.5 Coder 1.5B\n"
            "    provider: ollama\n"
            "    model: qwen2.5-coder:1.5b\n"
            "    apiBase: http://127.0.0.1:2345\n"
            "    roles:\n"
            "      - chat\n",
        )
        response = asyncio.run(
            self.server.continue_model_fallback_chat_completions(
                FakeRequest(
                    {
                        "model": "model-fallback",
                        "messages": [
                            {
                                "role": "user",
                                "content": "Write a Python sort function",
                            }
                        ],
                    }
                )
            )
        )
        payload = self.response_json(response)
        content = payload["choices"][0]["message"]["content"]

        self.assertIn("qwen2.5-coder:1.5b", content)
        self.assertIn("I'll use that by default", content)
        self.assertIn("First, paste the MCP token", content)
        self.assertNotIn("def ", content)
        self.assertEqual(1, content.count("First,"))
        self.assertLessEqual(content.count("?"), 1)
        self.assertEqual(
            "qwen2.5-coder:1.5b",
            payload["model_fallback"]["detected_default"]["model"],
        )
        self.assertEqual(1, len(payload["model_fallback"]["local_profiles"]))

    def test_model_fallback_stops_asking_after_five_user_requests(self):
        response = asyncio.run(
            self.server.continue_model_fallback_chat_completions(
                FakeRequest(
                    {
                        "model": "model-fallback",
                        "messages": [
                            {"role": "user", "content": "one"},
                            {"role": "user", "content": "two"},
                            {"role": "user", "content": "three"},
                            {"role": "user", "content": "four"},
                            {"role": "user", "content": "five"},
                        ],
                    }
                )
            )
        )
        content = self.response_json(response)["choices"][0]["message"]["content"]

        self.assertIn("I won't ask more setup questions", content)
        self.assertNotIn("First, paste", content)
        self.assertNotIn("Next, send", content)

    def test_model_fallback_chat_streams_for_continue_ui(self):
        response = asyncio.run(
            self.server.continue_model_fallback_chat_completions(
                FakeRequest(
                    {
                        "model": "model-fallback",
                        "stream": True,
                        "messages": [{"role": "user", "content": "setup"}],
                    }
                )
            )
        )
        stream_text = asyncio.run(collect_stream_text(response))
        data_lines = [
            line.removeprefix("data: ")
            for line in stream_text.splitlines()
            if line.startswith("data: ")
        ]
        chunks = [json.loads(line) for line in data_lines if line != "[DONE]"]
        content = "".join(
            choice.get("delta", {}).get("content", "")
            for chunk in chunks
            for choice in chunk.get("choices", [])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual("[DONE]", data_lines[-1])
        self.assertGreaterEqual(len(chunks), 3)
        self.assertTrue(all(chunk["object"] == "chat.completion.chunk" for chunk in chunks))
        self.assertEqual({"role": "assistant"}, chunks[0]["choices"][0]["delta"])
        self.assertEqual("stop", chunks[-1]["choices"][0]["finish_reason"])
        self.assertIn("I can set up Continue", content)
        self.assertIn("MCP_HTTP_BEARER_TOKEN", content)
        self.assertIn("not the real coding model", content)

    def test_model_fallback_configure_requires_mutate_scope_in_http_context(self):
        self.server.ALLOW_MUTATIONS = True
        auth_token = self.server._HTTP_REQUEST_AUTHORIZED.set(True)
        scope_token = self.server._HTTP_REQUEST_GRANTED_SCOPES.set(
            frozenset({self.server.MCP_SCOPE_READ})
        )
        try:
            with self.assertRaises(self.server.HTTPInsufficientScopeError) as raised:
                asyncio.run(
                    self.server.continue_model_fallback_configure(
                        FakeRequest(
                            {
                                "provider": "openai",
                                "model": "fallback-target",
                                "apiBase": "http://127.0.0.1:8787/v1",
                            }
                        )
                    )
                )
        finally:
            self.server._HTTP_REQUEST_GRANTED_SCOPES.reset(scope_token)
            self.server._HTTP_REQUEST_AUTHORIZED.reset(auth_token)

        self.assertEqual(self.server.MCP_SCOPE_MUTATE, raised.exception.required_scope)
        self.assertFalse((self.repo_path / ".continue/model-routing.yaml").exists())

    def test_model_fallback_configure_requires_mutate_scope_before_dry_run(self):
        self.server.ALLOW_MUTATIONS = False
        auth_token = self.server._HTTP_REQUEST_AUTHORIZED.set(True)
        scope_token = self.server._HTTP_REQUEST_GRANTED_SCOPES.set(
            frozenset({self.server.MCP_SCOPE_READ})
        )
        try:
            with self.assertRaises(self.server.HTTPInsufficientScopeError) as raised:
                asyncio.run(
                    self.server.continue_model_fallback_configure(
                        FakeRequest(
                            {
                                "provider": "openai",
                                "model": "fallback-target",
                                "apiBase": "http://127.0.0.1:8787/v1",
                            }
                        )
                    )
                )
        finally:
            self.server._HTTP_REQUEST_GRANTED_SCOPES.reset(scope_token)
            self.server._HTTP_REQUEST_AUTHORIZED.reset(auth_token)

        self.assertEqual(self.server.MCP_SCOPE_MUTATE, raised.exception.required_scope)
        self.assertFalse((self.repo_path / ".continue/model-routing.yaml").exists())

    def test_model_fallback_configure_allows_mutate_scope_in_http_context(self):
        self.server.ALLOW_MUTATIONS = True
        auth_token = self.server._HTTP_REQUEST_AUTHORIZED.set(True)
        scope_token = self.server._HTTP_REQUEST_GRANTED_SCOPES.set(
            frozenset({self.server.MCP_SCOPE_MUTATE})
        )
        try:
            response = asyncio.run(
                self.server.continue_model_fallback_configure(
                    FakeRequest(
                        {
                            "provider": "openai-compatible",
                            "model": "fallback-target",
                            "apiBase": "http://127.0.0.1:8787/v1",
                        }
                    )
                )
            )
        finally:
            self.server._HTTP_REQUEST_GRANTED_SCOPES.reset(scope_token)
            self.server._HTTP_REQUEST_AUTHORIZED.reset(auth_token)
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual("written", payload["status"])
        self.assertTrue((self.repo_path / ".continue/model-routing.yaml").exists())

    def test_model_fallback_configure_dry_run_when_mutations_disabled(self):
        self.server.ALLOW_MUTATIONS = False

        response = asyncio.run(
            self.server.continue_model_fallback_configure(
                FakeRequest(
                    {
                        "provider": "openai-compatible",
                        "model": "fallback-target",
                        "apiBase": "http://127.0.0.1:8787/v1",
                        "proxy": "http://127.0.0.1:8080",
                    }
                )
            )
        )
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 403)
        self.assertEqual("dry_run", payload["status"])
        self.assertIn(".continue/models/coding-openai-compatible.yaml", payload["files"])
        self.assertIn(".codebase-tooling-mcp/agent-proxy.local.toml", payload["files"])
        self.assertIn("[agent_proxy]", payload["files"][".codebase-tooling-mcp/agent-proxy.local.toml"])
        self.assertIn("Status: dry_run", payload["message"])
        self.assertIn("Secret state:", payload["message"])
        self.assertFalse((self.repo_path / ".continue/model-routing.yaml").exists())
        self.assertFalse((self.repo_path / ".codebase-tooling-mcp/agent-proxy.local.toml").exists())

    def test_model_fallback_configure_writes_continue_files_when_allowed(self):
        self.server.ALLOW_MUTATIONS = True

        response = asyncio.run(
            self.server.continue_model_fallback_configure(
                FakeRequest(
                    {
                        "provider": "openai-compatible",
                        "model": "fallback-target",
                        "apiBase": "http://127.0.0.1:8787/v1",
                        "proxy": "http://127.0.0.1:8080",
                        "caBundlePath": "/tmp/mitm-ca.pem",
                    }
                )
            )
        )
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual("written", payload["status"])
        profile_text = (self.repo_path / ".continue/models/coding-openai-compatible.yaml").read_text(
            encoding="utf-8"
        )
        routing_text = (self.repo_path / ".continue/model-routing.yaml").read_text(
            encoding="utf-8"
        )
        agent_proxy_text = (self.repo_path / ".codebase-tooling-mcp/agent-proxy.local.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn("provider: openai", profile_text)
        self.assertIn("model: fallback-target", profile_text)
        self.assertIn("apiBase: http://127.0.0.1:8787/v1", profile_text)
        self.assertIn("proxy: http://127.0.0.1:8080", profile_text)
        self.assertIn("caBundlePath: /tmp/mitm-ca.pem", profile_text)
        self.assertIn("model: fallback-target", routing_text)
        self.assertIn("[agent_proxy]", agent_proxy_text)
        self.assertIn('provider = "openai-compatible"', agent_proxy_text)
        self.assertIn('model = "fallback-target"', agent_proxy_text)
        self.assertIn('apiBase = "http://127.0.0.1:8787/v1"', agent_proxy_text)
        self.assertIn('apiKey = ""', agent_proxy_text)
        self.assertIn('allow_online = true', agent_proxy_text)
        self.assertIn("Status: written", payload["message"])
        self.assertIn("Secret state:", payload["message"])
        self.assertNotIn("unit-test-secret-value", agent_proxy_text)


    def test_model_fallback_configure_asks_for_api_key_when_required(self):
        self.server.ALLOW_MUTATIONS = True

        response = asyncio.run(
            self.server.continue_model_fallback_configure(
                FakeRequest(
                    {
                        "provider": "azure",
                        "model": "azure-deployment",
                        "apiBase": "https://example-resource.openai.azure.com",
                        "apiType": "azure",
                        "apiVersion": "2024-10-21",
                    }
                )
            )
        )
        payload = self.response_json(response)

        self.assertEqual(response.status_code, 400)
        self.assertEqual("needs_secret", payload["status"])
        self.assertIn("Status: needs-secret", payload["message"])
        self.assertIn("Secret state:", payload["message"])
        self.assertIn("Provide apiKey", payload["message"])
        self.assertFalse((self.repo_path / ".codebase-tooling-mcp/agent-proxy.local.toml").exists())

    def test_model_fallback_configure_stores_raw_api_key_as_continue_secret_only(self):
        self.server.ALLOW_MUTATIONS = True
        raw_key = "unit-test-secret-value"

        response = asyncio.run(
            self.server.continue_model_fallback_configure(
                FakeRequest(
                    {
                        "provider": "azure",
                        "model": "azure-deployment",
                        "apiBase": "https://example-resource.openai.azure.com",
                        "apiType": "azure",
                        "apiVersion": "2024-10-21",
                        "apiKeySecret": "AZURE_OPENAI_API_KEY",
                        "apiKey": raw_key,
                    }
                )
            )
        )
        payload = self.response_json(response)
        combined_output = json.dumps(payload, sort_keys=True)
        agent_proxy_text = (self.repo_path / ".codebase-tooling-mcp/agent-proxy.local.toml").read_text(
            encoding="utf-8"
        )
        profile_text = (self.repo_path / ".continue/models/coding-openai-compatible.yaml").read_text(
            encoding="utf-8"
        )
        secret_text = (self.repo_path / ".continue/.env").read_text(encoding="utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertEqual("written", payload["status"])
        self.assertIn('apiKey = "${{ secrets.AZURE_OPENAI_API_KEY }}"', agent_proxy_text)
        self.assertIn("apiKey: ${{ secrets.AZURE_OPENAI_API_KEY }}", profile_text)
        self.assertIn(f"AZURE_OPENAI_API_KEY={raw_key}", secret_text)
        self.assertTrue(payload["agent_proxy_config"]["secrets_persisted"])
        self.assertTrue(payload["agent_proxy_config"]["raw_secret_redacted"])
        self.assertIn("Secret state:", payload["message"])
        self.assertNotIn(raw_key, combined_output)
        self.assertNotIn(raw_key, agent_proxy_text)
        self.assertNotIn(raw_key, profile_text)

    def test_model_fallback_configure_dry_run_redacts_api_key_and_formats_output(self):
        self.server.ALLOW_MUTATIONS = False
        raw_key = "unit-test-secret-value"

        response = asyncio.run(
            self.server.continue_model_fallback_configure(
                FakeRequest(
                    {
                        "provider": "azure",
                        "model": "azure-deployment",
                        "apiBase": "https://example-resource.openai.azure.com",
                        "apiType": "azure",
                        "apiVersion": "2024-10-21",
                        "apiKeySecret": "AZURE_OPENAI_API_KEY",
                        "apiKey": raw_key,
                    }
                )
            )
        )
        payload = self.response_json(response)
        combined_output = json.dumps(payload, sort_keys=True)

        self.assertEqual(response.status_code, 403)
        self.assertEqual("dry_run", payload["status"])
        self.assertIn("Status: dry_run", payload["message"])
        self.assertIn("Config file: .codebase-tooling-mcp/agent-proxy.local.toml", payload["message"])
        self.assertIn("Secret state:", payload["message"])
        self.assertIn("[agent_proxy]", payload["files"][".codebase-tooling-mcp/agent-proxy.local.toml"])
        self.assertIn('${{ secrets.AZURE_OPENAI_API_KEY }}', payload["files"][".codebase-tooling-mcp/agent-proxy.local.toml"])
        self.assertNotIn(raw_key, combined_output)
        self.assertFalse((self.repo_path / ".continue/.env").exists())
