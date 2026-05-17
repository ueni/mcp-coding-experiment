# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class _Context:
    def __init__(self, session):
        self.session = session


class _SamplingSession:
    def __init__(self, *, supported=True, response_text="Safe summary.", deny=False):
        self.supported = supported
        self.response_text = response_text
        self.deny = deny
        self.calls = []

    def check_client_capability(self, _capability):
        return self.supported

    async def create_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.deny:
            raise PermissionError("user denied sampling")
        return SimpleNamespace(
            content=SimpleNamespace(text=self.response_text),
            model="fake-client-model",
            stopReason="endTurn",
            meta={"approval_status": "approved_by_fake_client"},
        )


class SamplingSafetyAdapterTest(ServerToolsTestBase):
    def setUp(self):
        super().setUp()
        self._orig_sampling_enabled = self.server.MCP_SAMPLING_ENABLED
        self._orig_sampling_allowed = self.server.MCP_SAMPLING_ALLOWED_USE_CASES_RAW
        self._orig_sampling_max_paths = self.server.MCP_SAMPLING_MAX_PATHS
        self._orig_sampling_max_bytes = self.server.MCP_SAMPLING_MAX_BYTES
        self._orig_sampling_max_context_tokens = self.server.MCP_SAMPLING_MAX_CONTEXT_TOKENS
        self._orig_sampling_max_output_tokens = self.server.MCP_SAMPLING_MAX_OUTPUT_TOKENS
        self.server.MCP_SAMPLING_ALLOWED_USE_CASES_RAW = "summary,classification,workflow_selection"
        self.server.MCP_SAMPLING_MAX_PATHS = 3
        self.server.MCP_SAMPLING_MAX_BYTES = 512
        self.server.MCP_SAMPLING_MAX_CONTEXT_TOKENS = 120
        self.server.MCP_SAMPLING_MAX_OUTPUT_TOKENS = 256

    def tearDown(self):
        self.server.MCP_SAMPLING_ENABLED = self._orig_sampling_enabled
        self.server.MCP_SAMPLING_ALLOWED_USE_CASES_RAW = self._orig_sampling_allowed
        self.server.MCP_SAMPLING_MAX_PATHS = self._orig_sampling_max_paths
        self.server.MCP_SAMPLING_MAX_BYTES = self._orig_sampling_max_bytes
        self.server.MCP_SAMPLING_MAX_CONTEXT_TOKENS = self._orig_sampling_max_context_tokens
        self.server.MCP_SAMPLING_MAX_OUTPUT_TOKENS = self._orig_sampling_max_output_tokens
        super().tearDown()

    def _run_with_session(self, session, **kwargs):
        with patch.object(self.server.mcp, "get_context", return_value=_Context(session)):
            return asyncio.run(self.server.model_assisted_summary(**kwargs))

    def _prompt_from_call(self, session):
        message = session.calls[-1]["messages"][0]
        content = getattr(message, "content", None)
        if isinstance(message, dict):
            content = message["content"]
        if isinstance(content, dict):
            return content["text"]
        return getattr(content, "text")

    def test_sampling_disabled_by_default_does_not_call_client(self):
        self.server.MCP_SAMPLING_ENABLED = False
        session = _SamplingSession(supported=True)

        out = self._run_with_session(session, paths=["README.md"])

        self.assertEqual(out["schema"], "model_assisted_summary.v1")
        self.assertEqual(out["status"], "disabled")
        self.assertFalse(out["ok"])
        self.assertFalse(out["policy"]["enabled"])
        self.assertFalse(out["request"]["would_call_client"])
        self.assertEqual(session.calls, [])

    def test_sampling_enabled_but_client_capability_absent_is_unsupported(self):
        self.server.MCP_SAMPLING_ENABLED = True
        session = _SamplingSession(supported=False)

        out = self._run_with_session(session, paths=["README.md"])

        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(out["capability"]["status"], "unsupported")
        self.assertIn("capability", out["capability"]["reason"])
        self.assertEqual(session.calls, [])

    def test_sampling_denial_is_audited_without_raw_prompt(self):
        self.server.MCP_SAMPLING_ENABLED = True
        session = _SamplingSession(supported=True, deny=True)

        out = self._run_with_session(session, paths=["README.md"], question="summarize")

        self.assertEqual(out["status"], "denied")
        self.assertEqual(out["audit"]["approval_status"], "denied")
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(out["audit"]["records_raw_prompt"])
        self.assertFalse(out["audit"]["records_repository_content"])
        self.assertIn("value", out["audit"]["prompt_digest"])

    def test_approved_summary_redacts_context_response_and_enforces_budgets(self):
        self.server.MCP_SAMPLING_ENABLED = True
        self.write_repo_text(
            "docs/sampling-fixture.md",
            "# Sampling fixture\n"
            "token = super-secret-value\n"
            "Authorization: Bearer top-secret-token\n"
            "Host path /tmp/private/workspace/file.py must not leak.\n"
            "Useful public context.\n",
        )
        session = _SamplingSession(
            supported=True,
            response_text="Summary references /tmp/private and token: response-secret-value",
        )

        out = self._run_with_session(
            session,
            paths=["docs/sampling-fixture.md"],
            question="Summarize without leaking token: question-secret-value",
            max_bytes=180,
            max_context_tokens=30,
            max_tokens=1000,
        )

        self.assertEqual(out["status"], "approved")
        self.assertTrue(out["ok"])
        self.assertEqual(len(session.calls), 1)
        self.assertLessEqual(session.calls[0]["max_tokens"], self.server.MCP_SAMPLING_MAX_OUTPUT_TOKENS)
        prompt = self._prompt_from_call(session)
        encoded_prompt = prompt.lower()
        self.assertNotIn("super-secret-value", encoded_prompt)
        self.assertNotIn("top-secret-token", encoded_prompt)
        self.assertNotIn("question-secret-value", encoded_prompt)
        self.assertNotIn("/tmp/private", encoded_prompt)
        self.assertIn("<redacted:", encoded_prompt)
        self.assertNotIn(str(self.repo_path), prompt)

        self.assertEqual(out["context"]["sources"][0]["path"], "docs/sampling-fixture.md")
        self.assertLessEqual(out["context"]["included_bytes"], 180)
        self.assertIn("secret_value", out["context"]["redactions_applied"])
        self.assertIn("absolute_path", out["context"]["redactions_applied"])
        self.assertIn("value", out["request"]["prompt_digest"])
        self.assertEqual(out["audit"]["approval_status"], "approved_by_fake_client")
        self.assertFalse(out["audit"]["records_raw_prompt"])
        self.assertFalse(out["audit"]["records_raw_response"])
        self.assertNotIn("response-secret-value", out["sampling"]["summary"])
        self.assertNotIn("/tmp/private", out["sampling"]["summary"])
        self.assertIn("value", out["sampling"]["output_digest"])

    def test_path_budget_or_secret_path_denies_before_client_call(self):
        self.server.MCP_SAMPLING_ENABLED = True
        session = _SamplingSession(supported=True)

        too_many = self._run_with_session(
            session,
            paths=["README.md", "AGENTS.md"],
            max_paths=1,
        )
        self.assertEqual(too_many["status"], "denied")
        self.assertEqual(too_many["reason"], "path_budget_exceeded")
        self.assertEqual(session.calls, [])

        self.write_repo_text(".env", "API_KEY=super-secret-value\n")
        secret_path = self._run_with_session(session, paths=[".env"])
        self.assertEqual(secret_path["status"], "denied")
        self.assertEqual(secret_path["reason"], "no_allowed_repository_context")
        self.assertEqual(session.calls, [])
        self.assertEqual(secret_path["context"]["omitted"][0]["reason"], "secret_bearing_path_denied")
