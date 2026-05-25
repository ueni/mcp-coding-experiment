# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class OTelTracingTest(ServerToolsTestBase):
    def setUp(self):
        super().setUp()
        self._orig_otel_enabled = self.server.MCP_OTEL_TRACING_ENABLED
        self._orig_otel_exporter = self.server.MCP_OTEL_EXPORTER
        self._orig_otel_spans_file = self.server.MCP_OTEL_SPANS_FILE
        self._orig_otel_service_name = self.server.MCP_OTEL_SERVICE_NAME
        self._orig_otel_baggage_allowlist = self.server.MCP_OTEL_BAGGAGE_ALLOWLIST_RAW
        self.trace_file = Path(".codebase-tooling-mcp/traces/otel_spans.jsonl")
        self.server.MCP_OTEL_SPANS_FILE = self.trace_file
        self.server.MCP_OTEL_EXPORTER = "jsonl"
        self.server.MCP_OTEL_SERVICE_NAME = "codebase-tooling-mcp-test"

    def tearDown(self):
        self.server.MCP_OTEL_TRACING_ENABLED = self._orig_otel_enabled
        self.server.MCP_OTEL_EXPORTER = self._orig_otel_exporter
        self.server.MCP_OTEL_SPANS_FILE = self._orig_otel_spans_file
        self.server.MCP_OTEL_SERVICE_NAME = self._orig_otel_service_name
        self.server.MCP_OTEL_BAGGAGE_ALLOWLIST_RAW = self._orig_otel_baggage_allowlist
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

    def _assert_valid_trace_context_span(
        self, *, trace_id: str, parent_span_id: str, source: str
    ) -> None:
        spans = self._spans()
        tool_span = next(span for span in spans if span["name"] == "mcp.tool.task_router")
        workflow_span = next(span for span in spans if span["name"] == "mcp.workflow.select")
        self.assertEqual(tool_span["trace_id"], trace_id)
        self.assertEqual(tool_span["parent_span_id"], parent_span_id)
        self.assertEqual(tool_span["correlation_id"], trace_id)
        self.assertEqual(workflow_span["trace_id"], trace_id)
        self.assertEqual(workflow_span["parent_span_id"], tool_span["span_id"])
        self.assertTrue(tool_span["attributes"]["mcp.trace_context.valid"])
        self.assertEqual(tool_span["attributes"]["mcp.trace_context.source"], source)
        self.assertEqual(tool_span["attributes"]["mcp.trace_context.trace_flags"], "01")
        self.assertEqual(tool_span["attributes"]["mcp.trace_context.tracestate.member_count"], 2)
        self.assertEqual(tool_span["attributes"]["mcp.trace_context.baggage.allowed_count"], 1)
        self.assertEqual(tool_span["attributes"]["mcp.trace_context.baggage.tenant"], "acme")
        self.assertEqual(tool_span["attributes"]["mcp.trace_context.baggage.dropped_count"], 2)

        encoded = json.dumps(spans, sort_keys=True)
        self.assertNotIn("hunter2-secret-token", encoded)
        self.assertNotIn("/tmp/should-not-leak", encoded)
        self.assertNotIn(str(self.repo_path), encoded)

    def test_otel_valid_trace_context_propagates_to_redacted_spans(self):
        self._enable_tracing()
        self.server.MCP_OTEL_BAGGAGE_ALLOWLIST_RAW = "tenant"
        trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
        parent_span_id = "00f067aa0ba902b7"
        context = self.server._otel_trace_context_from_carrier(
            {
                "traceparent": f"00-{trace_id}-{parent_span_id}-01",
                "tracestate": "rojo=00f067aa0ba902b7,congo=t61rcWkgMzE",
                "baggage": "tenant=acme,authorization=hunter2-secret-token,path=%2Ftmp%2Fshould-not-leak",
            },
            "mcp_meta",
        )
        token = self.server._OTEL_INCOMING_TRACE_CONTEXT.set(context)
        try:
            self.server.task_router(
                mode="workflow_select",
                prompt="Pick a safe workflow without capturing content",
                execution_mode="offline",
            )
        finally:
            self.server._OTEL_INCOMING_TRACE_CONTEXT.reset(token)

        self._assert_valid_trace_context_span(
            trace_id=trace_id, parent_span_id=parent_span_id, source="mcp_meta"
        )

    def test_otel_mcp_meta_request_context_propagates_trace_context(self):
        self._enable_tracing()
        self.server.MCP_OTEL_BAGGAGE_ALLOWLIST_RAW = "tenant"
        trace_id = "11111111111111111111111111111111"
        parent_span_id = "2222222222222222"
        request_context = SimpleNamespace(
            meta={
                "_meta": {
                    "traceparent": f"00-{trace_id}-{parent_span_id}-01",
                    "tracestate": "rojo=00f067aa0ba902b7,congo=t61rcWkgMzE",
                    "baggage": "tenant=acme,authorization=hunter2-secret-token,path=%2Ftmp%2Fshould-not-leak",
                }
            }
        )

        with patch.object(
            self.server.mcp,
            "get_context",
            return_value=SimpleNamespace(request_context=request_context),
        ):
            self.server.task_router(
                mode="workflow_select",
                prompt="Pick a safe workflow without capturing content",
                execution_mode="offline",
            )

        self._assert_valid_trace_context_span(
            trace_id=trace_id, parent_span_id=parent_span_id, source="mcp_meta"
        )

    def test_otel_http_header_carrier_propagates_trace_context(self):
        self._enable_tracing()
        self.server.MCP_OTEL_BAGGAGE_ALLOWLIST_RAW = "tenant"
        trace_id = "33333333333333333333333333333333"
        parent_span_id = "4444444444444444"
        carrier = self.server._otel_header_carrier(
            {
                "headers": [
                    (b"traceparent", f"00-{trace_id}-{parent_span_id}-01".encode("latin-1")),
                    (b"tracestate", b"rojo=00f067aa0ba902b7,congo=t61rcWkgMzE"),
                    (
                        b"baggage",
                        b"tenant=acme,authorization=hunter2-secret-token,path=%2Ftmp%2Fshould-not-leak",
                    ),
                ]
            }
        )
        context = self.server._otel_trace_context_from_carrier(carrier, "http_headers")
        token = self.server._OTEL_INCOMING_TRACE_CONTEXT.set(context)
        try:
            self.server.task_router(
                mode="workflow_select",
                prompt="Pick a safe workflow without capturing content",
                execution_mode="offline",
            )
        finally:
            self.server._OTEL_INCOMING_TRACE_CONTEXT.reset(token)

        self._assert_valid_trace_context_span(
            trace_id=trace_id, parent_span_id=parent_span_id, source="http_headers"
        )

    def test_otel_invalid_trace_context_is_dropped_but_counted(self):
        self._enable_tracing()
        context = self.server._otel_trace_context_from_carrier(
            {
                "traceparent": "00-00000000000000000000000000000000-0000000000000000-01",
                "baggage": "secret=hunter2-secret-token",
            },
            "http_headers",
        )
        token = self.server._OTEL_INCOMING_TRACE_CONTEXT.set(context)
        try:
            self.server.task_router(
                mode="workflow_select",
                prompt="Pick a safe workflow without capturing content",
            )
        finally:
            self.server._OTEL_INCOMING_TRACE_CONTEXT.reset(token)

        tool_span = next(span for span in self._spans() if span["name"] == "mcp.tool.task_router")
        self.assertNotEqual(tool_span["trace_id"], "00000000000000000000000000000000")
        self.assertEqual(tool_span["parent_span_id"], "")
        self.assertFalse(tool_span["attributes"]["mcp.trace_context.valid"])
        self.assertEqual(tool_span["attributes"]["mcp.trace_context.source"], "http_headers")
        self.assertEqual(tool_span["attributes"]["mcp.trace_context.invalid_count"], 1)
        self.assertEqual(tool_span["attributes"]["mcp.trace_context.baggage.dropped_count"], 1)
        self.assertNotIn("hunter2-secret-token", json.dumps(tool_span, sort_keys=True))

    def test_otel_trace_correlation_reaches_governance_summaries(self):
        self._enable_tracing()
        trace_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        context = self.server._otel_trace_context_from_carrier(
            {"traceparent": f"00-{trace_id}-bbbbbbbbbbbbbbbb-01"},
            "http_headers",
        )
        token = self.server._OTEL_INCOMING_TRACE_CONTEXT.set(context)
        try:
            diagnostics = self.server.workflow_diagnostics()
            report = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=False)
        finally:
            self.server._OTEL_INCOMING_TRACE_CONTEXT.reset(token)

        self.assertEqual(diagnostics["correlation_id"], trace_id)
        self.assertEqual(report["correlation_id"], trace_id)
        self.assertEqual(report["workflow_diagnostics"]["correlation_id"], trace_id)

    def test_otel_unsupported_exporter_is_offline_safe_noop(self):
        self.server.MCP_OTEL_TRACING_ENABLED = True
        self.server.MCP_OTEL_EXPORTER = "otlp"

        self.server.task_router(
            mode="workflow_select",
            prompt="Pick a release workflow before handoff",
        )

        self.assertFalse((self.repo_path / self.trace_file).exists())

    def test_otel_governance_and_artifact_spans_use_repo_relative_refs(self):
        self._enable_tracing()

        report = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=True)
        provenance = self.server.artifact_provenance(include_reports=True, include_snapshots=False)

        self.assertEqual(report["schema"], "governance_report.v1")
        self.assertGreaterEqual(provenance["artifact_count"], 2)

        spans = self._spans()
        governance_span = next(span for span in spans if span["name"] == "mcp.tool.governance_report")
        provenance_span = next(span for span in spans if span["name"] == "mcp.tool.artifact_provenance")
        self.assertEqual(governance_span["attributes"]["gen_ai.tool.name"], "governance_report")
        self.assertEqual(provenance_span["attributes"]["gen_ai.tool.name"], "artifact_provenance")
        refs = governance_span["attributes"].get("mcp.artifact.refs", [])
        self.assertTrue(any(ref.startswith(".codebase-tooling-mcp/reports/") for ref in refs))

        encoded = json.dumps(spans, sort_keys=True)
        self.assertNotIn(str(self.repo_path), encoded)
        self.assertNotIn("Authorization: Bearer", encoded)

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
