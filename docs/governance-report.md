<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Governance report workflow

`governance_report` is a read-only MCP workflow for enterprise audit review. It reads redacted events from `MCP_AUDIT_LOG_FILE`, summarizes existing governance artifacts, and exports JSON plus Markdown reports under `.codebase-tooling-mcp/reports/`. When `export=true`, it also writes a deterministic, redacted `workflow_lineage.v1` manifest for the governance-report workflow.

The first-slice report schema is `governance_report.v1`. It includes:

- audit counts for sensitive tool calls, blocked attempts, mutation-gate failures, HTTP authorization denials, tool categories, and failure reasons;
- hash-chain digest metadata over redacted audit events (`sha256`, no raw secrets);
- local governance hook summaries from stored `policy_simulator`, `release_readiness`, and `required_tool_chain` results when available;
- latest exported `dependency_security_report` status, vulnerability count, advisory freshness, and report path when available;
- snapshot/rollback references from the state snapshot index when available;
- a compact `workflow_diagnostics` summary for failed audit trajectories when blocked steps are present;
- git base/head metadata for PR or release review;
- `resource_links` plus `_meta.artifact_resources` entries for exported JSON, Markdown, and workflow-lineage files, including repository-relative URI/path, MIME type, size when known, created time, and redaction/safety metadata;
- `lineage` metadata linking to the generated `workflow_lineage.v1` manifest and read-only verifier.

Generated report artifacts also receive local provenance sidecars. The sidecar schema is `mcp_artifact_provenance.v1`, written next to the artifact as `<artifact>.provenance.json`. For example:

- `.codebase-tooling-mcp/reports/governance-report-...json.provenance.json`
- `.codebase-tooling-mcp/reports/governance-report-...md.provenance.json`
- `.codebase-tooling-mcp/reports/governance-report-...workflow-lineage.json.provenance.json`
- `.codebase-tooling-mcp/snapshots/git_snapshots.json.provenance.json` when `state_snapshot` updates the snapshot index

Each sidecar records the artifact path, SHA-256 content digest and size, artifact schema/version when available, generating tool/workflow, invocation timestamp, redacted selected inputs, repository/git metadata, server provenance schema, previous/next artifact links for multi-artifact exports, and a `workflow_lineage` link when a governance-report lineage manifest was emitted.

## Trust model and optional attestations

Unsigned local sidecars remain the default. Existing `mcp_artifact_provenance.v1` sidecars with `signing.signed=false` are still valid local integrity metadata; `artifact_provenance` reports them with attestation status `unsigned`, backend `local-only`, and `network_access=false`.

The optional attestation schema is `mcp_artifact_attestation.v1`, carried in the sidecar `signing` block when `signing.signed=true`. Stable fields are:

- `backend`: selected verifier backend. The only supported backend today is `local-dsse-fixture`.
- `subject_digest`: SHA-256 digest of the artifact subject.
- `signer_identity`: identity string claimed by the trusted fixture signer.
- `bundle_ref` / `envelope_ref`: reference to the attestation bundle or inline DSSE envelope; the fixture backend uses `inline:signing.envelope`.
- `verification.status`: one of `unsigned`, `verified`, `invalid`, `unsupported`, or `unavailable`.
- `envelope`: DSSE-style fixture envelope containing a signed `mcp_artifact_attestation.v1` payload. The payload binds the artifact subject digest and a digest of the provenance sidecar with the envelope removed, so artifact edits and sidecar edits are both detected.

The first supported trust backend is intentionally local/offline fixture verification only. `artifact_provenance` verifies deterministic inline DSSE fixture envelopes without network access, transparency-log access, GitHub API calls, or cosign/Sigstore calls. GitHub Artifact Attestations and Sigstore/cosign verification are future backends behind the same schema; until implemented they are reported as `unsupported`, not fetched.

Example call:

```json
{
  "start_time": "2026-05-12T00:00:00+00:00",
  "end_time": "2026-05-12T23:59:59+00:00",
  "base_ref": "main",
  "head_ref": "HEAD",
  "export": true
}
```

`artifact_provenance` is a read-only verification helper for these sidecars. It can check one artifact path or scan local report/snapshot artifacts, and flags missing sidecars, stale sidecars, digest mismatches, artifact/provenance schema mismatches, invalid fixture attestations, and unsupported attestation backends without mutating artifacts.

`workflow_lineage(mode="verify", manifest_path=".codebase-tooling-mcp/reports/...workflow-lineage.json")` is the first read-only drift verifier for `workflow_lineage.v1`. It recomputes the deterministic governance-report plan identity from redacted request constraints, git refs, audit-source metadata, and audit digest inputs. It reports `matched` when deterministic inputs and observed artifact digests still match, `input_changed` when the plan identity has drifted, `artifact_changed` when recorded artifacts are missing or their digests differ, and `non_deterministic_node` markers for observed outputs that are intentionally not promised as bit-for-bit replay.

The lineage manifest deliberately stores only safe identity inputs: repository-relative paths, redacted audit metadata/digests, git refs/commits, schema versions, deterministic node IDs, and observed artifact digests. It must not store raw prompts, transcript snippets, bearer tokens, secrets, absolute host paths, or file contents. Model-authored or otherwise non-deterministic outputs are represented as observed/non-deterministic nodes rather than replay promises.

`release_readiness(summary_mode="quick")` surfaces non-blocking `governance_report` and `dependency_security` checks showing whether recent governance/dependency evidence exists and whether dependency advisory data was clean, vulnerable, stale, skipped, network-disabled, or scanner-unavailable. Missing reports or disabled advisory lookup are informational by default and do not fail release readiness unless dependency-security blocking is explicitly enabled.

Security boundaries:

- report generation does not require mutation mode;
- relative audit/report paths are resolved inside `REPO_PATH`;
- absolute audit paths outside the repository are not read by the report workflow;
- secrets and tokens are redacted before aggregation and export;
- resource links and lineage manifests expose only repository-relative `repo://file/{path}` paths, never host absolute paths or raw secret-bearing inputs;
- unsigned provenance sidecars and workflow-lineage manifests are local integrity/replay metadata only and are not cryptographic signatures;
- the `local-dsse-fixture` backend is deterministic offline verifier plumbing for tests/local demos, not a production release trust root;
- external OPA or Agent Governance Toolkit integrations are intentionally out of scope for this first slice;
- CI-hosted SLSA/GitHub Artifact Attestations and future Sigstore/cosign signing are complementary later work behind the same attestation schema, not replaced by these local sidecars.
