# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

import json

from tests.server_test_support import ServerToolsTestBase


class ObservationCompressionReportTests(ServerToolsTestBase):
    def _write_jsonl(self, rel_path, rows):
        path = self.repo_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        return path

    def _sample_rows(self):
        passing_pytest = "================ test session starts ================\n3 passed in 0.42s"
        stack_trace = "Traceback (most recent call last):\n  File \"src/app.py\", line 7, in main\nValueError: boom"
        return [
            {
                "timestamp": "2026-05-10T10:00:00+00:00",
                "tool_name": "command_runner",
                "success": True,
                "arguments": {"command": "pytest tests/test_sample.py"},
                "output": passing_pytest,
            },
            {
                "timestamp": "2026-05-10T10:01:00+00:00",
                "tool_name": "command_runner",
                "success": True,
                "arguments": {"command": "pytest tests/test_sample.py"},
                "output": passing_pytest,
            },
            {
                "timestamp": "2026-05-10T10:02:00+00:00",
                "tool_name": "command_runner",
                "success": False,
                "arguments": {"command": "pytest tests/test_failure.py", "changed_files": ["src/app.py", "tests/test_failure.py"]},
                "exit_code": 1,
                "stderr": "FAILED tests/test_failure.py::test_main - AssertionError",
            },
            {
                "timestamp": "2026-05-10T10:03:00+00:00",
                "tool_name": "command_runner",
                "success": False,
                "arguments": {"command": "python src/app.py"},
                "exit_code": 1,
                "stderr": stack_trace,
            },
            {
                "timestamp": "2026-05-10T10:04:00+00:00",
                "tool_name": "command_runner",
                "success": False,
                "arguments": {"command": "python src/app.py"},
                "exit_code": 1,
                "stderr": stack_trace,
            },
            {
                "timestamp": "2026-05-10T10:05:00+00:00",
                "tool_name": "dependency_security_report",
                "success": True,
                "result": {"findings": [{"severity": "high", "id": "CVE-2026-1234", "path": "requirements.txt"}]},
            },
            {
                "timestamp": "2026-05-10T10:06:00+00:00",
                "tool_name": "workspace_transaction",
                "success": True,
                "result": {"snapshot_id": "state_snapshot_abc123", "rollback_ref": "rollback-abc123"},
            },
            {
                "timestamp": "2026-05-10T10:07:00+00:00",
                "tool_name": "task_router",
                "success": True,
                "arguments": {"user_constraints": ["Do not touch approved PRs", "must be read-only"]},
            },
            {
                "timestamp": "2026-05-10T10:08:00+00:00",
                "tool_name": "command_runner",
                "success": True,
                "arguments": {"command": "pip install -r requirements.txt"},
                "output": "Collecting fastapi\nRequirement already satisfied: pydantic\nSuccessfully installed demo-1.0",
            },
            {
                "timestamp": "2026-05-10T10:09:00+00:00",
                "tool_name": "command_runner",
                "success": False,
                "arguments": {"command": "cat /home/alice/private.txt", "api_key": "sk-abcdefghijklmnopqrstuvwxyz"},
                "exit_code": 1,
                "stderr": "permission denied for /home/alice/private.txt",
            },
        ]

    def test_classifies_observations_and_preserves_critical_signals(self):
        self._write_jsonl(".codebase-tooling-mcp/audit/security_events.jsonl", self._sample_rows())

        report = self.server.observation_compression_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            export=True,
        )

        self.assertEqual(report["schema"], "observation_compression_report.v1")
        self.assertTrue(report["read_only"])
        self.assertTrue(report["advisory_only"])
        buckets = report["summary"]["bucket_counts"]
        for bucket in ["preserve_raw", "summarize", "deduplicate", "drop_low_value", "redact_blocked"]:
            self.assertIn(bucket, buckets)
        self.assertGreaterEqual(buckets["preserve_raw"], 4)
        self.assertGreaterEqual(buckets["deduplicate"], 2)
        self.assertGreaterEqual(buckets["drop_low_value"], 1)
        self.assertGreaterEqual(buckets["redact_blocked"], 1)
        self.assertGreater(report["summary"]["estimated_token_savings"], 0)
        self.assertGreater(report["summary"]["retained_critical_signal_count"], 0)
        self.assertTrue(report["low_confidence_caveats"])
        self.assertIn("## Buckets", report["markdown"])
        self.assertTrue((self.repo_path / report["exports"]["json"]).exists())
        self.assertTrue((self.repo_path / report["exports"]["markdown"]).exists())

        failing = [row for row in report["observations"] if row["critical_signals"].get("exit_code") == 1]
        self.assertTrue(failing)
        self.assertTrue(any("src/app.py" in row["critical_signals"].get("changed_file_paths", []) for row in failing))
        self.assertTrue(any(row["critical_signals"].get("security_finding") for row in report["observations"]))
        self.assertTrue(any(row["critical_signals"].get("rollback_or_snapshot_reference") for row in report["observations"]))
        self.assertTrue(any(row["critical_signals"].get("user_constraint") for row in report["observations"]))
        self.assertTrue(any(row["critical_signals"].get("novel_error") for row in report["observations"]))

        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", encoded)
        self.assertNotIn("/home/alice/private.txt", encoded)
        self.assertTrue(all(item["raw_excerpt_included"] is False for item in report["fingerprints"]))

    def test_rejects_outside_absolute_paths_before_changed_path_extraction(self):
        self._write_jsonl(
            ".codebase-tooling-mcp/audit/security_events.jsonl",
            [
                {
                    "timestamp": "2026-05-10T11:00:00+00:00",
                    "tool_name": "redaction_probe",
                    "success": False,
                    "exit_code": 1,
                    "result": {
                        "path": "/home/alice/private.txt",
                        "file_path": "/work/private/secret.txt",
                        "changed": "/etc/passwd",
                    },
                },
                {
                    "timestamp": "2026-05-10T11:01:00+00:00",
                    "tool_name": "valid_path_probe",
                    "success": True,
                    "result": {
                        "path": "docs/a.md",
                        "file_path": str(self.repo_path / "src" / "sample.py"),
                        "changed_files": [
                            "src/sample.py",
                            str(self.repo_path / "tests" / "test_sample.py"),
                        ],
                    },
                },
            ],
        )

        report = self.server.observation_compression_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            export=False,
        )

        changed_paths = [
            path
            for row in report["observations"]
            for path in row["critical_signals"].get("changed_file_paths", [])
        ]
        encoded_paths = json.dumps(changed_paths, sort_keys=True)
        self.assertNotIn("home/alice/private.txt", encoded_paths)
        self.assertNotIn("work/private/secret.txt", encoded_paths)
        self.assertNotIn("etc/passwd", encoded_paths)
        self.assertIn("docs/a.md", changed_paths)
        self.assertIn("src/sample.py", changed_paths)
        self.assertIn("tests/test_sample.py", changed_paths)

    def test_output_is_deterministic_for_same_stored_observations(self):
        self._write_jsonl(".codebase-tooling-mcp/audit/security_events.jsonl", self._sample_rows())

        kwargs = {
            "start_time": "2026-05-10T00:00:00+00:00",
            "end_time": "2026-05-11T00:00:00+00:00",
            "export": False,
        }
        first = self.server.observation_compression_report(**kwargs)
        second = self.server.observation_compression_report(**kwargs)

        self.assertEqual(first, second)

    def test_self_optimization_includes_observation_compression_opportunities(self):
        self._write_jsonl(".codebase-tooling-mcp/audit/security_events.jsonl", self._sample_rows())

        report = self.server.self_optimization_report(
            start_time="2026-05-10T00:00:00+00:00",
            end_time="2026-05-11T00:00:00+00:00",
            include_git=False,
            include_traces=False,
        )

        observation = report["metrics"]["observation_compression"]
        self.assertEqual(observation["schema"], "observation_compression_report.v1")
        self.assertGreater(observation["summary"]["observation_count"], 0)
        self.assertGreaterEqual(observation["compression_opportunities"]["estimated_token_savings"], 0)
