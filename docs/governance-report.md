<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Governance report workflow

`governance_report` is a read-only MCP workflow for enterprise audit review. It reads redacted events from `MCP_AUDIT_LOG_FILE`, summarizes existing governance artifacts, and exports JSON plus Markdown reports under `.codebase-tooling-mcp/reports/`. When `export=true`, it also writes a deterministic, redacted `workflow_lineage.v1` manifest for the governance-report workflow.

The first-slice report schema is `governance_report.v1`. It includes:

- audit counts for sensitive tool calls, blocked attempts, mutation-gate failures, HTTP authorization denials, tool categories, and failure reasons;
- hash-chain digest metadata over redacted audit events (`sha256`, no raw secrets);
- local governance hook summaries from stored `policy_simulator`, `workflow_policy_plan`, `release_readiness`, and `required_tool_chain` results when available;
- latest stored `workflow_policy_plan` decision/plan ID as optional pre-execution workflow-policy evidence when available;
- latest exported `dependency_security_report` status, vulnerability count, advisory freshness, and report path when available;
- compact `tool_catalog_integrity` status, baseline/current digests, drift counts, and advisory lint counts without embedding the full catalog;
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

- `backend`: selected verifier backend. Supported values are `local-dsse-fixture` and opt-in `github-artifact-attestations`; unknown future values report `unsupported`.
- `subject_digest`: SHA-256 digest of the artifact subject. It must match the local artifact digest and the verified attestation subject.
- `signer_identity`: identity string claimed by the signer or GitHub Actions workflow certificate. It is reported after secret/path redaction.
- `bundle_ref` / `envelope_ref`: reference to the attestation bundle or inline DSSE envelope; the fixture backend uses `inline:signing.envelope`, while GitHub Artifact Attestations use `bundle_ref` plus a trusted-root reference in `signing.verification.trusted_root_ref`.
- `verification.status`: one of `unsigned`, `verified`, `invalid`, `unsupported`, or `unavailable`.
- `envelope`: DSSE-style fixture envelope containing a signed `mcp_artifact_attestation.v1` payload. The payload binds the artifact subject digest and a digest of the provenance sidecar with the envelope removed, so artifact edits and sidecar edits are both detected.

`local-dsse-fixture` remains intentionally local/offline verifier plumbing for tests and demos, not a production release trust root. The `github-artifact-attestations` backend is read-only and disabled unless the sidecar/config explicitly sets `signing.verification.enabled=true`. Its default path is offline-safe: the caller supplies repository-local `bundle_ref` and `trusted_root_ref` files, and `artifact_provenance` shells out to `gh attestation verify --bundle ... --custom-trusted-root ... --format json` with a bounded timeout. The verifier then enforces policy identity checks in addition to cryptographic success: subject SHA-256 digest, expected owner/repo, workflow path or name, expected ref or commit, and predicate type. If any prerequisite or policy evidence is missing, the result is `unavailable`; if evidence is present but mismatched, the result is `invalid`.

Online GitHub verification is not used by default. To request it, set `signing.verification.mode="online"` and `signing.verification.allow_online=true` (or the matching explicit environment gates); otherwise online requests fail closed with `attestation_online_verification_disabled`. When online verification is actually attempted, the attestation result reports `network_access=true` with only bounded metadata. Missing `gh`, bundle, trusted root, token, private-repository transparency data, malformed verifier output, or an unavailable GitHub verifier never produces success. Sigstore/cosign remains a future unsupported backend behind the same schema.

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

`artifact_provenance` is a read-only verification helper for these sidecars. It can check one artifact path or scan local report/snapshot artifacts, and flags missing sidecars, stale sidecars, digest mismatches, artifact/provenance schema mismatches, invalid local/GitHub attestations, unavailable GitHub verifier prerequisites, and unsupported attestation backends without mutating artifacts. Use `github-artifact-attestations` for release artifacts that should be bound to an expected GitHub Actions workflow identity; keep `local-dsse-fixture` for deterministic offline tests/local demos only. GitHub attestations complement `workflow_lineage`: lineage explains the MCP workflow/report replay inputs, while the GitHub attestation proves the artifact came from the expected GitHub workflow/predicate identity.

`workflow_lineage(mode="verify", manifest_path=".codebase-tooling-mcp/reports/...workflow-lineage.json")` is the first read-only drift verifier for `workflow_lineage.v1`. It recomputes the deterministic governance-report plan identity from redacted request constraints, git refs, audit-source metadata, and audit digest inputs. It reports `matched` when deterministic inputs and observed artifact digests still match, `input_changed` when the plan identity has drifted, `artifact_changed` when recorded artifacts are missing or their digests differ, and `non_deterministic_node` markers for observed outputs that are intentionally not promised as bit-for-bit replay.

The lineage manifest deliberately stores only safe identity inputs: repository-relative paths, redacted audit metadata/digests, git refs/commits, schema versions, deterministic node IDs, and observed artifact digests. It must not store raw prompts, transcript snippets, bearer tokens, secrets, absolute host paths, or file contents. Model-authored or otherwise non-deterministic outputs are represented as observed/non-deterministic nodes rather than replay promises.

`release_readiness(summary_mode="quick")` surfaces non-blocking `governance_report`, `workflow_policy_plan`, and `dependency_security` checks showing whether recent governance/workflow-policy/dependency evidence exists and whether dependency advisory data was clean, vulnerable, stale, skipped, network-disabled, or scanner-unavailable. Missing reports or disabled advisory lookup are informational by default and do not fail release readiness unless dependency-security blocking is explicitly enabled.

Security boundaries:

- report generation does not require mutation mode;
- relative audit/report paths are resolved inside `REPO_PATH`;
- absolute audit paths outside the repository are not read by the report workflow;
- secrets and tokens are redacted before aggregation and export;
- tool-catalog integrity summaries include only public MCP metadata digests/counts, never repository contents or host absolute paths;
- resource links and lineage manifests expose only repository-relative `repo://file/{path}` paths, never host absolute paths or raw secret-bearing inputs;
- unsigned provenance sidecars and workflow-lineage manifests are local integrity/replay metadata only and are not cryptographic signatures;
- the `local-dsse-fixture` backend is deterministic offline verifier plumbing for tests/local demos, not a production release trust root;
- the `github-artifact-attestations` backend is opt-in, read-only, offline by default, and redacts tokens, unnecessary bundle internals, artifact contents, and host absolute paths from results;
- GitHub attestation success requires both verifier success and expected policy identity matches; disabled backends, missing dependencies/files/tokens, unavailable online verification, or private-repository transparency gaps report `unavailable` or `unsupported`, never `verified`;
- external OPA or Agent Governance Toolkit integrations are intentionally out of scope for this first slice;
- CI-hosted GitHub Artifact Attestations and future Sigstore/cosign signing complement local sidecars and workflow lineage rather than replacing them.
