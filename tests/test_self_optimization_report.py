# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import os
import subprocess
from copy import deepcopy

from tests.server_test_support import ServerToolsTestBase


class SelfOptimizationReportTests(ServerToolsTestBase):
    def _write_jsonl(self, rel_path, rows):
        path = self.repo_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        return path

    def _commit_with_date(self, message, timestamp="2026-05-10T12:00:00+00:00"):
        self.write_repo_text("docs/throughput.md", message + "\n")
        self.git("add", "docs/throughput.md")
        env = os.environ.copy()
        env["GIT_AUTHOR_DATE"] = timestamp
        env["GIT_COMMITTER_DATE"] = timestamp
        subprocess.run(
            ["git", "-C", str(self.repo_path), "commit", "-m", message],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    def _sample_audit_rows(self):
        return [
            {
                "timestamp": "2026-05-10T10:00:00+00:00",
                "tool_name": "grep",
                "categories": ["read-only"],
                "success": True,
                "reason": "",
                "arguments": {
                    "query": "issue #90 optimization loop",
                    "mode": "search",
                    "execution_mode": "online-cloud-assisted",
                },
            },
            {
                "timestamp": "2026-05-10T10:05:00+00:00",
                "tool_name": "command_runner",
                "categories": ["shell/process"],
                "success": False,
                "reason": "timeout noisy log",
                "arguments": {"task": "issue #90 test run"},
            },
            {
                "timestamp": "2026-05-10T10:08:00+00:00",
                "tool_name": "governance_report",
                "categories": ["read-only"],
                "success": True,
                "reason": "",
                "arguments": {
                    "workflow": "governance_report",
                    "pr": "PR #12",
                    "compressed_observation": {
                        "schema": "compressed_observation.v1",
                        "omitted": [{"category": "rows", "count": 3}],
                    },
                },
            },
        ]

    def _sample_span_rows(self):
        return [
            {
                "schema": "mcp_otel_span.local_json.v1",
                "name": "mcp.tool.grep",
                "start_time": "2026-05-10T10:00:01+00:00",
                "end_time": "2026-05-10T10:00:02+00:00",
                "duration_ms": 1200,
                "status": {"code": "OK"},
                "attributes": {
                    "mcp.tool.name": "grep",
                    "mcp.tool.mode": "search",
                    "gen_ai.request.model": "router-model-a",
                    "mcp.backend": "local",
                    "gen_ai.usage.input_tokens": 100,
                    "gen_ai.usage.output_tokens": 50,
                    "mcp.cache.hit": True,
                    "issue": "issue #90",
                },
            },
            {
                "schema": "mcp_otel_span.local_json.v1",
                "name": "mcp.tool.workflow_task",
                "start_time": "2026-05-10T10:02:00+00:00",
                "end_time": "2026-05-10T10:02:05+00:00",
                "duration_ms": 5000,
                "status": {"code": "ERROR", "description": "task failed"},
                "attributes": {
                    "mcp.tool.name": "workflow_task",
                    "mcp.workflow.name": "governance_report",
                    "pr": "PR #12",
                },
            },
        ]

    def _write_sample_usage_fixtures(self):
        self._write_jsonl(".codebase-tooling-mcp/audit/security_events.jsonl", self._sample_audit_rows())
        self._write_jsonl(".codebase-tooling-mcp/traces/otel_spans.jsonl", self._sample_span_rows())
        (self.repo_path / ".codebase-tooling-mcp" / "cache").mkdir(parents=True, exist_ok=True)
        (self.repo_path / ".codebase-tooling-mcp" / "cache" / "tool_cache.json").write_text(
            json.dumps({"entries": {"grep": {"k1": {"updated_at": "2026-05-10T10:00:00+00:00", "value": {"ok": True}}}}}),
            encoding="utf-8",
        )
        task_dir = self.repo_path / ".codebase-tooling-mcp" / "tasks"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "task-issue-90.json").write_text(
            json.dumps(
                {
                    "schema": "workflow_task.v1",
                    "task_id": "task-issue-90",
                    "workflow": "governance_report",
                    "status": "completed",
                    "state": "completed",
                    "attempt": 2,
                    "retry_of": "task-issue-90-prev",
                    "retries": [{"at": "2026-05-10T10:07:00+00:00", "reason": "retry after noisy gate"}],
                    "created_at": "2026-05-10T10:00:00+00:00",
                    "started_at": "2026-05-10T10:03:00+00:00",
                    "updated_at": "2026-05-10T10:14:00+00:00",
                    "finished_at": "2026-05-10T10:15:00+00:00",
                    "arguments": {"issue": "issue #90", "pr": "PR #12"},
                    "audit_events": [
                        {"event": "queued", "at": "2026-05-10T10:00:00+00:00"},
                        {"event": "start", "at": "2026-05-10T10:03:00+00:00"},
                        {"event": "test_gate", "state": "running", "at": "2026-05-10T10:05:00+00:00"},
                        {"event": "blocked", "at": "2026-05-10T10:06:00+00:00"},
                        {"event": "retry", "at": "2026-05-10T10:07:00+00:00"},
                        {"event": "complete", "at": "2026-05-10T10:15:00+00:00"},
                    ],
                    "test_gates": [
                        {"name": "pytest focused", "status": "passed"},
                        {"name": "git diff --check", "status": "passed"},
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self._commit_with_date("Fixes #90 via PR #12")

    def test_aggregates_usage_by_issue_pr_workflow_and_routing(self):
        self._write_sample_usage_fixtures()

        report = self.server.self_optimization_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            export=False,
        )

        self.assertEqual(report["schema"], "self_optimization_report.v1")
        totals = report["metrics"]["totals"]
        self.assertEqual(totals["audit_event_count"], 3)
        self.assertEqual(totals["trace_span_count"], 2)
        self.assertGreaterEqual(totals["tool_call_count"], 5)
        self.assertGreaterEqual(totals["failed_or_noisy_count"], 2)
        self.assertIn("#90", report["metrics"]["throughput"]["issues_touched"])
        self.assertIn("#12", report["metrics"]["throughput"]["prs_touched"])
        self.assertTrue(any(row["name"] == "#90" for row in report["metrics"]["by_issue"]))
        self.assertTrue(any(row["name"] == "governance_report" for row in report["metrics"]["by_workflow"]))
        self.assertTrue(any(row["name"] == "router-model-a" for row in report["metrics"]["routing"]["models"]))
        self.assertTrue(any(row["name"] == "local" for row in report["metrics"]["routing"]["backends"]))
        self.assertEqual(totals["explicit_token_record_count"], 1)
        self.assertGreater(report["metrics"]["cache"]["entry_count"], 0)
        self.assertGreater(report["metrics"]["compression"]["estimated_saved_tokens"], 0)
        self.assertIn("task-issue-90", report["metrics"]["throughput"]["tasks_touched"])
        self.assertTrue(any(row["name"] == "task-issue-90" for row in report["metrics"]["by_task"]))
        self.assertGreater(report["metrics"]["state_transitions"]["count"], 0)
        self.assertGreater(report["metrics"]["test_gates"]["count"], 0)
        self.assertGreater(report["metrics"]["retries_rework"]["retry_count"], 0)
        self.assertGreater(report["metrics"]["blocked_waiting_time"]["seconds"], 0)
        self.assertEqual(report["metrics"]["data_availability"]["test_gates"]["status"], "observed")

    def test_redacts_secrets_and_sensitive_names(self):
        self._write_jsonl(
            ".codebase-tooling-mcp/audit/security_events.jsonl",
            [
                {
                    "timestamp": "2026-05-10T10:00:00+00:00",
                    "tool_name": "task_router",
                    "categories": ["read-only"],
                    "success": False,
                    "reason": "AcmeCo failed for Alice Example at /tmp/customer/raw-trace.json",
                    "arguments": {
                        "project": "AcmeCo",
                        "person": "Alice Example",
                        "query": "Alice Example AcmeCo password=super-secret-value token=ghp_12345678901234567890 issue #90",
                        "log_path": "/tmp/customer/raw-trace.json",
                    },
                }
            ],
        )

        report = self.server.self_optimization_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            export=False,
            include_git=False,
            include_traces=False,
            redact_terms=["AcmeCo", "Alice Example"],
        )
        encoded = json.dumps(report, sort_keys=True)

        self.assertNotIn("AcmeCo", encoded)
        self.assertNotIn("Alice", encoded)
        self.assertNotIn("ghp_12345678901234567890", encoded)
        self.assertNotIn("super-secret-value", encoded)
        self.assertNotIn("/tmp/customer/raw-trace.json", encoded)
        self.assertIn("<absolute_path_outside_repo>", encoded)
        self.assertIn("<redacted", encoded)
        self.assertFalse(report["security"]["raw_traces_exposed"])
        self.assertFalse(report["security"]["records_secrets"])
        self.assertIsInstance(report["metrics"]["estimation_basis"]["token_estimates"], str)

    def test_baseline_estimation_counts_spend_and_savings(self):
        record = {
            "source": "trace",
            "tool": "grep",
            "workflow": "search",
            "success": True,
            "duration_ms": 1000,
            "categories": [],
            "issue_refs": ["#90"],
            "pr_refs": [],
            "tokens": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "saved_tokens": 20},
            "cache_hit_count": 0,
            "compression": {"compressed_observation_count": 0, "omitted_signal_count": 0, "estimated_saved_tokens": 0},
        }

        metrics = self.server._self_opt_aggregate_records([record], {"total_entries": 0, "tools": {}})

        self.assertEqual(metrics["totals"]["estimated_spent_seconds"], 1.0)
        self.assertEqual(metrics["totals"]["estimated_baseline_seconds"], 30.0)
        self.assertEqual(metrics["totals"]["estimated_saved_seconds"], 29.0)
        self.assertEqual(metrics["totals"]["estimated_saved_tokens"], 20)

    def test_duplicate_recommendation_suppression_is_stable(self):
        first = self.server._self_opt_candidate(
            "cache-reuse",
            "Reuse cache or index artifacts for repeated inspection",
            "same",
            {},
            "do it",
        )
        duplicate = self.server._self_opt_candidate(
            "cache-reuse",
            "Reuse cache or index artifacts for repeated inspection",
            "same again",
            {},
            "do it again",
        )

        within_report = self.server._self_opt_suppress_duplicate_recommendations([first, duplicate], [])
        self.assertFalse(within_report[0]["suppressed"])
        self.assertTrue(within_report[1]["suppressed"])
        self.assertEqual(within_report[1]["duplicate_of"], within_report[0]["id"])

        existing = [{"duplicate_key": first["duplicate_key"], "id": "existing-issue-1"}]
        against_existing = self.server._self_opt_suppress_duplicate_recommendations([first], existing)
        self.assertTrue(against_existing[0]["suppressed"])
        self.assertEqual(against_existing[0]["duplicate_of"], "existing-issue-1")

    def test_offline_no_network_behavior_with_missing_sources(self):
        report = self.server.self_optimization_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            export=False,
            include_git=False,
        )

        self.assertFalse(report["sources"]["network"]["used"])
        self.assertTrue(report["security"]["offline_capable"])
        self.assertFalse(report["security"]["network_used"])
        self.assertEqual(report["exports"], {})
        self.assertEqual(report["metrics"]["totals"]["tool_call_count"], 0)
        self.assertEqual(report["confidence"], "low")
        self.assertIn("No local audit", " ".join(report["caveats"]))
        self.assertEqual(report["metrics"]["data_availability"]["token_usage"]["status"], "not_available")
        self.assertEqual(report["metrics"]["data_availability"]["state_transitions"]["status"], "not_available")
        self.assertEqual(report["metrics"]["test_gates"]["status"], "not_available")
        self.assertEqual(report["github_issue_gate"]["status"], "disabled")

    def test_github_issue_update_gate_requires_explicit_high_confidence(self):
        self._write_jsonl(
            ".codebase-tooling-mcp/audit/security_events.jsonl",
            [
                {
                    "timestamp": "2026-05-10T10:00:00+00:00",
                    "tool_name": "command_runner",
                    "categories": ["shell/process"],
                    "success": False,
                    "reason": "timeout noisy log",
                    "arguments": {"task": "issue #90 test run"},
                }
            ],
        )

        default_report = self.server.self_optimization_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            export=False,
            include_git=False,
            include_traces=False,
        )
        self.assertEqual(default_report["github_issue_gate"]["status"], "disabled")
        self.assertFalse(default_report["sources"]["network"]["used"])

        dry_run = self.server.self_optimization_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            export=False,
            include_git=False,
            include_traces=False,
            github_issue_update_mode="dry_run",
            github_repository="owner/repo",
        )
        self.assertEqual(dry_run["github_issue_gate"]["status"], "dry_run")
        self.assertFalse(dry_run["github_issue_gate"]["network_used"])
        self.assertGreaterEqual(dry_run["github_issue_gate"]["eligible_count"], 1)
        self.assertTrue(
            all(action["confidence"] == "high" for action in dry_run["github_issue_gate"]["planned_actions"])
        )

        self.server.ALLOW_MUTATIONS = False
        try:
            with self.assertRaises(PermissionError):
                self.server.self_optimization_report(
                    start_time="2026-05-10T00:00:00+00:00",
                    end_time="2026-05-11T00:00:00+00:00",
                    export=False,
                    include_git=False,
                    include_traces=False,
                    github_issue_update_mode="apply",
                    github_repository="owner/repo",
                )
        finally:
            self.server.ALLOW_MUTATIONS = True

        existing_title = "Record token and model routing fields in redacted local telemetry"
        deduped = self.server.self_optimization_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            export=False,
            include_git=False,
            include_traces=False,
            github_issue_metadata=[{"number": 123, "title": existing_title, "state": "open", "body": ""}],
            github_issue_update_mode="dry_run",
            github_repository="owner/repo",
        )
        telemetry_candidate = next(
            item for item in deduped["optimization_candidates"] if item["title"] == existing_title
        )
        self.assertTrue(telemetry_candidate["suppressed"])
        self.assertEqual(telemetry_candidate["duplicate_of"], "#123")
        self.assertTrue(
            any(action["action"] == "update" and action["target_issue"] == "#123" for action in deduped["github_issue_gate"]["planned_actions"])
        )
        self.assertEqual(deduped["sources"]["github_issues"]["status"], "provided")
        self.assertEqual(deduped["sources"]["proxy_anonymizer_disclosure"]["status"], "not_available")

        local_index = self.repo_path / ".codebase-tooling-mcp" / "reports" / "SELF_OPTIMIZATION_GITHUB_ISSUES.json"
        local_index.parent.mkdir(parents=True, exist_ok=True)
        local_index.write_text(
            json.dumps({"issues": [{"number": 124, "title": existing_title, "state": "open", "body": ""}]}),
            encoding="utf-8",
        )
        local_deduped = self.server.self_optimization_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            export=False,
            include_git=False,
            include_traces=False,
            github_issue_update_mode="dry_run",
            github_repository="owner/repo",
        )
        local_candidate = next(
            item for item in local_deduped["optimization_candidates"] if item["title"] == existing_title
        )
        self.assertTrue(local_candidate["suppressed"])
        self.assertEqual(local_candidate["duplicate_of"], "#124")
        self.assertEqual(local_deduped["sources"]["github_issues"]["status"], "local_index")

    def test_report_output_is_stable_except_generated_metadata(self):
        self._write_sample_usage_fixtures()

        first = self.server.self_optimization_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            export=False,
            include_git=False,
        )
        second = self.server.self_optimization_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            export=False,
            include_git=False,
        )

        def stable(report):
            copied = deepcopy(report)
            copied.pop("generated_at", None)
            copied.pop("report_id", None)
            return copied

        self.assertEqual(stable(first), stable(second))
