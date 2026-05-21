<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Memory governance report

`memory_governance_report` is a read-only advisory report for repository-local MCP memory stores. It inventories context, failure, root-cause, artifact, and workflow-task memory without deleting, rewriting, or returning raw memory content.

The first-slice schema is `memory_governance_report.v1`. It includes:

- store metadata for `.codebase-tooling-mcp/memory/context_memory.json`, failure memory, root-cause memory, artifact memory indexes, and workflow task state;
- entry-level redacted metadata such as kind, source, confidence, schema/policy version, timestamps, age, tag count, and stable hashes;
- deterministic findings for missing/old schema or policy version, missing source/confidence/provenance metadata, expired/stale entries, stale repository path references, untrusted-content contamination, sensitive-placeholder/admission risks, and duplicate/conflicting facts where detectable;
- deterministic quarantine, expiry, or revalidation recommendations.

The report intentionally does not include memory text, summaries, stderr/stdout snippets, root causes, fixes, task payloads, raw paths from memory entries, host absolute paths, secrets, or bearer tokens. Evidence uses counts, redacted scalar metadata, and stable hashes only.

`governance_report` embeds the compact `memory_governance.summary` so broader audit reports can show memory consolidation posture without dumping memory content.

Mutation or cleanup remains out of scope. Any future quarantine/delete path must use explicit mutation controls and audit events.
