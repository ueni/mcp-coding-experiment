<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Secret exposure report

`secret_exposure_report` is a local/offline, read-only repository scanner for pre-mutation, pre-commit, release, and governance handoff checks. It scans selected repository paths, including generated MCP artifacts under `.codebase-tooling-mcp/` when they are in scope, with a conservative built-in pattern set for common token, private-key, bearer-token, connection-string, cloud-key, and generic secret-assignment shapes.

The report returns only redacted evidence:

- rule id and match class;
- repository-relative path plus line number/range;
- severity and confidence;
- stable redacted fingerprint for allowlisting and de-duplication;
- baseline/new classification when Git evidence is available.

It never returns raw secret values, file excerpts, bearer tokens, host absolute paths, prompts, or transcripts.

## Baseline and diff behavior

By default the scanner compares the working tree with `baseline_ref="HEAD"`. When Git data is available, findings already present at the baseline are marked `baseline`; findings on added diff lines or absent from the baseline are marked `new`. High-confidence newly introduced secrets can block release or mutation gates.

Untracked files are scanned as working-tree files. Because they are absent from the baseline, matching findings are treated as new when a valid baseline ref exists.

## Allowlist

The default allowlist path is `.codebase-tooling-mcp/secret-exposure-allowlist.json`. It may contain either a list or an object with `allowlist`, `findings`, `entries`, or `fingerprints` arrays.

Examples:

```json
{
  "allowlist": [
    {"fingerprint": "secretfp_sha256:...", "reason": "documented fake canary"},
    {"path": "tests/fixtures/example.env", "rule_id": "generic_secret_assignment", "line": 4}
  ]
}
```

Prefer exact fingerprints. Path/rule/line entries are useful for stable test canaries but should include a short reason and owner in real repositories.

## Gate integrations

- `mutation_step_guard` accepts `secret_exposure` evidence and denies a planned mutation when a high-confidence newly introduced secret is in scope.
- `release_readiness` runs `secret_exposure_report` by default and blocks when the report gate would block.
- `governance_report` includes a compact redacted secret-exposure summary.

## Limitations

This is intentionally not parity with GitHub Secret Scanning. It does not call GitHub APIs, validate whether credentials are live, use provider push-protection intelligence, or claim comprehensive pattern coverage. Use it as a local preflight, then rely on hosted secret scanning, provider rotation/revocation, and history cleanup for real incidents.
