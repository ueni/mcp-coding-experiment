<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Workflow fixture smith

`workflow_fixture_smith` is an opt-in, deterministic, offline first slice for
creating FrontierSmith-style open-ended workflow fixture candidates from local
metadata. It is designed to reduce overfitting to the checked-in E2E MCP workflow
benchmarks without importing private or live service data.

## Safety model

- Inputs are checked-in/local seed metadata only:
  `evaluation/workflow_fixture_smith/seeds/local_seed_metadata.json`.
- Live GitHub issue import and network-dependent generation are disabled by
  schema and validation.
- Generated candidates are written only to the quarantine review queue:
  `.codebase-tooling-mcp/review-queue/workflow-fixture-candidates/`.
- Candidates carry `quarantine.review_queue: true` and
  `quarantine.ci_enabled: false`; they are never benchmark fixtures and are not
  CI-enabled unless a maintainer manually reviews and promotes them later.
- Unsafe or unbounded seed/variant text is rejected, including live imports,
  network dependencies, destructive actions, secret handling, and unbounded
  repository scope.

## Candidate contract

Each candidate uses `workflow_fixture_smith_candidate.v1` and includes:

- source metadata proving local/offline provenance;
- verifier requirements with deterministic checks, expected file touches, and
  required reports;
- safety gates for no-network/no-live-import operation, bounded mutation scope,
  and manual promotion;
- expected artifacts for reviewer/verifier inspection;
- diversity score and reasons comparing tags, tool-chain shape, artifact kinds,
  and combined gates against existing checked-in workflow fixtures.

## Usage

Dry-run without writing artifacts:

```bash
python3 scripts/workflow_fixture_smith.py --dry-run --compact
```

Write the quarantine review queue:

```bash
python3 scripts/workflow_fixture_smith.py --compact
```

The default queue path is generated state under `.codebase-tooling-mcp/`, not the
checked-in benchmark fixture directory. Promotion, if ever desired, is a separate
manual review step that copies a candidate into a proper benchmark fixture with
its own tests and PR review.
