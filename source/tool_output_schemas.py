# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Schema-first MCP output contracts for agent-critical tools.

The schemas in this module are intentionally conservative: they describe stable
fields clients may rely on while allowing selected experimental fields through
``additionalProperties``.  Tool implementations can continue to return richer
payloads without breaking clients that validate against these contracts.
"""

from __future__ import annotations

import json
from typing import Any

SCHEMA_VERSION = "tool_output_contracts.v1"


SCHEMA_BACKED_TOOL_NAMES: tuple[str, ...] = (
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
    "skill_privilege_scope",
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
    "workflow_lineage",
    "interaction_invariant_audit",
    "mutation_step_guard",
)

STABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "repo_info": ("repo_path", "repo_exists", "is_git_repo", "allow_mutations", "transport"),
    "roots_diagnostics": ("schema", "read_only", "advisory_only", "server_repo", "fetch", "roots", "relationship", "guidance"),
    "model_assisted_summary": ("schema", "ok", "status", "read_only", "advisory_only", "purpose", "policy", "capability", "request", "audit"),
    "runtime_state": ("schema", "timestamp", "transport", "server", "sse", "ollama", "docker", "dependency_locks"),
    "git_status": ("status", "short"),
    "grep": ("path", "line", "column", "match"),
    "find_paths": ("<array item: repository-relative path>",),
    "read_snippet": ("path", "start_line", "end_line", "content"),
    "summarize_diff": ("file_count", "total_added", "total_deleted", "risk_flags"),
    "risk_scoring": ("risk_score", "risk_level", "reasons", "summary"),
    "workspace_transaction": ("schema", "mode", "result"),
    "policy_simulator": ("schema", "ok", "blocking_policies", "docs", "security", "risk", "license"),
    "workflow_policy_plan": ("schema", "read_only", "executed_plan", "decision", "ok", "plan_id", "blocking_policies", "required_preconditions", "findings", "safe_next_actions"),
    "policy_governance_decision": ("schema", "read_only", "executed_plan", "decision", "ok", "decision_id", "policy_bundle", "matched_rule_ids", "decision_counts", "rule_results", "findings", "safe_next_actions"),
    "skill_privilege_scope": ("schema", "read_only", "executed_imported_code", "external_services_called", "decision", "ok", "summary", "action_nodes", "advisory_blockers", "required_privileges"),
    "workflow_reminder": ("schema", "read_only", "advisory_only", "emitted", "trigger", "remembered_constraints", "required_next_gate", "safe_next_actions", "suppress_if_already_satisfied", "evidence"),
    "workflow_task": ("schema", "task_id", "workflow", "status", "state", "retry_policy", "retry_after_seconds", "max_attempts", "attempt", "last_error_class", "no_auto_retry", "expires_at", "retention_seconds", "expired_result_action", "audit_events"),
    "task_status": ("schema", "task_id", "workflow", "status", "state", "retry_policy", "retry_after_seconds", "max_attempts", "attempt", "last_error_class", "no_auto_retry", "expires_at", "retention_seconds", "expired_result_action", "audit_events"),
    "clarification_gate": ("schema", "ok_to_continue", "status", "missing_fields", "questions", "fallback_checklist", "elicitation"),
    "release_readiness": ("schema", "base_ref", "head_ref", "ok", "checks"),
    "agent_quality_delta": ("schema", "base_ref", "head_ref", "ok", "decision", "summary", "static_analysis", "complexity", "policy", "provenance"),
    "tool_catalog_integrity": ("schema", "ok", "status", "baseline", "current", "drift", "lint", "draft_tool_metadata_lint", "security"),
    "dependency_security_report": ("schema", "report_id", "generated_at", "status", "ok", "summary", "components", "vulnerabilities", "advisory", "gate", "exports", "resource_links"),
    "ci_workflow_security_report": ("schema", "report_id", "generated_at", "status", "ok", "summary", "findings", "workflows", "exports"),
    "secret_exposure_report": ("schema", "report_id", "generated_at", "status", "ok", "summary", "findings", "gate", "exports"),
    "agent_security_delta": ("schema", "report_id", "generated_at", "base_ref", "head_ref", "status", "ok", "summary", "findings", "gate", "exports"),
    "agent_security_delta_report": ("schema", "report_id", "generated_at", "base_ref", "head_ref", "status", "ok", "summary", "findings", "gate", "exports"),
    "mcp_threat_model_report": ("schema", "report_id", "generated_at", "status", "ok", "summary", "components", "trust_boundaries", "dread_rubric", "threats", "findings", "exports"),
    "governance_report": ("schema", "report_id", "generated_at"),
    "memory_governance_report": ("schema", "generated_at", "read_only", "policy_version", "summary", "stores", "findings", "security"),
    "self_optimization_report": ("schema", "report_id", "generated_at"),
    "observation_compression_report": ("schema", "report_id", "generated_at", "read_only", "advisory_only", "window", "summary", "classification_buckets", "observations", "fingerprints", "compression_opportunities", "low_confidence_caveats", "security"),
    "agents_context_health": ("schema", "read_only", "advisory_only", "target", "budget", "summary", "instruction_categories", "duplicate_guidance", "stale_guidance", "risky_global_instructions", "move_candidates", "safety"),
    "artifact_provenance": ("schema", "provenance_schema", "attestation_schema", "artifact_count", "ok", "checks"),
    "result_reference_resolve": ("schema", "ok", "status", "read_only", "reference_id", "artifact", "security"),
    "workflow_diagnostics": ("schema", "ok", "critical_failure_step", "failure_category", "constraint_violations", "evidence", "confidence", "recommended_followup", "redactions_applied"),
    "workflow_lineage": ("schema", "read_only", "manifest_path", "plan_id", "status", "ok", "checks", "conditions"),
    "interaction_invariant_audit": ("schema", "read_only", "advisory_only", "ok_to_continue", "confidence", "extracted_invariants", "suspected_smells", "safe_next_actions", "linked_gates"),
    "mutation_step_guard": ("schema", "read_only", "ok_to_mutate", "decision", "decision_flags", "decisive_deviation_risk", "missing_preconditions", "targeted_reflection_checklist", "safe_next_actions"),
}

EXPERIMENTAL_FIELDS: dict[str, tuple[str, ...]] = {
    "repo_info": ("docker", "current_branch", "head", "dirty", "max_read_bytes", "max_output_chars"),
    "roots_diagnostics": ("safety", "roots.items", "relationship.per_root_relationships"),
    "model_assisted_summary": ("execution_mode", "execution_mode_source", "context", "sampling", "guidance"),
    "runtime_state": ("server.python_server_processes", "ollama.tags_probe", "dependency_locks.sections"),
    "git_status": ("raw",),
    "grep": ("lineText", "schema", "total_matches", "returned", "paths", "result_id", "count", "compressed_observation", "prompt_injection_signals", "_meta"),
    "find_paths": (),
    "read_snippet": ("requested_start_line", "requested_end_line", "total_lines", "prompt_injection_signals", "_meta"),
    "summarize_diff": ("files", "files_sorted_by_churn", "patch", "patch_unified", "prompt_injection_signals", "_meta"),
    "risk_scoring": ("untrusted_content_signals",),
    "workspace_transaction": ("resource_links", "_meta"),
    "policy_simulator": (),
    "workflow_policy_plan": ("intent", "execution_mode", "allowed_targets", "data_classification", "step_count", "steps", "least_privilege_scope", "security"),
    "policy_governance_decision": ("intent", "execution_mode", "data_classification", "step_count", "bundle_id", "bundle_version", "authoritative_hard_gates", "workflow_policy_plan", "least_privilege_scope", "security"),
    "skill_privilege_scope": ("intent", "item_id", "suggested_constraints", "security"),
    "workflow_reminder": ("reminder_id", "input_summary", "security"),
    "workflow_task": ("started", "created_at", "started_at", "updated_at", "finished_at", "retention_expires_at", "retry_of", "replay", "idempotency_key", "result", "result_expired", "result_available", "safe_next_actions", "progress", "progress_detail", "arguments", "artifact_references", "resource_links", "cancellation", "security", "_meta"),
    "task_status": ("started", "created_at", "started_at", "updated_at", "finished_at", "retention_expires_at", "retry_of", "replay", "idempotency_key", "result", "result_expired", "result_available", "safe_next_actions", "progress", "progress_detail", "arguments", "artifact_references", "resource_links", "cancellation", "security", "_meta"),
    "clarification_gate": ("audit", "inputs", "decision_reasons"),
    "release_readiness": ("started_at", "finished_at", "mcp_apps"),
    "agent_quality_delta": ("read_only", "advisory_only", "changed_files", "hints", "static_analysis.findings", "provenance.patch_survivorship"),
    "tool_catalog_integrity": ("read_only", "baseline.per_tool", "current.per_tool", "drift.changed[].metadata_diff", "lint.findings", "draft_tool_metadata_lint.findings"),
    "dependency_security_report": ("inputs", "skipped", "warnings", "security", "provenance", "_meta"),
    "ci_workflow_security_report": ("findings_by_severity", "suppressed_findings", "suppressions", "config", "action_uses", "security", "resource_links", "provenance", "_meta"),
    "secret_exposure_report": ("inputs", "rules", "allowlist", "skipped", "security", "resource_links", "_meta"),
    "agent_security_delta": ("read_only", "changed_files", "skipped", "security", "resource_links", "provenance", "_meta"),
    "agent_security_delta_report": ("read_only", "changed_files", "skipped", "security", "resource_links", "provenance", "_meta"),
    "mcp_threat_model_report": ("controls", "baseline", "fixtures", "security", "resource_links", "_meta"),
    "governance_report": ("audit", "governance_hooks", "agents_context_health", "exports", "resource_links", "summary", "window", "git", "snapshots", "security", "workflow_diagnostics", "tool_catalog_integrity", "ci_workflow_security", "agent_security_delta", "secret_exposure", "untrusted_content_signals", "memory_governance", "result_references", "result_mode", "result_reference", "lineage", "provenance", "compressed_observation", "_meta"),
    "memory_governance_report": ("inputs", "entries", "advisory_only"),
    "self_optimization_report": ("window", "summary", "metrics", "optimization_candidates", "optimization_integrity_report", "agents_context_health", "security", "sources", "bottlenecks", "usage_guidance", "resource_links", "exports", "confidence", "caveats", "github_issue_gate", "patch_survivorship", "optimization_candidates[].anti_gaming", "metrics.observation_compression", "result_mode", "result_reference", "_meta"),
    "observation_compression_report": ("sources", "resource_links", "exports", "markdown", "_meta"),
    "agents_context_health": ("findings", "summary.severity_counts", "budget.remaining_bytes", "budget.remaining_tokens"),
    "artifact_provenance": ("checks[].attestation",),
    "result_reference_resolve": ("producer_tool", "summary", "message", "content", "content_encoding"),
    "workflow_diagnostics": ("audit_source", "read_only", "security", "trajectory", "failure_categories", "critical_step_candidate", "safe_next_actions", "failure_taxonomy", "llm_judging"),
    "workflow_lineage": ("mode", "security"),
    "interaction_invariant_audit": ("security", "redactions_applied", "input_summary"),
    "mutation_step_guard": ("input_summary", "security"),
}


def _object_schema(
    required: list[str],
    properties: dict[str, Any],
    *,
    additional_properties: bool = True,
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": additional_properties,
    }


PROMPT_INJECTION_SIGNAL_SCHEMA: dict[str, Any] = _object_schema(
    ["schema", "detected", "non_blocking", "summary", "evidence", "redaction"],
    {
        "schema": {"type": "string", "const": "prompt_injection_signals.v1"},
        "detected": {"type": "boolean"},
        "non_blocking": {"type": "boolean"},
        "policy": {"type": "string"},
        "input": {"type": "object"},
        "summary": {"type": "object"},
        "evidence": {"type": "array", "items": {"type": "object"}},
        "bounds": {"type": "object"},
        "redaction": {"type": "object"},
    },
)

PROMPT_INJECTION_SIGNAL_COUNTS_SCHEMA: dict[str, Any] = _object_schema(
    ["schema", "detected", "total_signals", "category_counts", "severity", "non_blocking"],
    {
        "schema": {"type": "string", "const": "prompt_injection_signal_counts.v1"},
        "detected": {"type": "boolean"},
        "total_signals": {"type": "integer"},
        "category_counts": {"type": "object"},
        "severity": {"type": "string"},
        "non_blocking": {"type": "boolean"},
        "privacy": {"type": "object"},
    },
)

UNTRUSTED_CONTENT_METADATA_SCHEMA: dict[str, Any] = _object_schema(
    ["untrusted_content"],
    {
        "untrusted_content": _object_schema(
            ["schema", "treat_as", "non_blocking_default", "prompt_injection_signals"],
            {
                "schema": {"type": "string", "const": "untrusted_content_metadata.v1"},
                "treat_as": {"type": "string"},
                "non_blocking_default": {"type": "boolean"},
                "prompt_injection_signals": PROMPT_INJECTION_SIGNAL_SCHEMA,
            },
        )
    },
)

COMPRESSED_OBSERVATION_SCHEMA: dict[str, Any] = _object_schema(
    ["schema", "summary", "preserved_signals", "omitted", "raw_reference", "rules", "provenance", "redaction"],
    {
        "schema": {"type": "string", "const": "compressed_observation.v1"},
        "summary": {"type": "string"},
        "preserved_signals": {"type": "object"},
        "omitted": {"type": "array", "items": {"type": "object"}},
        "raw_reference": {"type": "object"},
        "rules": _object_schema(
            ["rule_set", "version", "deterministic"],
            {
                "rule_set": {"type": "string"},
                "version": {"type": "integer"},
                "deterministic": {"type": "boolean"},
                "max_preserved_signals": {"type": "integer"},
            },
        ),
        "provenance": _object_schema(
            ["tool", "generated_by", "input_scope"],
            {
                "tool": {"type": "string"},
                "generated_by": {"type": "string"},
                "input_scope": {"type": "string"},
            },
        ),
        "redaction": _object_schema(
            ["applied", "method", "contains_secrets"],
            {
                "applied": {"type": "boolean"},
                "method": {"type": "string"},
                "contains_secrets": {"type": "boolean"},
            },
        ),
    },
)


RESOURCE_LINK_SCHEMA: dict[str, Any] = _object_schema(
    ["schema", "title", "uri", "mime_type", "created_at", "safety"],
    {
        "schema": {"type": "string", "const": "artifact_resource_link.v1"},
        "title": {"type": "string"},
        "uri": {"type": "string"},
        "path": {"type": "string"},
        "mime_type": {"type": "string"},
        "size_bytes": {"type": "integer"},
        "created_at": {"type": "string"},
        "safety": _object_schema(
            ["redacted", "contains_secrets", "repo_boundary_enforced", "note"],
            {
                "redacted": {"type": "boolean"},
                "contains_secrets": {"type": "boolean"},
                "repo_boundary_enforced": {"type": "boolean"},
                "note": {"type": "string"},
            },
        ),
    },
)

RESULT_REFERENCE_SCHEMA: dict[str, Any] = _object_schema(
    ["schema", "reference_id", "producer_tool", "created_at", "expires_at", "summary", "content", "sensitivity", "resolver"],
    {
        "schema": {"type": "string", "const": "mcp_result_reference.v1"},
        "reference_id": {"type": "string"},
        "result_mode": {"type": "string", "enum": ["reference"]},
        "producer_tool": {"type": "string"},
        "created_at": {"type": "string"},
        "expires_at": {"type": "string"},
        "retention": {"type": "object"},
        "summary": {"type": "object"},
        "content": _object_schema(
            ["content_type", "mime_type", "size_bytes", "hash"],
            {
                "content_type": {"type": "string"},
                "mime_type": {"type": "string"},
                "encoding": {"type": "string"},
                "size_bytes": {"type": "integer"},
                "hash": {"type": "object"},
            },
        ),
        "sensitivity": {"type": "object"},
        "resolver": {"type": "object"},
    },
)

RESULT_REFERENCE_RESOLVE_SCHEMA: dict[str, Any] = _object_schema(
    ["schema", "ok", "status", "read_only", "reference_id", "artifact", "security"],
    {
        "schema": {"type": "string", "const": "mcp_result_reference.resolve.v1"},
        "ok": {"type": "boolean"},
        "status": {"type": "string", "enum": ["resolved", "expired", "missing", "boundary_rejected", "hash_mismatch", "invalid_reference"]},
        "read_only": {"type": "boolean", "const": True},
        "reference_id": {"type": "string"},
        "producer_tool": {"type": "string"},
        "summary": {"type": "object"},
        "artifact": {"type": "object"},
        "message": {"type": "string"},
        "content": {"type": "string"},
        "content_encoding": {"type": "string"},
        "security": {"type": "object"},
    },
)

WORKFLOW_TASK_STATUS_OUTPUT_SCHEMA: dict[str, Any] = _object_schema(
    [
        "schema",
        "task_id",
        "workflow",
        "status",
        "state",
        "retry_policy",
        "retry_after_seconds",
        "max_attempts",
        "attempt",
        "last_error_class",
        "no_auto_retry",
        "expires_at",
        "retention_seconds",
        "expired_result_action",
        "audit_events",
    ],
    {
        "schema": {"type": "string", "const": "workflow_task.v1"},
        "task_id": {"type": "string"},
        "workflow": {"type": "string"},
        "status": {
            "type": "string",
            "enum": [
                "pending",
                "running",
                "cancel_requested",
                "succeeded",
                "failed",
                "cancelled",
                "expired",
            ],
        },
        "state": {"type": "string"},
        "started": {"type": "boolean"},
        "ok": {"type": "boolean"},
        "retry_policy": {
            "type": "string",
            "enum": ["none", "client_may_retry", "server_retrying", "human_required"],
        },
        "retry_after_seconds": {"type": "integer", "minimum": 0},
        "max_attempts": {"type": "integer", "minimum": 1},
        "attempt": {"type": "integer", "minimum": 0},
        "last_error_class": {"type": "string"},
        "idempotency_key": {"type": "string"},
        "replay": {"type": "object"},
        "no_auto_retry": {"type": "boolean"},
        "created_at": {"type": "string"},
        "started_at": {"type": "string"},
        "updated_at": {"type": "string"},
        "finished_at": {"type": "string"},
        "expires_at": {"type": "string"},
        "retention_seconds": {"type": "integer", "minimum": 1},
        "retention_expires_at": {"type": "string"},
        "expired_result_action": {
            "type": "string",
            "enum": ["rerun_allowed", "rerun_requires_approval", "unavailable"],
        },
        "result_expired": {"type": "boolean"},
        "result_available": {"type": "boolean"},
        "safe_next_actions": {"type": "array", "items": {"type": "string"}},
        "retry_of": {"type": "string"},
        "progress": {"type": "number"},
        "progress_detail": {"type": "object"},
        "arguments": {"type": "object"},
        "result": {"type": "object"},
        "artifact_references": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
        "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
        "cancellation": {"type": "object"},
        "audit_events": {"type": "array", "items": {"type": "object"}},
        "security": {"type": "object"},
        "_meta": {"type": "object"},
    },
)

STATE_SNAPSHOT_OUTPUT_SCHEMA: dict[str, Any] = _object_schema(
    ["schema", "snapshot_id", "backend", "base_head", "stash_commit", "stash_ref", "had_changes", "resource_links", "_meta"],
    {
        "schema": {"type": "string", "const": "state_snapshot.v1"},
        "snapshot_id": {"type": "string"},
        "backend": {"type": "string"},
        "base_head": {"type": "string"},
        "stash_commit": {"type": "string"},
        "stash_ref": {"type": "string"},
        "had_changes": {"type": "boolean"},
        "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
        "_meta": {"type": "object"},
    },
)

ERROR_OUTPUT_SCHEMA: dict[str, Any] = _object_schema(
    ["ok", "error"],
    {
        "ok": {"type": "boolean", "const": False},
        "error": _object_schema(
            ["type", "message"],
            {
                "type": {"type": "string"},
                "message": {"type": "string"},
                "tool": {"type": "string"},
                "retryable": {"type": "boolean"},
            },
        ),
    },
)

TOOL_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "repo_info": _object_schema(
        ["repo_path", "repo_exists", "is_git_repo", "allow_mutations", "transport"],
        {
            "repo_path": {"type": "string"},
            "repo_exists": {"type": "boolean"},
            "is_git_repo": {"type": "boolean"},
            "allow_mutations": {"type": "boolean"},
            "transport": {"type": "string"},
            "max_read_bytes": {"type": "integer"},
            "max_output_chars": {"type": "integer"},
            "docker": {"type": "object"},
            "current_branch": {"type": "string"},
            "head": {"type": "string"},
            "dirty": {"type": "boolean"},
        },
    ),
    "roots_diagnostics": _object_schema(
        ["schema", "read_only", "advisory_only", "server_repo", "fetch", "roots", "relationship", "guidance"],
        {
            "schema": {"type": "string", "const": "roots_diagnostics.v1"},
            "read_only": {"type": "boolean", "const": True},
            "advisory_only": {"type": "boolean", "const": True},
            "repo_boundary_enforced": {"type": "boolean"},
            "server_repo": {"type": "object"},
            "fetch": {"type": "object"},
            "roots": {"type": "object"},
            "relationship": {"type": "object"},
            "guidance": {"type": "array", "items": {"type": "string"}},
            "safety": {"type": "object"},
        },
    ),
    "model_assisted_summary": _object_schema(
        ["schema", "ok", "status", "read_only", "advisory_only", "purpose", "policy", "capability", "request", "audit"],
        {
            "schema": {"type": "string", "const": "model_assisted_summary.v1"},
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "reason": {"type": "string"},
            "read_only": {"type": "boolean", "const": True},
            "advisory_only": {"type": "boolean", "const": True},
            "purpose": {"type": "string"},
            "execution_mode": {"type": "string"},
            "execution_mode_source": {"type": "string"},
            "policy": {"type": "object"},
            "capability": {"type": "object"},
            "context": {"type": "object"},
            "request": {"type": "object"},
            "sampling": {"type": "object"},
            "audit": {"type": "object"},
            "guidance": {"type": "array", "items": {"type": "string"}},
        },
    ),
    "runtime_state": _object_schema(
        ["schema", "timestamp", "transport", "server", "sse", "ollama", "docker", "dependency_locks"],
        {
            "schema": {"type": "string", "const": "runtime_state.v1"},
            "timestamp": {"type": "string"},
            "transport": {"type": "string"},
            "server": {"type": "object"},
            "sse": {"type": "object"},
            "ollama": {"type": "object"},
            "docker": {"type": "object"},
            "dependency_locks": {"type": "object"},
        },
    ),
    "git_status": _object_schema(
        ["status", "short"],
        {
            "status": {"type": "array", "items": {"type": "string"}},
            "short": {"type": "boolean"},
            "raw": {"type": "string"},
        },
    ),
    "grep": {
        "type": "array",
        "items": _object_schema(
            [],
            {
                "path": {"type": "string"},
                "line": {"type": "integer"},
                "column": {"type": "integer"},
                "match": {"type": "string"},
                "lineText": {"type": "string"},
                "schema": {"type": "string"},
                "total_matches": {"type": "integer"},
                "returned": {"type": "integer"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "result_id": {"type": "string"},
                "count": {"type": "integer"},
                "results": {"type": "array", "items": {"type": "object"}},
                "compressed_observation": COMPRESSED_OBSERVATION_SCHEMA,
                "prompt_injection_signals": PROMPT_INJECTION_SIGNAL_SCHEMA,
                "_meta": UNTRUSTED_CONTENT_METADATA_SCHEMA,
            },
        ),
    },
    "find_paths": {"type": "array", "items": {"type": "string"}},
    "read_snippet": _object_schema(
        ["path", "start_line", "end_line", "content"],
        {
            "path": {"type": "string"},
            "requested_start_line": {"type": "integer"},
            "requested_end_line": {"type": "integer"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
            "total_lines": {"type": "integer"},
            "content": {"type": "string"},
            "prompt_injection_signals": PROMPT_INJECTION_SIGNAL_SCHEMA,
            "_meta": UNTRUSTED_CONTENT_METADATA_SCHEMA,
        },
    ),
    "summarize_diff": _object_schema(
        ["file_count", "total_added", "total_deleted", "risk_flags"],
        {
            "file_count": {"type": "integer"},
            "total_added": {"type": "integer"},
            "total_deleted": {"type": "integer"},
            "files": {"type": "array", "items": {"type": "object"}},
            "risk_flags": {"type": "object"},
            "files_sorted_by_churn": {"type": "array", "items": {"type": "object"}},
            "patch": {"type": "string"},
            "patch_unified": {"type": "integer"},
            "prompt_injection_signals": PROMPT_INJECTION_SIGNAL_SCHEMA,
            "_meta": UNTRUSTED_CONTENT_METADATA_SCHEMA,
        },
    ),
    "risk_scoring": _object_schema(
        ["risk_score", "risk_level", "reasons", "summary"],
        {
            "risk_score": {"type": "integer"},
            "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "object"},
            "untrusted_content_signals": PROMPT_INJECTION_SIGNAL_COUNTS_SCHEMA,
        },
    ),
    "workspace_transaction": _object_schema(
        ["schema", "mode", "result"],
        {
            "schema": {"type": "string", "const": "workspace_transaction.v1"},
            "mode": {"type": "string"},
            "result": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "compressed_observation": COMPRESSED_OBSERVATION_SCHEMA,
            "_meta": {"type": "object"},
        },
    ),
    "policy_simulator": _object_schema(
        ["schema", "ok", "blocking_policies", "docs", "security", "risk", "license"],
        {
            "schema": {"type": "string", "const": "policy_simulator.v1"},
            "ok": {"type": "boolean"},
            "blocking_policies": {"type": "array", "items": {"type": "string"}},
            "docs": {"type": "object"},
            "security": {"type": "object"},
            "risk": {"type": "object"},
            "license": {"type": "object"},
        },
    ),
    "workflow_policy_plan": _object_schema(
        ["schema", "read_only", "executed_plan", "decision", "ok", "plan_id", "blocking_policies", "required_preconditions", "findings", "safe_next_actions"],
        {
            "schema": {"type": "string", "const": "workflow_policy_plan.v1"},
            "read_only": {"type": "boolean", "const": True},
            "executed_plan": {"type": "boolean", "const": False},
            "decision": {"type": "string", "enum": ["allow", "deny", "needs_approval", "needs_clarification"]},
            "ok": {"type": "boolean"},
            "plan_id": {"type": "string"},
            "intent": {"type": "string"},
            "execution_mode": {"type": "string"},
            "allowed_targets": {"type": "array", "items": {"type": "string"}},
            "data_classification": {"type": "string"},
            "step_count": {"type": "integer"},
            "steps": {"type": "array", "items": {"type": "object"}},
            "blocking_policies": {"type": "array", "items": {"type": "string"}},
            "required_preconditions": {"type": "array", "items": {"type": "object"}},
            "findings": {"type": "array", "items": {"type": "object"}},
            "least_privilege_scope": {"type": "object"},
            "safe_next_actions": {"type": "array", "items": {"type": "string"}},
            "security": {"type": "object"},
        },
    ),
    "policy_governance_decision": _object_schema(
        ["schema", "read_only", "executed_plan", "decision", "ok", "decision_id", "policy_bundle", "matched_rule_ids", "decision_counts", "rule_results", "findings", "safe_next_actions"],
        {
            "schema": {"type": "string", "const": "policy_governance_decision.v1"},
            "read_only": {"type": "boolean", "const": True},
            "executed_plan": {"type": "boolean", "const": False},
            "decision": {"type": "string", "enum": ["allow", "deny", "requires_approval"]},
            "ok": {"type": "boolean"},
            "decision_id": {"type": "string"},
            "intent": {"type": "string"},
            "execution_mode": {"type": "string"},
            "data_classification": {"type": "string"},
            "step_count": {"type": "integer"},
            "policy_bundle": {"type": "object"},
            "bundle_id": {"type": "string"},
            "bundle_version": {"type": "string"},
            "matched_rule_ids": {"type": "array", "items": {"type": "string"}},
            "decision_counts": {"type": "object"},
            "rule_results": {"type": "array", "items": {"type": "object"}},
            "findings": {"type": "array", "items": {"type": "object"}},
            "safe_next_actions": {"type": "array", "items": {"type": "string"}},
            "authoritative_hard_gates": {"type": "array", "items": {"type": "string"}},
            "workflow_policy_plan": {"type": "object"},
            "least_privilege_scope": {"type": "object"},
            "security": {"type": "object"},
        },
    ),
    "skill_privilege_scope": _object_schema(
        ["schema", "read_only", "executed_imported_code", "external_services_called", "decision", "ok", "summary", "action_nodes", "advisory_blockers", "required_privileges"],
        {
            "schema": {"type": "string", "const": "skill_privilege_scope.v1"},
            "read_only": {"type": "boolean", "const": True},
            "executed_imported_code": {"type": "boolean", "const": False},
            "external_services_called": {"type": "boolean", "const": False},
            "intent": {"type": "string"},
            "item_id": {"type": "string"},
            "decision": {"type": "string", "enum": ["allow", "allow_with_warnings", "advisory_block"]},
            "ok": {"type": "boolean"},
            "summary": {"type": "object"},
            "action_nodes": {"type": "array", "items": {"type": "object"}},
            "advisory_blockers": {"type": "array", "items": {"type": "object"}},
            "required_privileges": {"type": "array", "items": {"type": "object"}},
            "suggested_constraints": {"type": "array", "items": {"type": "string"}},
            "security": {"type": "object"},
        },
    ),
    "workflow_reminder": _object_schema(
        ["schema", "read_only", "advisory_only", "emitted", "trigger", "remembered_constraints", "required_next_gate", "safe_next_actions", "suppress_if_already_satisfied", "evidence"],
        {
            "schema": {"type": "string", "const": "workflow_reminder.v1"},
            "read_only": {"type": "boolean", "const": True},
            "advisory_only": {"type": "boolean", "const": True},
            "emitted": {"type": "boolean"},
            "reminder_id": {"type": "string"},
            "trigger": {"type": "string"},
            "remembered_constraints": {"type": "array", "items": {"type": "object"}},
            "required_next_gate": {"type": "object"},
            "safe_next_actions": {"type": "array", "items": {"type": "string"}},
            "suppress_if_already_satisfied": {"type": "array", "items": {"type": "object"}},
            "evidence": {"type": "object"},
            "input_summary": {"type": "object"},
            "security": {"type": "object"},
        },
    ),
    "workflow_task": WORKFLOW_TASK_STATUS_OUTPUT_SCHEMA,
    "task_status": WORKFLOW_TASK_STATUS_OUTPUT_SCHEMA,

    "clarification_gate": _object_schema(
        ["schema", "ok_to_continue", "status", "missing_fields", "questions", "fallback_checklist", "elicitation"],
        {
            "schema": {"type": "string", "const": "clarification_gate.v1"},
            "ok_to_continue": {"type": "boolean"},
            "status": {"type": "string", "enum": ["ready", "needs_clarification", "declined", "cancelled"]},
            "missing_fields": {"type": "array", "items": {"type": "object"}},
            "questions": {"type": "array", "items": {"type": "string"}},
            "fallback_checklist": {"type": "array", "items": {"type": "string"}},
            "elicitation": {"type": "object"},
            "audit": {"type": "object"},
            "inputs": {"type": "object"},
            "decision_reasons": {"type": "array", "items": {"type": "string"}},
        },
    ),
    "release_readiness": _object_schema(
        ["schema", "base_ref", "head_ref", "ok", "checks"],
        {
            "schema": {"type": "string", "enum": ["release_readiness.v1", "release_readiness.quick.v1"]},
            "base_ref": {"type": "string"},
            "head_ref": {"type": "string"},
            "started_at": {"type": "string"},
            "finished_at": {"type": "string"},
            "ok": {"type": "boolean"},
            "checks": {"type": "object"},
        },
    ),
    "agent_quality_delta": _object_schema(
        ["schema", "base_ref", "head_ref", "ok", "decision", "summary", "static_analysis", "complexity", "policy", "provenance"],
        {
            "schema": {"type": "string", "const": "agent_quality_delta.v1"},
            "base_ref": {"type": "string"},
            "head_ref": {"type": "string"},
            "read_only": {"type": "boolean", "const": True},
            "advisory_only": {"type": "boolean"},
            "started_at": {"type": "string"},
            "finished_at": {"type": "string"},
            "ok": {"type": "boolean"},
            "decision": {"type": "string", "enum": ["pass", "warn", "block", "review-only"]},
            "summary": {"type": "object"},
            "changed_files": {"type": "array", "items": {"type": "object"}},
            "static_analysis": {"type": "object"},
            "complexity": {"type": "object"},
            "hints": {"type": "array", "items": {"type": "object"}},
            "policy": {"type": "object"},
            "provenance": {"type": "object"},
        },
    ),
    "tool_catalog_integrity": _object_schema(
        ["schema", "ok", "status", "baseline", "current", "drift", "lint", "draft_tool_metadata_lint", "security"],
        {
            "schema": {"type": "string", "const": "tool_catalog_integrity.v1"},
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "read_only": {"type": "boolean", "const": True},
            "baseline": {"type": "object"},
            "current": {"type": "object"},
            "drift": {"type": "object"},
            "lint": {"type": "object"},
            "draft_tool_metadata_lint": {"type": "object"},
            "security": {"type": "object"},
        },
    ),
    "dependency_security_report": _object_schema(
        ["schema", "report_id", "generated_at", "status", "ok", "summary", "components", "vulnerabilities", "advisory", "gate", "exports", "resource_links"],
        {
            "schema": {"type": "string", "const": "dependency_security_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "read_only": {"type": "boolean", "const": True},
            "status": {"type": "string", "enum": ["clean", "vulnerable", "skipped", "stale-cache", "network-disabled", "scanner-unavailable"]},
            "ok": {"type": "boolean"},
            "summary": {"type": "object"},
            "inputs": {"type": "object"},
            "advisory": {"type": "object"},
            "gate": {"type": "object"},
            "components": {"type": "array", "items": {"type": "object"}},
            "vulnerabilities": {"type": "array", "items": {"type": "object"}},
            "skipped": {"type": "array", "items": {"type": "object"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "security": {"type": "object"},
            "exports": {"type": "object"},
            "provenance": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "_meta": {"type": "object"},
        },
    ),
    "ci_workflow_security_report": _object_schema(
        ["schema", "report_id", "generated_at", "status", "ok", "summary", "findings", "workflows", "exports"],
        {
            "schema": {"type": "string", "const": "ci_workflow_security_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "read_only": {"type": "boolean", "const": True},
            "status": {"type": "string", "enum": ["clean", "warnings", "findings", "parse-error", "no-workflows"]},
            "ok": {"type": "boolean"},
            "summary": {"type": "object"},
            "workflows": {"type": "array", "items": {"type": "object"}},
            "findings": {"type": "array", "items": {"type": "object"}},
            "findings_by_severity": {"type": "object"},
            "suppressed_findings": {"type": "array", "items": {"type": "object"}},
            "suppressions": {"type": "object"},
            "config": {"type": "object"},
            "action_uses": {"type": "array", "items": {"type": "object"}},
            "security": {"type": "object"},
            "exports": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
        },
    ),
    "secret_exposure_report": _object_schema(
        ["schema", "report_id", "generated_at", "status", "ok", "summary", "findings", "gate", "exports"],
        {
            "schema": {"type": "string", "const": "secret_exposure_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "read_only": {"type": "boolean", "const": True},
            "status": {"type": "string", "enum": ["clean", "findings", "blocked", "skipped"]},
            "ok": {"type": "boolean"},
            "summary": {"type": "object"},
            "findings": {"type": "array", "items": {"type": "object"}},
            "gate": {"type": "object"},
            "inputs": {"type": "object"},
            "rules": {"type": "array", "items": {"type": "object"}},
            "allowlist": {"type": "object"},
            "skipped": {"type": "array", "items": {"type": "object"}},
            "security": {"type": "object"},
            "exports": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "_meta": {"type": "object"},
        },
    ),
    "agent_security_delta_report": _object_schema(
        ["schema", "report_id", "generated_at", "base_ref", "head_ref", "status", "ok", "summary", "findings", "gate", "exports"],
        {
            "schema": {"type": "string", "const": "agent_security_delta_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "read_only": {"type": "boolean", "const": True},
            "base_ref": {"type": "string"},
            "head_ref": {"type": "string"},
            "status": {"type": "string", "enum": ["pass", "warn", "block"]},
            "ok": {"type": "boolean"},
            "summary": {"type": "object"},
            "changed_files": {"type": "array", "items": {"type": "string"}},
            "findings": {"type": "array", "items": {"type": "object"}},
            "skipped": {"type": "array", "items": {"type": "object"}},
            "gate": {"type": "object"},
            "security": {"type": "object"},
            "exports": {"type": "object"},
            "provenance": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "_meta": {"type": "object"},
        },
    ),
    "mcp_threat_model_report": _object_schema(
        ["schema", "report_id", "generated_at", "status", "ok", "summary", "components", "trust_boundaries", "dread_rubric", "threats", "findings", "exports"],
        {
            "schema": {"type": "string", "const": "mcp_threat_model_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "read_only": {"type": "boolean", "const": True},
            "status": {"type": "string", "enum": ["clean", "findings", "regression"]},
            "ok": {"type": "boolean"},
            "summary": {"type": "object"},
            "components": {"type": "array", "items": {"type": "object"}},
            "trust_boundaries": {"type": "array", "items": {"type": "object"}},
            "controls": {"type": "object"},
            "dread_rubric": {"type": "object"},
            "threats": {"type": "array", "items": {"type": "object"}},
            "findings": {"type": "array", "items": {"type": "object"}},
            "baseline": {"type": "object"},
            "fixtures": {"type": "object"},
            "security": {"type": "object"},
            "exports": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "_meta": {"type": "object"},
        },
    ),
    "governance_report": _object_schema(
        ["schema", "report_id", "generated_at"],
        {
            "schema": {"type": "string", "const": "governance_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "window": {"type": "object"},
            "git": {"type": "object"},
            "audit": {"type": "object"},
            "workflow_diagnostics": {"type": "object"},
            "governance_hooks": {"type": "object"},
            "ci_workflow_security": {"type": "object"},
            "agent_security_delta": {"type": "object"},
            "secret_exposure": {"type": "object"},
            "untrusted_content_signals": {"type": "object"},
            "result_references": {"type": "object"},
            "result_mode": {"type": "string", "enum": ["inline", "summary", "reference"]},
            "result_reference": RESULT_REFERENCE_SCHEMA,
            "agents_context_health": {"type": "object"},
            "snapshots": {"type": "object"},
            "security": {"type": "object"},
            "exports": {"type": "object"},
            "lineage": {"type": "object"},
            "provenance": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "_meta": {"type": "object"},
        },
    ),
    "memory_governance_report": _object_schema(
        ["schema", "generated_at", "read_only", "policy_version", "summary", "stores", "findings", "security"],
        {
            "schema": {"type": "string", "const": "memory_governance_report.v1"},
            "generated_at": {"type": "string"},
            "read_only": {"type": "boolean", "const": True},
            "advisory_only": {"type": "boolean", "const": True},
            "policy_version": {"type": "string"},
            "inputs": {"type": "object"},
            "summary": {"type": "object"},
            "stores": {"type": "array", "items": {"type": "object"}},
            "entries": {"type": "array", "items": {"type": "object"}},
            "findings": {"type": "array", "items": {"type": "object"}},
            "security": {"type": "object"},
        },
    ),
    "self_optimization_report": _object_schema(
        ["schema", "report_id", "generated_at"],
        {
            "schema": {"type": "string", "const": "self_optimization_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "window": {"type": "object"},
            "summary": {"type": "object"},
            "metrics": {"type": "object"},
            "sources": {"type": "object"},
            "confidence": {"type": "string"},
            "caveats": {"type": "array", "items": {"type": "string"}},
            "bottlenecks": {"type": "array", "items": {"type": "object"}},
            "optimization_candidates": {"type": "array", "items": {"type": "object"}},
            "optimization_integrity_report": {"type": "object"},
            "agents_context_health": {"type": "object"},
            "github_issue_gate": {"type": "object"},
            "patch_survivorship": {"type": "object"},
            "usage_guidance": {"type": "object"},
            "security": {"type": "object"},
            "result_mode": {"type": "string", "enum": ["inline", "summary", "reference"]},
            "result_reference": RESULT_REFERENCE_SCHEMA,
            "exports": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "_meta": {"type": "object"},
        },
    ),
    "observation_compression_report": _object_schema(
        ["schema", "report_id", "generated_at", "read_only", "advisory_only", "window", "summary", "classification_buckets", "observations", "fingerprints", "compression_opportunities", "low_confidence_caveats", "security"],
        {
            "schema": {"type": "string", "const": "observation_compression_report.v1"},
            "report_id": {"type": "string"},
            "generated_at": {"type": "string"},
            "read_only": {"type": "boolean", "const": True},
            "advisory_only": {"type": "boolean", "const": True},
            "window": {"type": "object"},
            "summary": {"type": "object"},
            "classification_buckets": {"type": "array", "items": {"type": "object"}},
            "observations": {"type": "array", "items": {"type": "object"}},
            "fingerprints": {"type": "array", "items": {"type": "object"}},
            "compression_opportunities": {"type": "object"},
            "low_confidence_caveats": {"type": "array", "items": {"type": "string"}},
            "sources": {"type": "object"},
            "security": {"type": "object"},
            "exports": {"type": "object"},
            "resource_links": {"type": "array", "items": RESOURCE_LINK_SCHEMA},
            "markdown": {"type": "string"},
            "_meta": {"type": "object"},
        },
    ),
    "agents_context_health": _object_schema(
        ["schema", "read_only", "advisory_only", "target", "budget", "summary", "instruction_categories", "duplicate_guidance", "stale_guidance", "risky_global_instructions", "move_candidates", "safety"],
        {
            "schema": {"type": "string", "const": "agents_context_health.v1"},
            "read_only": {"type": "boolean", "const": True},
            "advisory_only": {"type": "boolean", "const": True},
            "target": {"type": "object"},
            "budget": {"type": "object"},
            "summary": {"type": "object"},
            "instruction_categories": {"type": "array", "items": {"type": "object"}},
            "duplicate_guidance": {"type": "array", "items": {"type": "object"}},
            "stale_guidance": {"type": "array", "items": {"type": "object"}},
            "risky_global_instructions": {"type": "array", "items": {"type": "object"}},
            "move_candidates": {"type": "array", "items": {"type": "object"}},
            "findings": {"type": "array", "items": {"type": "object"}},
            "safety": {"type": "object"},
        },
    ),
    "artifact_provenance": _object_schema(
        ["schema", "provenance_schema", "attestation_schema", "artifact_count", "ok", "checks"],
        {
            "schema": {"type": "string", "const": "artifact_provenance_report.v1"},
            "provenance_schema": {"type": "string", "const": "mcp_artifact_provenance.v1"},
            "attestation_schema": {"type": "string", "const": "mcp_artifact_attestation.v1"},
            "artifact_count": {"type": "integer"},
            "ok": {"type": "boolean"},
            "checks": {"type": "array", "items": {"type": "object"}},
        },
    ),
    "result_reference_resolve": RESULT_REFERENCE_RESOLVE_SCHEMA,
    "workflow_diagnostics": _object_schema(
        ["schema", "ok", "critical_failure_step", "failure_category", "constraint_violations", "evidence", "confidence", "recommended_followup", "redactions_applied"],
        {
            "schema": {"type": "string", "const": "workflow_diagnostics.v1"},
            "ok": {"type": "boolean"},
            "step_count": {"type": "integer"},
            "failed_step_count": {"type": "integer"},
            "constraint_violation_count": {"type": "integer"},
            "failure_taxonomy": {"type": "object"},
            "failure_categories": {"type": "object"},
            "critical_step_candidate": {"type": "object"},
            "critical_failure_step": {"type": "object"},
            "failure_category": {"type": "string"},
            "constraint_violations": {"type": "array", "items": {"type": "object"}},
            "evidence": {"type": "array", "items": {"type": "object"}},
            "confidence": {"type": "number"},
            "recommended_followup": {"type": "array", "items": {"type": "string"}},
            "safe_next_actions": {"type": "array", "items": {"type": "string"}},
            "redactions_applied": {"type": "array", "items": {"type": "string"}},
            "audit_source": {"type": "object"},
            "read_only": {"type": "boolean"},
            "security": {"type": "object"},
            "trajectory": {"type": "array", "items": {"type": "object"}},
            "llm_judging": {"type": "object"},
        },
    ),
    "workflow_lineage": _object_schema(
        ["schema", "read_only", "manifest_path", "plan_id", "status", "ok", "checks", "conditions"],
        {
            "schema": {"type": "string", "const": "workflow_lineage.verify.v1"},
            "mode": {"type": "string", "const": "verify"},
            "read_only": {"type": "boolean", "const": True},
            "manifest_path": {"type": "string"},
            "plan_id": {"type": "string"},
            "status": {"type": "string", "enum": ["matched", "input_changed", "artifact_changed"]},
            "ok": {"type": "boolean"},
            "conditions": {"type": "array", "items": {"type": "string"}},
            "checks": {
                "type": "object",
                "properties": {
                    "plan": {"type": "object"},
                    "artifacts": {"type": "object"},
                    "non_deterministic_nodes": {"type": "array", "items": {"type": "object"}},
                },
                "additionalProperties": True,
            },
            "security": {"type": "object"},
        },
    ),
    "interaction_invariant_audit": _object_schema(
        ["schema", "read_only", "advisory_only", "ok_to_continue", "confidence", "extracted_invariants", "suspected_smells", "safe_next_actions", "linked_gates"],
        {
            "schema": {"type": "string", "const": "interaction_invariant_audit.v1"},
            "read_only": {"type": "boolean", "const": True},
            "advisory_only": {"type": "boolean", "const": True},
            "ok_to_continue": {"type": "boolean"},
            "confidence": {"type": "number"},
            "extracted_invariants": {"type": "array", "items": {"type": "object"}},
            "suspected_smells": {"type": "array", "items": {"type": "object"}},
            "safe_next_actions": {"type": "array", "items": {"type": "string"}},
            "linked_gates": {"type": "object"},
            "redactions_applied": {"type": "array", "items": {"type": "string"}},
            "security": {"type": "object"},
            "input_summary": {"type": "object"},
        },
    ),
    "mutation_step_guard": _object_schema(
        ["schema", "read_only", "ok_to_mutate", "decision", "decision_flags", "decisive_deviation_risk", "missing_preconditions", "targeted_reflection_checklist", "safe_next_actions"],
        {
            "schema": {"type": "string", "const": "mutation_step_guard.v1"},
            "read_only": {"type": "boolean", "const": True},
            "ok_to_mutate": {"type": "boolean"},
            "decision": {"type": "string", "enum": ["allow", "needs_clarification", "needs_snapshot", "needs_fresh_context", "needs_tests", "needs_human_approval", "deny"]},
            "decision_flags": {"type": "object"},
            "decisive_deviation_risk": {"type": "object"},
            "missing_preconditions": {"type": "array", "items": {"type": "object"}},
            "targeted_reflection_checklist": {"type": "array", "items": {"type": "string"}},
            "safe_next_actions": {"type": "array", "items": {"type": "string"}},
            "input_summary": {"type": "object"},
            "security": {"type": "object"},
        },
    ),
}

TOOL_OUTPUT_SCHEMAS["agent_security_delta"] = TOOL_OUTPUT_SCHEMAS["agent_security_delta_report"]


def tool_output_contract(tool_name: str) -> dict[str, Any]:
    """Return the checked-in output contract for a schema-backed tool."""
    if tool_name not in TOOL_OUTPUT_SCHEMAS:
        raise KeyError(f"no output schema registered for tool: {tool_name}")
    return {
        "schema": SCHEMA_VERSION,
        "tool": tool_name,
        "outputSchema": TOOL_OUTPUT_SCHEMAS[tool_name],
        "errorOutputSchema": ERROR_OUTPUT_SCHEMA,
        "stableFields": list(STABLE_FIELDS[tool_name]),
        "experimentalFields": list(EXPERIMENTAL_FIELDS[tool_name]),
    }


def all_tool_output_contracts() -> dict[str, Any]:
    """Return the complete initial schema-backed tool list and contracts."""
    return {
        "schema": SCHEMA_VERSION,
        "tools": [tool_output_contract(name) for name in SCHEMA_BACKED_TOOL_NAMES],
    }


def structured_tool_result(tool_name: str, payload: Any) -> dict[str, Any]:
    """Build an MCP-compatible result preserving text and structuredContent.

    FastMCP can serialize plain dict/list returns, but fixtures and direct clients
    need a deterministic envelope that mirrors the MCP 2025-06-18 result shape:
    human-readable JSON text remains in ``content`` and the same typed object is
    available under ``structuredContent``.
    """
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, sort_keys=True, ensure_ascii=False),
            }
        ],
        "structuredContent": payload,
        "outputSchema": TOOL_OUTPUT_SCHEMAS[tool_name],
    }


def make_tool_error(tool_name: str, exc: Exception | str, *, retryable: bool = False) -> dict[str, Any]:
    """Return the shared documented error envelope for schema-backed tools."""
    return {
        "ok": False,
        "error": {
            "tool": tool_name,
            "type": type(exc).__name__ if isinstance(exc, Exception) else "Error",
            "message": str(exc),
            "retryable": retryable,
        },
    }


def validate_against_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Small JSON-Schema subset validator for contract tests and smoke fixtures."""
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise AssertionError(f"{path}: expected object")
        for key in schema.get("required", []):
            if key not in value:
                raise AssertionError(f"{path}: missing required key {key!r}")
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                validate_against_schema(item, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties", True) is False:
                raise AssertionError(f"{path}: unexpected key {key!r}")
    elif expected == "array":
        if not isinstance(value, list):
            raise AssertionError(f"{path}: expected array")
        item_schema = schema.get("items")
        if item_schema:
            for idx, item in enumerate(value):
                validate_against_schema(item, item_schema, f"{path}[{idx}]")
    elif expected == "string":
        if not isinstance(value, str):
            raise AssertionError(f"{path}: expected string")
    elif expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise AssertionError(f"{path}: expected integer")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise AssertionError(f"{path}: expected boolean")
    elif expected == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise AssertionError(f"{path}: expected number")

    if "const" in schema and value != schema["const"]:
        raise AssertionError(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise AssertionError(f"{path}: expected one of {schema['enum']!r}")
