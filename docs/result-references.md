<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Reference-based result handles

`mcp_result_reference.v1` is an opt-in local handle for large read-only MCP outputs. Use `result_mode="reference"` when a compact summary is enough initially and resolve the full artifact only if needed.

Initial report tools: `governance_report` and `self_optimization_report`. Default `result_mode="inline"` keeps existing behavior. `result_mode="summary"` returns the same compact envelope without creating a handle.

## `mcp_result_reference.v1`

A reference handle contains:

- `schema`, `reference_id`, `producer_tool`, `created_at`, and `expires_at`.
- `summary`: a bounded decision summary safe to include in agent context.
- `content`: `content_type`/`mime_type`, `encoding`, `size_bytes`, and SHA-256 hash metadata.
- `retention`: TTL hours and retention policy aligned with short-lived workflow task artifacts.
- `sensitivity`: redaction level and flags showing the full sensitive payload is not embedded in the handle.
- `resolver`: `tool="result_reference_resolve"`, a local resolver URI, repository resource URI, repository-relative path, and boundary-enforcement flags.

Artifacts are stored under `.codebase-tooling-mcp/reports/result-references/` and contain the original generated report JSON after the producer tool's existing redaction policy has run.

## Resolver behavior

`result_reference_resolve` is read-only. It accepts either the full reference envelope or explicit `reference_id`, repository-relative `path`, and `expected_hash` fields. Resolution order is intentionally defensive:

1. reject malformed references;
2. reject expired references;
3. enforce the repository boundary before reading;
4. report missing artifacts clearly;
5. verify SHA-256 before returning content.

Resolver statuses are `resolved`, `expired`, `missing`, `boundary_rejected`, `hash_mismatch`, and `invalid_reference`. Host absolute paths are not exposed in rejected responses.

## Audit and governance

Handle creation and resolution append audit events with the reference id, producer tool, size/hash prefix, status, expiry, and `payload_embedded=false` metadata. `governance_report` summarizes those events under `result_references` without embedding referenced payloads by default.
