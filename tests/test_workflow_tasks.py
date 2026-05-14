# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import time
from datetime import datetime, timedelta, timezone

from tests.server_test_support import ServerToolsTestBase


class WorkflowTaskTests(ServerToolsTestBase):
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
