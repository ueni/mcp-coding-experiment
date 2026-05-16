# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import time
from pathlib import Path

from tests.server_test_support import ServerToolsTestBase


class OTelTracingTest(ServerToolsTestBase):
    def setUp(self):
        super().setUp()
        self._orig_otel_enabled = self.server.MCP_OTEL_TRACING_ENABLED
        self._orig_otel_exporter = self.server.MCP_OTEL_EXPORTER
        self._orig_otel_spans_file = self.server.MCP_OTEL_SPANS_FILE
        self._orig_otel_service_name = self.server.MCP_OTEL_SERVICE_NAME
        self.trace_file = Path(".codebase-tooling-mcp/traces/otel_spans.jsonl")
        self.server.MCP_OTEL_SPANS_FILE = self.trace_file
        self.server.MCP_OTEL_EXPORTER = "jsonl"
        self.server.MCP_OTEL_SERVICE_NAME = "codebase-tooling-mcp-test"

    def tearDown(self):
        self.server.MCP_OTEL_TRACING_ENABLED = self._orig_otel_enabled
        self.server.MCP_OTEL_EXPORTER = self._orig_otel_exporter
        self.server.MCP_OTEL_SPANS_FILE = self._orig_otel_spans_file
        self.server.MCP_OTEL_SERVICE_NAME = self._orig_otel_service_name
        super().tearDown()

    def _enable_tracing(self) -> None:
        self.server.MCP_OTEL_TRACING_ENABLED = True

    def _spans(self) -> list[dict]:
        path = self.repo_path / self.trace_file
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_otel_tracing_disabled_by_default_does_not_write_local_spans(self):
        self.server.MCP_OTEL_TRACING_ENABLED = False

        self.server.task_router(
            mode="workflow_select",
            prompt="Pick a release workflow before handoff",
        )

        self.assertFalse((self.repo_path / self.trace_file).exists())

    def test_otel_workflow_select_writes_redacted_local_json_spans(self):
        self._enable_tracing()

        self.server.task_router(
            mode="workflow_select",
            prompt="Audit /tmp/should-not-leak/raw.py without exposing Authorization: Bearer hunter2-secret-token",
            execution_mode="offline",
            top_k=2,
        )

        spans = self._spans()
        names = {span["name"] for span in spans}
        self.assertIn("mcp.tool.task_router", names)
        self.assertIn("mcp.workflow.select", names)

        tool_span = next(span for span in spans if span["name"] == "mcp.tool.task_router")
        workflow_span = next(span for span in spans if span["name"] == "mcp.workflow.select")
        self.assertEqual(tool_span["attributes"]["gen_ai.operation.name"], "execute_tool")
        self.assertEqual(tool_span["attributes"]["gen_ai.tool.name"], "task_router")
        self.assertEqual(workflow_span["parent_span_id"], tool_span["span_id"])
        self.assertEqual(workflow_span["attributes"]["mcp.execution_mode"], "offline")
        self.assertFalse(workflow_span["attributes"]["mcp.content_capture.enabled"])

        encoded = json.dumps(spans, sort_keys=True)
        self.assertNotIn("hunter2-secret-token", encoded)
        self.assertNotIn("/tmp/should-not-leak", encoded)
        self.assertNotIn(str(self.repo_path), encoded)

    def test_otel_policy_denial_span_is_redacted_and_correlates_audit_event(self):
        self._enable_tracing()
        self.server.ALLOW_MUTATIONS = False

        with self.assertRaises(PermissionError):
            self.server.apply_unified_diff(
                "diff --git a/tmp.py b/tmp.py\n+secret from /tmp/should-not-leak/file.py\n",
                check_only=False,
            )

        spans = self._spans()
        policy_span = next(span for span in spans if span["name"] == "mcp.policy_gate")
        self.assertEqual(policy_span["attributes"]["mcp.policy.decision"], "deny")
        self.assertEqual(policy_span["attributes"]["mcp.policy.reason"], "mutations disabled")
        self.assertEqual(policy_span["status"]["code"], "ERROR")

        tool_span = next(span for span in spans if span["name"] == "mcp.tool.apply_unified_diff")
        self.assertEqual(tool_span["status"]["code"], "ERROR")
        self.assertEqual(policy_span["parent_span_id"], tool_span["span_id"])

        encoded = json.dumps(spans, sort_keys=True)
        self.assertNotIn("/tmp/should-not-leak", encoded)
        self.assertNotIn("secret from", encoded)
        self.assertNotIn(str(self.repo_path), encoded)

        audit_path = self.repo_path / ".codebase-tooling-mcp/audit/security_events.jsonl"
        audit_events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(audit_events[-1]["correlation_id"], tool_span["correlation_id"])

    def test_otel_workflow_task_lifecycle_uses_task_id_correlation(self):
        self._enable_tracing()

        started = self.server.workflow_task(
            action="start",
            workflow="governance_report",
            task_id="otel-governance",
            base_ref="HEAD",
            head_ref="HEAD",
            export=False,
        )
        self.assertEqual(started["task_id"], "otel-governance")

        for _ in range(50):
            status = self.server.task_status("otel-governance")
            if status["state"] in {"succeeded", "failed", "expired"}:
                break
            time.sleep(0.02)
        else:
            self.fail("workflow task did not complete")

        spans = self._spans()
        lifecycle = [span for span in spans if span["name"] == "mcp.workflow_task.lifecycle"]
        self.assertTrue(lifecycle)
        self.assertTrue(any(span["attributes"]["mcp.workflow.event"] == "start" for span in lifecycle))
        self.assertTrue(any(span["attributes"]["mcp.workflow.event"] == "completed" for span in lifecycle))
        self.assertTrue(all(span["correlation_id"] == "otel-governance" for span in lifecycle))
        self.assertTrue(
            any(
                span["name"] == "mcp.tool.task_status"
                and span["correlation_id"] == "otel-governance"
                for span in spans
            )
        )

        encoded = json.dumps(spans, sort_keys=True)
        self.assertNotIn(str(self.repo_path), encoded)
