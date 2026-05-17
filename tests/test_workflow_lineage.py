# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json

from tests.server_test_support import ServerToolsTestBase


class WorkflowLineageTests(ServerToolsTestBase):
    def _lineage_plan_inputs(self, *, export: bool = True):
        events, audit_meta = self.server._load_audit_events(None, None)
        counts = self.server._aggregate_audit_events(events)
        git_info = {
            "base_ref": "HEAD",
            "head_ref": "HEAD",
            "base_commit": self.git("rev-parse", "HEAD").stdout.strip(),
            "head_commit": self.git("rev-parse", "HEAD").stdout.strip(),
            "range": "HEAD...HEAD",
        }
        constraints = self.server._workflow_lineage_request_constraints(
            start_time="",
            end_time="",
            base_ref="HEAD",
            head_ref="HEAD",
            export=export,
            compressed_observation=False,
        )
        return self.server._governance_workflow_lineage_plan_inputs(
            constraints=constraints,
            git_info=git_info,
            counts=counts,
            audit_meta=audit_meta,
        )

    def test_plan_id_is_stable_and_changes_for_meaningful_inputs(self):
        first = self._lineage_plan_inputs(export=True)
        second = self._lineage_plan_inputs(export=True)
        changed = self._lineage_plan_inputs(export=False)

        self.assertEqual(
            self.server._workflow_lineage_plan_id(first),
            self.server._workflow_lineage_plan_id(second),
        )
        self.assertNotEqual(
            self.server._workflow_lineage_plan_id(first),
            self.server._workflow_lineage_plan_id(changed),
        )

    def test_lineage_sanitizer_redacts_secrets_prompts_and_absolute_paths(self):
        sanitized = self.server._workflow_lineage_sanitize(
            {
                "token": "super-secret-value",
                "prompt": "raw private prompt text",
                "repo_path": str(self.repo_path / "src" / "sample.py"),
                "outside_path": "/tmp/private/file.txt",
            }
        )
        encoded = json.dumps(sanitized, sort_keys=True)

        self.assertNotIn("super-secret-value", encoded)
        self.assertNotIn("raw private prompt text", encoded)
        self.assertNotIn(str(self.repo_path), encoded)
        self.assertIn("<redacted>", encoded)
        self.assertIn("<absolute_path_outside_repo>", encoded)

    def test_governance_report_emits_lineage_manifest_and_verify_matches(self):
        out = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=True)
        lineage_path = out["exports"]["lineage"]
        manifest_path = self.repo_path / lineage_path
        self.assertTrue(manifest_path.exists())

        manifest_text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
        self.assertEqual(manifest["schema"], "workflow_lineage.v1")
        self.assertEqual(manifest["plan_id"], out["lineage"]["plan_id"])
        self.assertEqual(manifest["workflow"]["name"], "governance_report")
        self.assertTrue(manifest["nodes"])
        self.assertTrue(manifest["edges"])
        self.assertTrue(manifest["artifacts"])
        self.assertNotIn(str(self.repo_path), manifest_text)

        sidecar_path = self.repo_path / out["exports"]["provenance"][out["exports"]["json"]]
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(sidecar["links"]["workflow_lineage"], lineage_path)

        verify = self.server.workflow_lineage(mode="verify", manifest_path=lineage_path)
        self.assertEqual(verify["schema"], "workflow_lineage.verify.v1")
        self.assertEqual(verify["status"], "matched")
        self.assertIn("non_deterministic_node", verify["conditions"])
        self.assertTrue(verify["security"]["read_only"])

    def test_workflow_lineage_verify_detects_artifact_drift(self):
        out = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=True)
        markdown_path = self.repo_path / out["exports"]["markdown"]
        markdown_path.write_text(markdown_path.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")

        verify = self.server.workflow_lineage(mode="verify", manifest_path=out["exports"]["lineage"])

        self.assertEqual(verify["status"], "artifact_changed")
        self.assertEqual(verify["checks"]["artifacts"]["status"], "artifact_changed")

    def test_workflow_lineage_verify_detects_input_drift(self):
        out = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=True)
        audit_path = self.repo_path / ".codebase-tooling-mcp" / "audit" / "security_events.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            self.server.json.dumps(
                {
                    "timestamp": "2026-05-12T08:00:00+00:00",
                    "tool_name": "policy_simulator",
                    "categories": ["read-only"],
                    "success": True,
                    "reason": "",
                    "arguments": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        verify = self.server.workflow_lineage(mode="verify", manifest_path=out["exports"]["lineage"])

        self.assertEqual(verify["status"], "input_changed")
        self.assertEqual(verify["checks"]["plan"]["status"], "input_changed")
