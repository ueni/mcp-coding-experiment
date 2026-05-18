# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class WorkflowTaskTests(ServerToolsTestBase):
    def _wait_for_task_status(self, task_id: str, terminal: set[str] | None = None):
        terminal = terminal or {"succeeded", "failed", "expired", "cancelled"}
        deadline = time.time() + 5
        final = self.server.task_status(task_id)
        while time.time() < deadline:
            final = self.server.task_status(task_id)
            if final["status"] in terminal:
                return final
            time.sleep(0.05)
        self.fail(f"workflow task did not reach terminal state: {task_id}")

    def test_workflow_task_starts_governance_report_and_persists_redacted_status(self):
        started = self.server.workflow_task(
            workflow="governance_report",
            base_ref="HEAD",
            head_ref="HEAD",
            export=True,
        )

        self.assertEqual(started["schema"], "workflow_task.v1")
        self.assertRegex(started["task_id"], r"^task-[0-9a-f]{32}$")
        self.assertIn(started["status"], {"pending", "running", "succeeded"})
        self.assertIn("resource_links", started)
        self.assertEqual(started["resource_links"][0]["schema"], "artifact_resource_link.v1")

        deadline = time.time() + 5
        final = started
        while time.time() < deadline:
            final = self.server.task_status(started["task_id"])
            if final["status"] in {"succeeded", "failed", "expired"}:
                break
            time.sleep(0.05)

        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["result"]["schema"], "governance_report.v1")
        self.assertIn("report_id", final["result"])
        self.assertGreaterEqual(final["progress"], 1.0)
        self.assertTrue(final["security"]["redacted"])
        self.assertFalse(final["security"]["contains_secrets"])
        self.assertTrue(final["artifact_references"])

        status_path = self.repo_path / ".codebase-tooling-mcp" / "tasks" / f"{started['task_id']}.json"
        self.assertTrue(status_path.exists())
        persisted = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["task_id"], started["task_id"])
        self.assertNotIn(str(self.repo_path), status_path.read_text(encoding="utf-8"))

    def test_workflow_task_supports_retry_reference(self):
        first = self.server.workflow_task(
            workflow="governance_report",
            base_ref="HEAD",
            head_ref="HEAD",
            export=False,
        )
        retry = self.server.workflow_task(
            workflow="governance_report",
            base_ref="HEAD",
            head_ref="HEAD",
            export=False,
            retry_of=first["task_id"],
        )

        self.assertNotEqual(first["task_id"], retry["task_id"])
        self.assertEqual(retry["retry_of"], first["task_id"])
        self.assertIn("retry", [event["event"] for event in retry["audit_events"]])

    def test_prune_removes_retained_status_but_preserves_result_artifacts(self):
        created_at = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        task_id = "retained-old"
        self.server._write_workflow_task_status(
            {
                "schema": "workflow_task.v1",
                "task_id": task_id,
                "workflow": "vscode_task_run",
                "status": "succeeded",
                "state": "succeeded",
                "ok": True,
                "created_at": created_at,
                "started_at": created_at,
                "finished_at": created_at,
                "updated_at": created_at,
                "expires_at": (datetime.now(timezone.utc) - timedelta(days=9)).isoformat(),
                "retention_expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                "retry_of": "",
                "progress": 1.0,
                "progress_detail": {"phase": "complete", "percent": 100},
                "arguments": {},
                "artifact_references": [],
                "audit_events": [],
                "security": {"redacted": True, "contains_secrets": False, "repo_boundary_enforced": True},
            }
        )
        artifact_payload = self.server._write_workflow_task_result_artifact(
            task_id,
            {"schema": "vscode_task_run.v1", "ok": True, "stdout": "kept", "stderr": ""},
        )
        status_path = self.repo_path / ".codebase-tooling-mcp" / "tasks" / f"{task_id}.json"
        artifact_path = self.repo_path / ".codebase-tooling-mcp" / "tasks" / "artifacts" / f"{task_id}-vscode-task-result.json"
        self.assertTrue(status_path.exists())
        self.assertEqual(artifact_payload["task_id"], task_id)

        self.server._prune_workflow_task_statuses()

        self.assertFalse(status_path.exists())
        self.assertTrue(artifact_path.exists())

    def test_task_status_marks_non_final_expired_tasks(self):
        created_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        task_id = "task-" + "a" * 32
        self.server._write_workflow_task_status(
            {
                "schema": "workflow_task.v1",
                "task_id": task_id,
                "workflow": "governance_report",
                "status": "running",
                "state": "running",
                "created_at": created_at,
                "started_at": created_at,
                "finished_at": "",
                "updated_at": created_at,
                "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                "retention_expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                "retry_of": "",
                "progress": 0.5,
                "progress_detail": {"phase": "running", "percent": 50},
                "arguments": {},
                "artifact_references": [],
                "audit_events": [],
                "security": {"redacted": True, "contains_secrets": False, "repo_boundary_enforced": True},
            }
        )

        out = self.server.task_status(task_id)
        self.assertEqual(out["status"], "expired")
        self.assertEqual(out["progress_detail"]["phase"], "expired")
        self.assertIn("expired", [event["event"] for event in out["audit_events"]])

    def test_workflow_task_progress_notifications_are_rate_limited_and_monotonic(self):
        class FakeSession:
            def __init__(self):
                self.notifications = []

            async def send_progress_notification(
                self,
                progress_token,
                progress,
                total=None,
                message=None,
                related_request_id=None,
            ):
                self.notifications.append(
                    {
                        "progress_token": progress_token,
                        "progress": progress,
                        "total": total,
                        "message": message,
                        "related_request_id": related_request_id,
                    }
                )

        class Meta:
            progressToken = "progress-token-1"

        fake_session = FakeSession()
        request_context = type(
            "RequestContext",
            (),
            {"meta": Meta(), "session": fake_session, "request_id": "request-1"},
        )()
        context = type(
            "Context",
            (),
            {"request_context": request_context, "session": fake_session, "request_id": "request-1"},
        )()
        task_id = "task-progress"

        try:
            with patch.object(self.server.mcp, "get_context", return_value=context):
                self.server._workflow_task_register_protocol_bridge(task_id)

            self.server._workflow_task_emit_progress(
                task_id,
                {"status": "running", "progress": 0.25, "progress_detail": {"phase": "running", "percent": 25}},
                force=True,
            )
            self.server._workflow_task_emit_progress(
                task_id,
                {"status": "running", "progress": 0.30, "progress_detail": {"phase": "running", "percent": 30}},
            )
            self.server._workflow_task_emit_progress(
                task_id,
                {"status": "running", "progress": 0.20, "progress_detail": {"phase": "running", "percent": 20}},
                force=True,
            )
            self.server._workflow_task_emit_progress(
                task_id,
                {"status": "succeeded", "progress": 1.0, "progress_detail": {"phase": "complete", "percent": 100}},
                force=True,
            )
        finally:
            self.server._workflow_task_cleanup_protocol_bridge(task_id)

        self.assertEqual([item["progress"] for item in fake_session.notifications], [25.0, 25.0, 100.0])
        self.assertEqual(fake_session.notifications[0]["progress_token"], "progress-token-1")
        self.assertEqual(fake_session.notifications[0]["related_request_id"], "request-1")
        self.assertNotIn(task_id, self.server._WORKFLOW_TASK_PROGRESS_BRIDGES)

    def test_workflow_task_cancel_action_marks_running_task_cancelled_and_pollable(self):
        def slow_vscode_task_run(**_kwargs):
            time.sleep(0.2)
            return {
                "schema": "vscode_task_run.v1",
                "ok": True,
                "timeout": False,
                "stdout": "finished after cancel",
                "stderr": "",
            }

        with patch.object(self.server, "vscode_task_run", side_effect=slow_vscode_task_run):
            started = self.server.workflow_task(
                label="Docker: build",
                max_retries=0,
                task_id="unit-cancel",
                restart=True,
            )
            self.assertEqual(started["task_id"], "unit-cancel")
            cancelled = self.server.workflow_task(
                action="cancel",
                task_id="unit-cancel",
                cancel_reason="stop after user token=secret",
            )
            self.assertIn(cancelled["status"], {"cancel_requested", "cancelled"})
            final = self._wait_for_task_status("unit-cancel", terminal={"cancelled"})

        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(final["state"], "cancelled")
        self.assertFalse(final["ok"])
        self.assertEqual(final["progress_detail"], {"phase": "cancelled", "percent": 100})
        self.assertTrue(final["cancellation"]["requested"])
        self.assertTrue(final["cancellation"]["reason_redacted"])
        self.assertEqual(self.server.task_status("unit-cancel")["status"], "cancelled")

    def test_cancelled_notification_request_id_maps_to_workflow_task_status(self):
        now = self.server._now_iso()
        task_id = "task-protocol-cancel"
        self.server._write_workflow_task_status(
            {
                "schema": "workflow_task.v1",
                "task_id": task_id,
                "workflow": "governance_report",
                "status": "pending",
                "state": "pending",
                "started": True,
                "ok": False,
                "attempt": 0,
                "max_retries": 0,
                "retries": [],
                "created_at": now,
                "started_at": "",
                "finished_at": "",
                "updated_at": now,
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "retention_expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                "retry_of": "",
                "cancel_requested": False,
                "cancel_requested_at": "",
                "cancelled_at": "",
                "cancellation": {"requested": False, "reason_redacted": True, "best_effort": True},
                "progress": 0.0,
                "progress_detail": {"phase": "queued", "percent": 0},
                "arguments": {},
                "artifact_references": [],
                "audit_events": [{"event": "start", "at": now}],
                "security": {"redacted": True, "contains_secrets": False, "repo_boundary_enforced": True},
            }
        )
        self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID["request-cancel-1"] = task_id

        try:
            notification = self.server.mcp_types.CancelledNotification(
                params=self.server.mcp_types.CancelledNotificationParams(
                    requestId="request-cancel-1",
                    reason="client cancelled token=secret",
                )
            )
            asyncio.run(self.server._handle_workflow_task_cancelled_notification(notification))
            out = self.server.task_status(task_id)
        finally:
            self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID.pop("request-cancel-1", None)
            self.server._workflow_task_cleanup_runtime(task_id)

        self.assertEqual(out["status"], "cancelled")
        self.assertTrue(out["cancellation"]["requested"])
        self.assertEqual(out["cancellation"]["source"], "notifications/cancelled")
        self.assertTrue(any(event["event"] == "cancelled" for event in out["audit_events"]))
