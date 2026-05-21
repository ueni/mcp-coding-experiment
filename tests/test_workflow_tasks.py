# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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

    def _write_workflow_task_fixture(self, task_id: str, **overrides):
        now = self.server._now_iso()
        status = str(overrides.pop("status", "pending"))
        workflow = str(overrides.pop("workflow", "vscode_task_run"))
        payload = {
            "schema": "workflow_task.v1",
            "task_id": task_id,
            "workflow": workflow,
            "status": status,
            "state": overrides.pop("state", status),
            "started": True,
            "ok": overrides.pop("ok", False),
            "attempt": overrides.pop("attempt", 0),
            "max_retries": overrides.pop("max_retries", 0),
            "retries": overrides.pop("retries", []),
            "created_at": now,
            "started_at": overrides.pop("started_at", now if status != "pending" else ""),
            "finished_at": overrides.pop(
                "finished_at",
                now if status in self.server._WORKFLOW_TASK_FINAL_STATUSES else "",
            ),
            "updated_at": now,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "retention_expires_at": (
                datetime.now(timezone.utc) + timedelta(days=7)
            ).isoformat(),
            "retry_of": "",
            "cancel_requested": False,
            "cancel_requested_at": "",
            "cancelled_at": "",
            "cancellation": {
                "requested": False,
                "requested_at": "",
                "cancelled_at": "",
                "reason": "",
                "reason_redacted": True,
                "best_effort": True,
            },
            "progress": overrides.pop("progress", 0.0),
            "progress_detail": overrides.pop(
                "progress_detail", {"phase": "queued", "percent": 0}
            ),
            "arguments": overrides.pop("arguments", {}),
            "artifact_references": overrides.pop("artifact_references", []),
            "audit_events": overrides.pop("audit_events", [{"event": "start", "at": now}]),
            "security": {
                "redacted": True,
                "contains_secrets": False,
                "repo_boundary_enforced": True,
            },
        }
        payload.update(overrides)
        return self.server._write_workflow_task_status(payload)

    def _assert_persisted_cancellation_metadata_excludes(
        self, task_id: str, *values: str
    ):
        status_path = (
            self.repo_path / ".codebase-tooling-mcp" / "tasks" / f"{task_id}.json"
        )
        persisted = json.loads(status_path.read_text(encoding="utf-8"))
        metadata = json.dumps(
            {
                "audit_events": persisted.get("audit_events", []),
                "cancellation": persisted.get("cancellation", {}),
            },
            sort_keys=True,
        )
        for value in values:
            self.assertNotIn(value, metadata)

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

    def test_workflow_task_start_progress_token_emits_notifications_end_to_end(self):
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
            progressToken = "progress-token-start"

        def quick_vscode_task_run(**_kwargs):
            return {
                "schema": "vscode_task_run.v1",
                "ok": True,
                "timeout": False,
                "stdout": "done",
                "stderr": "",
            }

        fake_session = FakeSession()
        request_context = type(
            "RequestContext",
            (),
            {"meta": Meta(), "session": fake_session, "request_id": "request-start-1"},
        )()
        context = type(
            "Context",
            (),
            {
                "request_context": request_context,
                "session": fake_session,
                "request_id": "request-start-1",
            },
        )()
        task_id = "task-progress-start"

        try:
            with (
                patch.object(self.server.mcp, "get_context", return_value=context),
                patch.object(self.server, "vscode_task_run", side_effect=quick_vscode_task_run),
            ):
                started = self.server.workflow_task(
                    action="start",
                    workflow="vscode_task_run",
                    label="Docker: build",
                    max_retries=0,
                    task_id=task_id,
                    restart=True,
                )
                final = self._wait_for_task_status(task_id)
        finally:
            self.server._workflow_task_cleanup_protocol_bridge(task_id)
            self.server._workflow_task_cleanup_runtime(task_id)

        self.assertEqual(started["task_id"], task_id)
        self.assertEqual(final["status"], "succeeded")
        self.assertTrue(fake_session.notifications)
        self.assertEqual(
            {item["progress_token"] for item in fake_session.notifications},
            {"progress-token-start"},
        )
        self.assertEqual(fake_session.notifications[-1]["progress"], 100.0)
        self.assertTrue(
            any(
                item["related_request_id"] == "request-start-1"
                for item in fake_session.notifications
            )
        )

    def test_streamable_http_event_store_replays_last_event_id_for_same_workflow_task_stream(self):
        store = self.server._BoundedWorkflowTaskEventStore(max_events=10, retention_seconds=3600)
        msg_a1 = SimpleNamespace(root=SimpleNamespace(method="notifications/progress", params={"progress": 10}))
        msg_a2 = SimpleNamespace(root=SimpleNamespace(method="notifications/progress", params={"progress": 30}))
        msg_b = SimpleNamespace(root=SimpleNamespace(method="notifications/progress", params={"progress": 20}))

        async def _exercise():
            token = self.server._STREAMABLE_HTTP_SESSION_ID.set("session-a")
            try:
                priming_event = await store.store_event("stream-a", None)
                with self.server._WORKFLOW_TASK_LOCK:
                    self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID["stream-a"] = "task-a"
                    self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID["stream-b"] = "task-b"
                    self.server._WORKFLOW_TASK_REQUEST_TO_SESSION_ID["stream-a"] = "session-a"
                    self.server._WORKFLOW_TASK_REQUEST_TO_SESSION_ID["stream-b"] = "session-a"
                    self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID_AT["stream-a"] = time.monotonic()
                    self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID_AT["stream-b"] = time.monotonic()
                event_a1 = await store.store_event("stream-a", msg_a1)
                await store.store_event("stream-b", msg_b)
                event_a2 = await store.store_event("stream-a", msg_a2)

                replayed = []
                replayed_after_priming = []

                async def send(event_message):
                    replayed.append(event_message)

                async def send_after_priming(event_message):
                    replayed_after_priming.append(event_message)

                stream_id = await store.replay_events_after(event_a1, send)
                priming_stream_id = await store.replay_events_after(priming_event, send_after_priming)
                return stream_id, event_a1, event_a2, replayed, priming_stream_id, replayed_after_priming
            finally:
                self.server._STREAMABLE_HTTP_SESSION_ID.reset(token)

        try:
            stream_id, event_a1, event_a2, replayed, priming_stream_id, replayed_after_priming = asyncio.run(_exercise())
        finally:
            with self.server._WORKFLOW_TASK_LOCK:
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID.pop("stream-a", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID.pop("stream-b", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_SESSION_ID.pop("stream-a", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_SESSION_ID.pop("stream-b", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID_AT.pop("stream-a", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID_AT.pop("stream-b", None)

        self.assertEqual(stream_id, "stream-a")
        self.assertEqual([item.event_id for item in replayed], [event_a2])
        self.assertIs(replayed[0].message, msg_a2)
        self.assertEqual(priming_stream_id, "stream-a")
        self.assertEqual([item.event_id for item in replayed_after_priming], [event_a1, event_a2])

    def test_streamable_http_event_store_blocks_cross_session_or_cross_stream_replay(self):
        store = self.server._BoundedWorkflowTaskEventStore(max_events=10, retention_seconds=3600)
        msg_a1 = SimpleNamespace(root=SimpleNamespace(method="notifications/progress", params={"progress": 10}))
        msg_a2 = SimpleNamespace(root=SimpleNamespace(method="notifications/progress", params={"progress": 30}))
        msg_b = SimpleNamespace(root=SimpleNamespace(method="notifications/progress", params={"progress": 20}))
        msg_other_session = SimpleNamespace(
            root=SimpleNamespace(method="notifications/progress", params={"progress": 40})
        )

        async def _exercise():
            token = self.server._STREAMABLE_HTTP_SESSION_ID.set("session-a")
            with self.server._WORKFLOW_TASK_LOCK:
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID["stream-a"] = "task-a"
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID["stream-b"] = "task-b"
                self.server._WORKFLOW_TASK_REQUEST_TO_SESSION_ID["stream-a"] = "session-a"
                self.server._WORKFLOW_TASK_REQUEST_TO_SESSION_ID["stream-b"] = "session-a"
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID_AT["stream-a"] = time.monotonic()
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID_AT["stream-b"] = time.monotonic()
            try:
                event_a1 = await store.store_event("stream-a", msg_a1)
                event_b = await store.store_event("stream-b", msg_b)
                await store.store_event("stream-a", msg_a2)
                self.server._STREAMABLE_HTTP_SESSION_ID.reset(token)
                token = self.server._STREAMABLE_HTTP_SESSION_ID.set("session-b")
                replay_from_other_session_same_mapping = []

                async def send_other_session_same_mapping(event_message):
                    replay_from_other_session_same_mapping.append(event_message)

                other_session_same_mapping = await store.replay_events_after(
                    event_a1,
                    send_other_session_same_mapping,
                )
                with self.server._WORKFLOW_TASK_LOCK:
                    # Simulate the same request-stream identifier being reused by a
                    # different MCP session/task before a stale Last-Event-ID is replayed.
                    self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID["stream-a"] = "task-c"
                    self.server._WORKFLOW_TASK_REQUEST_TO_SESSION_ID["stream-a"] = "session-b"
                    self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID_AT["stream-a"] = time.monotonic()
                event_c = await store.store_event("stream-a", msg_other_session)

                replay_from_stale_session = []
                replay_from_cross_stream = []
                replay_from_current_session = []

                async def send_stale(event_message):
                    replay_from_stale_session.append(event_message)

                async def send_cross_stream(event_message):
                    replay_from_cross_stream.append(event_message)

                async def send_current(event_message):
                    replay_from_current_session.append(event_message)

                stale_stream = await store.replay_events_after(event_a1, send_stale)
                cross_stream = await store.replay_events_after(event_b, send_cross_stream)
                current_stream = await store.replay_events_after(event_c, send_current)
                return (
                    other_session_same_mapping,
                    stale_stream,
                    cross_stream,
                    current_stream,
                    replay_from_other_session_same_mapping,
                    replay_from_stale_session,
                    replay_from_cross_stream,
                    replay_from_current_session,
                )
            finally:
                self.server._STREAMABLE_HTTP_SESSION_ID.reset(token)

        try:
            (
                other_session_same_mapping,
                stale_stream,
                cross_stream,
                current_stream,
                replay_from_other_session_same_mapping,
                replay_from_stale_session,
                replay_from_cross_stream,
                replay_from_current_session,
            ) = asyncio.run(_exercise())
        finally:
            with self.server._WORKFLOW_TASK_LOCK:
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID.pop("stream-a", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID.pop("stream-b", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_SESSION_ID.pop("stream-a", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_SESSION_ID.pop("stream-b", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID_AT.pop("stream-a", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID_AT.pop("stream-b", None)

        self.assertIsNone(other_session_same_mapping)
        self.assertEqual(replay_from_other_session_same_mapping, [])
        self.assertIsNone(stale_stream)
        self.assertEqual(replay_from_stale_session, [])
        self.assertIsNone(cross_stream)
        self.assertEqual(replay_from_cross_stream, [])
        self.assertEqual(current_stream, "stream-a")
        self.assertEqual(replay_from_current_session, [])

    def test_streamable_http_event_store_bounds_retention_and_skips_unscoped_messages(self):
        store = self.server._BoundedWorkflowTaskEventStore(max_events=2, retention_seconds=3600)
        raw_unscoped = SimpleNamespace(root=SimpleNamespace(result={"stdout": "raw output"}))
        msg1 = SimpleNamespace(root=SimpleNamespace(method="notifications/progress", params={"progress": 10}))
        msg2 = SimpleNamespace(root=SimpleNamespace(method="notifications/progress", params={"progress": 20}))
        msg3 = SimpleNamespace(root=SimpleNamespace(method="notifications/progress", params={"progress": 30}))

        async def _exercise():
            token = self.server._STREAMABLE_HTTP_SESSION_ID.set("session-a")
            try:
                await store.store_event("stream-raw", raw_unscoped)
                with self.server._WORKFLOW_TASK_LOCK:
                    self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID["stream-a"] = "task-a"
                    self.server._WORKFLOW_TASK_REQUEST_TO_SESSION_ID["stream-a"] = "session-a"
                    self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID_AT["stream-a"] = time.monotonic()
                old_event = await store.store_event("stream-a", msg1)
                kept_event = await store.store_event("stream-a", msg2)
                newest_event = await store.store_event("stream-a", msg3)

                replay_from_old = []
                replay_from_kept = []

                async def send_old(event_message):
                    replay_from_old.append(event_message)

                async def send_kept(event_message):
                    replay_from_kept.append(event_message)

                old_stream = await store.replay_events_after(old_event, send_old)
                kept_stream = await store.replay_events_after(kept_event, send_kept)
                return old_stream, kept_stream, newest_event, replay_from_old, replay_from_kept, store.stats()
            finally:
                self.server._STREAMABLE_HTTP_SESSION_ID.reset(token)

        try:
            old_stream, kept_stream, newest_event, replay_from_old, replay_from_kept, stats = asyncio.run(_exercise())
        finally:
            with self.server._WORKFLOW_TASK_LOCK:
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID.pop("stream-a", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_SESSION_ID.pop("stream-a", None)
                self.server._WORKFLOW_TASK_REQUEST_TO_TASK_ID_AT.pop("stream-a", None)

        self.assertIsNone(old_stream)
        self.assertEqual(replay_from_old, [])
        self.assertEqual(kept_stream, "stream-a")
        self.assertEqual([item.event_id for item in replay_from_kept], [newest_event])
        self.assertEqual(stats["events"], 2)
        self.assertEqual(stats["sessions"], 1)
        self.assertEqual(stats["tasks"], 1)

    def test_workflow_task_cancel_unknown_task_raises_without_persisting_status(self):
        with self.assertRaises(FileNotFoundError):
            self.server.workflow_task(
                action="cancel",
                task_id="missing-task",
                cancel_reason="missing task token=unknown-secret-value",
            )

        notification = self.server.mcp_types.CancelledNotification(
            params=self.server.mcp_types.CancelledNotificationParams(
                requestId="unknown-request-id",
                reason="client cancelled token=unknown-secret-value",
            )
        )
        asyncio.run(self.server._handle_workflow_task_cancelled_notification(notification))

        status_path = self.repo_path / ".codebase-tooling-mcp" / "tasks" / "missing-task.json"
        self.assertFalse(status_path.exists())

    def test_workflow_task_cancel_finished_task_is_ignored_and_preserves_status(self):
        task_id = "finished-cancel"
        secret = "finished-secret-value"
        result = {"schema": "vscode_task_run.v1", "ok": True, "sentinel": "keep"}
        self._write_workflow_task_fixture(
            task_id,
            status="succeeded",
            state="succeeded",
            ok=True,
            progress=1.0,
            progress_detail={"phase": "complete", "percent": 100},
            result=result,
            audit_events=[{"event": "completed", "at": self.server._now_iso()}],
        )

        out = self.server.workflow_task(
            action="cancel",
            task_id=task_id,
            cancel_reason=f"late cancel token={secret}",
        )

        self.assertEqual(out["status"], "succeeded")
        self.assertEqual(out["state"], "succeeded")
        self.assertTrue(out["ok"])
        self.assertEqual(out["progress_detail"], {"phase": "complete", "percent": 100})
        self.assertEqual(out["result"], result)
        self.assertTrue(out["cancellation"]["requested"])
        self.assertTrue(out["cancellation"]["ignored"])
        self.assertEqual(out["cancellation"]["ignored_status"], "succeeded")
        self.assertIn("cancel_ignored", [event["event"] for event in out["audit_events"]])
        self.assertNotIn("cancelled", [event["event"] for event in out["audit_events"]])
        self._assert_persisted_cancellation_metadata_excludes(task_id, secret)

    def test_workflow_task_duplicate_cancel_is_idempotent_and_redacted(self):
        task_id = "duplicate-cancel"
        first_secret = "first-secret-value"
        second_secret = "second-secret-value"
        self._write_workflow_task_fixture(
            task_id,
            status="running",
            state="running",
            progress=0.25,
            progress_detail={"phase": "running", "percent": 25},
        )

        first = self.server.workflow_task(
            action="cancel",
            task_id=task_id,
            cancel_reason=f"stop token={first_secret}",
        )
        first_audit_events = first["audit_events"]
        first_cancellation = first["cancellation"]
        second = self.server.workflow_task(
            action="cancel",
            task_id=task_id,
            cancel_reason=f"stop again token={second_secret}",
        )

        self.assertEqual(first["status"], "cancelled")
        self.assertEqual(second["status"], "cancelled")
        self.assertEqual(second["audit_events"], first_audit_events)
        self.assertEqual(second["cancellation"], first_cancellation)
        self._assert_persisted_cancellation_metadata_excludes(
            task_id, first_secret, second_secret
        )

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
