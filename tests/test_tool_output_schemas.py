# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

import asyncio

from source.tool_output_schemas import (
    ERROR_OUTPUT_SCHEMA,
    RESOURCE_LINK_SCHEMA,
    RESULT_REFERENCE_SCHEMA,
    RESULT_REFERENCE_RESOLVE_SCHEMA,
    SCHEMA_BACKED_TOOL_NAMES,
    STATE_SNAPSHOT_OUTPUT_SCHEMA,
    TOOL_OUTPUT_SCHEMAS,
    all_tool_output_contracts,
    make_tool_error,
    structured_tool_result,
    validate_against_schema,
)
from tests.server_test_support import ServerToolsTestBase


class ToolOutputSchemaContractTests(ServerToolsTestBase):
    def test_initial_schema_backed_tool_list_is_checked_in(self):
        self.assertEqual(
            SCHEMA_BACKED_TOOL_NAMES,
            (
                "repo_info",
                "roots_diagnostics",
                "model_assisted_summary",
                "runtime_state",
                "git_status",
                "grep",
                "find_paths",
                "read_snippet",
                "summarize_diff",
                "risk_scoring",
                "workspace_transaction",
                "policy_simulator",
                "workflow_policy_plan",
                "policy_governance_decision",
                "workflow_reminder",
                "workflow_task",
                "task_status",
                "clarification_gate",
                "release_readiness",
                "agent_quality_delta",
                "tool_catalog_integrity",
                "dependency_security_report",
                "ci_workflow_security_report",
                "secret_exposure_report",
                "agent_security_delta",
                "agent_security_delta_report",
                "mcp_threat_model_report",
                "governance_report",
                "memory_governance_report",
                "self_optimization_report",
                "observation_compression_report",
                "agents_context_health",
                "artifact_provenance",
                "result_reference_resolve",
                "workflow_diagnostics",
                "workflow_event_log_report",
                "workflow_lineage",
                "interaction_invariant_audit",
                "mutation_step_guard",
            ),
        )
        contracts = all_tool_output_contracts()
        self.assertEqual(len(contracts["tools"]), len(SCHEMA_BACKED_TOOL_NAMES))
        self.assertEqual(set(TOOL_OUTPUT_SCHEMAS), set(SCHEMA_BACKED_TOOL_NAMES))

    def test_schema_documentation_tracks_checked_in_contracts(self):
        docs_root = self.server.Path(__file__).resolve().parents[1]
        schema_doc = (docs_root / "docs" / "mcp-output-schemas.md").read_text(encoding="utf-8")
        contracts = all_tool_output_contracts()
        by_tool = {entry["tool"]: entry for entry in contracts["tools"]}
        table_rows = {
            line.split("|", 3)[1].strip().strip("`"): line
            for line in schema_doc.splitlines()
            if line.startswith("| `")
        }

        for tool_name in SCHEMA_BACKED_TOOL_NAMES:
            with self.subTest(tool_name=tool_name):
                self.assertIn(f"- `{tool_name}`", schema_doc)
                row = table_rows[tool_name]
                for field in by_tool[tool_name]["stableFields"]:
                    if field.startswith("<"):
                        continue
                    self.assertIn(f"`{field}`", row)

    def test_threat_model_dread_rubric_is_stable_required_contract(self):
        contract = all_tool_output_contracts()["tools"][SCHEMA_BACKED_TOOL_NAMES.index("mcp_threat_model_report")]
        schema = TOOL_OUTPUT_SCHEMAS["mcp_threat_model_report"]

        self.assertIn("dread_rubric", contract["stableFields"])
        self.assertIn("dread_rubric", schema["required"])
        self.assertNotIn("dread_rubric", contract["experimentalFields"])

    def test_representative_success_outputs_validate_against_schemas(self):
        self.write_repo_text("src/schema_contract.py", "def schema_marker():\n    return 'marker'\n")

        lineage_report = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=True)
        referenced_report = self.server.self_optimization_report(result_mode="reference")
        workflow_task_output = self.server.workflow_task(
            workflow="governance_report", base_ref="HEAD", head_ref="HEAD", export=False
        )

        outputs = {
            "repo_info": self.server.repo_info(),
            "roots_diagnostics": asyncio.run(self.server.roots_diagnostics()),
            "model_assisted_summary": asyncio.run(self.server.model_assisted_summary()),
            "runtime_state": self.server.runtime_state(),
            "git_status": self.server.git_status(),
            "grep": self.server.grep(pattern="schema_marker", path="src"),
            "find_paths": self.server.find_paths(path="src", recursive=True),
            "read_snippet": self.server.read_snippet(
                path="src/schema_contract.py",
                start_line=1,
                end_line=2,
                output_profile="normal",
            ),
            "summarize_diff": self.server.summarize_diff(output_profile="normal"),
            "risk_scoring": self.server.risk_scoring(),
            "workspace_transaction": self.server.workspace_transaction(mode="begin", label="schema-contract"),
            "policy_simulator": self.server.policy_simulator(base_ref="HEAD", head_ref="HEAD"),
            "workflow_policy_plan": self.server.workflow_policy_plan(
                intent="Inspect repository status before summarizing",
                execution_mode="offline-onboard-only",
                allowed_targets=["."],
                planned_steps=[
                    {"tool": "repo_info", "mode": "read", "args": {}},
                    {"tool": "git_status", "mode": "read", "args": {}},
                ],
            ),
            "workflow_reminder": self.server.workflow_reminder(
                task_summary="Keep release validation fresh before readiness.",
                intended_next_action={"tool": "release_readiness", "mode": "release"},
                last_gate_results={"change_impact_gate": {"ok": True, "selected_tests": ["tests/test_tool_output_schemas.py"]}},
            ),
            "workflow_task": workflow_task_output,
            "task_status": self.server.task_status(workflow_task_output["task_id"]),
            "clarification_gate": self.server.clarification_gate(
                intent="prepare a safe release",
                target="HEAD",
                operation="release_readiness",
                risk_level="medium",
                rollback_plan="read-only check",
            ),
            "release_readiness": self.server.release_readiness(
                base_ref="HEAD",
                head_ref="HEAD",
                run_tests=False,
                run_docs_check=False,
                run_security_check=False,
                run_license_check=False,
                run_risk_check=False,
                run_impact_check=False,
            ),
            "tool_catalog_integrity": self.server.tool_catalog_integrity(),
            "dependency_security_report": self.server.dependency_security_report(export=False),
            "ci_workflow_security_report": self.server.ci_workflow_security_report(export=False),
            "secret_exposure_report": self.server.secret_exposure_report(paths=["src"], baseline_ref="HEAD"),
            "agent_security_delta": self.server.agent_security_delta(base_ref="HEAD", head_ref="HEAD", export=False),
            "agent_security_delta_report": self.server.agent_security_delta_report(base_ref="HEAD", head_ref="HEAD", export=False),
            "governance_report": self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=False),
            "observation_compression_report": self.server.observation_compression_report(
                include_traces=False,
                include_tasks=False,
                export=False,
            ),
            "agents_context_health": self.server.agents_context_health(),
            "artifact_provenance": self.server.artifact_provenance(include_reports=False, include_snapshots=False),
            "result_reference_resolve": self.server.result_reference_resolve(
                reference=referenced_report["result_reference"], include_content=False
            ),
            "workflow_diagnostics": self.server.workflow_diagnostics(),
            "workflow_event_log_report": self.server.workflow_event_log_report(),
            "workflow_lineage": self.server.workflow_lineage(
                manifest_path=lineage_report["exports"]["lineage"]
            ),
            "interaction_invariant_audit": self.server.interaction_invariant_audit(
                task_summary="Read-only audit before mutation; run tests before readiness.",
                recent_notes=["No mutation happened yet."],
            ),
            "mutation_step_guard": self.server.mutation_step_guard(
                planned_tool="workspace_transaction",
                mode="write",
                argument_summary={"path": "src/schema_contract.py"},
                declared_intent="Update schema contract fixture.",
                target_files=["src/schema_contract.py"],
                expected_diff_shape={"file_count": 1, "line_additions": 1, "line_deletions": 0},
                selected_tests=["tests/test_tool_output_schemas.py"],
                invariant_audit_summary={"ok_to_continue": True, "suspected_smells": []},
                context_metadata={"fresh": True, "tests_fresh": True},
            ),
        }

        for tool_name, payload in outputs.items():
            with self.subTest(tool_name=tool_name):
                validate_against_schema(payload, TOOL_OUTPUT_SCHEMAS[tool_name])


    def test_result_reference_mode_creates_resolvable_hash_verified_handle(self):
        payload = self.server.self_optimization_report(result_mode="reference", result_reference_ttl_hours=1)

        self.assertEqual(payload["schema"], "self_optimization_report.v1")
        self.assertEqual(payload["result_mode"], "reference")
        self.assertIn("result_reference", payload)
        self.assertNotIn("metrics", payload)
        reference = payload["result_reference"]
        validate_against_schema(reference, RESULT_REFERENCE_SCHEMA)
        self.assertEqual(reference["schema"], "mcp_result_reference.v1")
        self.assertFalse(reference["sensitivity"]["sensitive_payload_embedded"])
        self.assertEqual(reference["content"]["mime_type"], "application/json")

        resolved = self.server.result_reference_resolve(reference=reference)
        validate_against_schema(resolved, RESULT_REFERENCE_RESOLVE_SCHEMA)
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["status"], "resolved")
        self.assertIn('"schema": "self_optimization_report.v1"', resolved["content"])
        self.assertEqual(
            resolved["artifact"]["hash"]["value"],
            reference["content"]["hash"]["value"],
        )

        governance = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=False)
        audit_summary = governance["result_references"]
        self.assertGreaterEqual(audit_summary["created_count"], 1)
        self.assertGreaterEqual(audit_summary["resolved_count"], 1)
        self.assertFalse(audit_summary["privacy"]["full_payloads_embedded"])
        self.assertTrue(
            any(item["reference_id"] == reference["reference_id"] for item in audit_summary["latest"])
        )

    def test_result_reference_resolver_rejects_boundary_and_handles_missing_expired_and_hash_mismatch(self):
        payload = self.server.self_optimization_report(result_mode="reference", result_reference_ttl_hours=1)
        reference = payload["result_reference"]

        outside = self.server.json.loads(self.server.json.dumps(reference))
        outside["resolver"]["path"] = "../outside.json"
        rejected = self.server.result_reference_resolve(reference=outside)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["status"], "boundary_rejected")
        self.assertNotIn(str(self.repo_path), self.server.json.dumps(rejected))

        missing = self.server.json.loads(self.server.json.dumps(reference))
        missing["resolver"]["path"] = ".codebase-tooling-mcp/reports/result-references/missing.json"
        missing_result = self.server.result_reference_resolve(reference=missing)
        self.assertFalse(missing_result["ok"])
        self.assertEqual(missing_result["status"], "missing")

        expired = self.server.json.loads(self.server.json.dumps(reference))
        expired["expires_at"] = "2000-01-01T00:00:00+00:00"
        expired_result = self.server.result_reference_resolve(reference=expired)
        self.assertFalse(expired_result["ok"])
        self.assertEqual(expired_result["status"], "expired")

        mismatch = self.server.json.loads(self.server.json.dumps(reference))
        mismatch["content"]["hash"]["value"] = "0" * 64
        mismatch_result = self.server.result_reference_resolve(reference=mismatch)
        self.assertFalse(mismatch_result["ok"])
        self.assertEqual(mismatch_result["status"], "hash_mismatch")
        self.assertNotIn("content", mismatch_result)

    def test_result_reference_resolver_rejects_path_only_and_missing_hash_inputs(self):
        for include_content in (False, True):
            with self.subTest(include_content=include_content):
                path_only = self.server.result_reference_resolve(
                    path="README.md",
                    include_content=include_content,
                )
                validate_against_schema(path_only, RESULT_REFERENCE_RESOLVE_SCHEMA)
                self.assertFalse(path_only["ok"])
                self.assertEqual(path_only["status"], "invalid_reference")
                self.assertFalse(path_only["security"]["hash_verified_before_content"])
                self.assertFalse(path_only["security"]["payload_embedded"])
                self.assertNotIn("content", path_only)
                self.assertNotIn("# Test Repo", self.server.json.dumps(path_only))

        missing_hash = self.server.result_reference_resolve(
            reference_id="manual-readme",
            path="README.md",
            include_content=True,
        )
        validate_against_schema(missing_hash, RESULT_REFERENCE_RESOLVE_SCHEMA)
        self.assertFalse(missing_hash["ok"])
        self.assertEqual(missing_hash["status"], "invalid_reference")
        self.assertFalse(missing_hash["security"]["hash_verified_before_content"])
        self.assertFalse(missing_hash["security"]["payload_embedded"])
        self.assertNotIn("content", missing_hash)

        readme_hash = self.server.hashlib.sha256((self.repo_path / "README.md").read_bytes()).hexdigest()
        missing_reference_id = self.server.result_reference_resolve(
            path="README.md",
            expected_hash=readme_hash,
            include_content=True,
        )
        self.assertFalse(missing_reference_id["ok"])
        self.assertEqual(missing_reference_id["status"], "invalid_reference")
        self.assertNotIn("content", missing_reference_id)

        explicit = self.server.result_reference_resolve(
            reference_id="manual-readme",
            path="README.md",
            expected_hash=readme_hash,
            include_content=False,
        )
        validate_against_schema(explicit, RESULT_REFERENCE_RESOLVE_SCHEMA)
        self.assertTrue(explicit["ok"])
        self.assertEqual(explicit["status"], "resolved")
        self.assertTrue(explicit["security"]["hash_verified_before_content"])
        self.assertFalse(explicit["security"]["payload_embedded"])
        self.assertNotIn("content", explicit)

    def test_result_modes_preserve_default_inline_and_support_summary_for_two_reports(self):
        inline_self = self.server.self_optimization_report()
        self.assertEqual(inline_self["schema"], "self_optimization_report.v1")
        self.assertIn("metrics", inline_self)
        self.assertNotIn("result_mode", inline_self)

        summary_self = self.server.self_optimization_report(result_mode="summary")
        self.assertEqual(summary_self["result_mode"], "summary")
        self.assertNotIn("metrics", summary_self)
        self.assertNotIn("result_reference", summary_self)

        reference_self = self.server.self_optimization_report(result_mode="reference")
        self.assertEqual(reference_self["result_mode"], "reference")
        self.assertNotIn("metrics", reference_self)
        self.assertIn("result_reference", reference_self)

        inline_governance = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=False)
        self.assertEqual(inline_governance["schema"], "governance_report.v1")
        self.assertIn("audit", inline_governance)

        summary_governance = self.server.governance_report(
            base_ref="HEAD", head_ref="HEAD", export=False, result_mode="summary"
        )
        self.assertEqual(summary_governance["result_mode"], "summary")
        self.assertNotIn("audit", summary_governance)

        reference_governance = self.server.governance_report(
            base_ref="HEAD", head_ref="HEAD", export=False, result_mode="reference"
        )
        self.assertEqual(reference_governance["result_mode"], "reference")
        self.assertNotIn("audit", reference_governance)
        self.assertIn("result_reference", reference_governance)

        mode_payloads = {
            "self_optimization_report:inline": inline_self,
            "self_optimization_report:summary": summary_self,
            "self_optimization_report:reference": reference_self,
            "governance_report:inline": inline_governance,
            "governance_report:summary": summary_governance,
            "governance_report:reference": reference_governance,
        }
        for case_name, payload in mode_payloads.items():
            tool_name = case_name.split(":", 1)[0]
            with self.subTest(case_name=case_name):
                validate_against_schema(payload, TOOL_OUTPUT_SCHEMAS[tool_name])

    def test_resource_link_schema_validates_generated_artifacts(self):
        report = self.server.governance_report(base_ref="HEAD", head_ref="HEAD", export=True)
        self.assertGreaterEqual(len(report["resource_links"]), 2)
        for link in report["resource_links"]:
            validate_against_schema(link, RESOURCE_LINK_SCHEMA)
            self.assertFalse(link["safety"]["contains_secrets"])
            self.assertNotIn(str(self.repo_path), self.server.json.dumps(link))

        (self.repo_path / "snapshot-change.txt").write_text("changed\n", encoding="utf-8")
        snapshot = self.server.state_snapshot(label="schema-contract")
        validate_against_schema(snapshot, STATE_SNAPSHOT_OUTPUT_SCHEMA)
        self.assertGreaterEqual(len(snapshot["resource_links"]), 2)
        self.assertTrue(any(link["uri"].startswith("git-ref://") for link in snapshot["resource_links"]))
        for link in snapshot["resource_links"]:
            validate_against_schema(link, RESOURCE_LINK_SCHEMA)
            self.assertFalse(link["safety"]["contains_secrets"])
            self.assertNotIn(str(self.repo_path), self.server.json.dumps(link))

    def test_fastmcp_advertises_checked_in_output_schemas(self):
        listed = asyncio.run(self.server.mcp.list_tools())
        by_name = {tool.name: tool for tool in listed}
        for tool_name in SCHEMA_BACKED_TOOL_NAMES:
            with self.subTest(tool_name=tool_name):
                self.assertIn(tool_name, by_name)
                advertised = by_name[tool_name].outputSchema
                checked_in = TOOL_OUTPUT_SCHEMAS[tool_name]
                self.assertEqual(advertised.get("type"), "object")
                if checked_in.get("type") == "object":
                    self.assertEqual(advertised, checked_in)
                else:
                    self.assertEqual(
                        advertised.get("x-codebase-tooling-mcp-legacy-output-schema"),
                        checked_in,
                    )
                    self.assertEqual(advertised.get("properties", {}).get("result"), checked_in)

    def test_shared_error_envelope_validates_for_each_schema_backed_tool(self):
        for tool_name in SCHEMA_BACKED_TOOL_NAMES:
            with self.subTest(tool_name=tool_name):
                payload = make_tool_error(tool_name, ValueError("bad input"))
                validate_against_schema(payload, ERROR_OUTPUT_SCHEMA)

    def test_structured_result_preserves_text_json_for_legacy_clients(self):
        payload = self.server.grep(pattern="alpha", path="src", summary_mode="quick")
        envelope = structured_tool_result("grep", payload)
        validate_against_schema(envelope["structuredContent"], TOOL_OUTPUT_SCHEMAS["grep"])
        self.assertIn("structuredContent", envelope)
        self.assertEqual(envelope["content"][0]["type"], "text")
        self.assertIn("grep.quick.v1", envelope["content"][0]["text"])
