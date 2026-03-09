<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# JSON Settings Files

This page documents the JSON configuration files under `.config/labs`.

## `.config/labs/policy_gatekeeper.json`

Controls policy checks executed by `policy_gatekeeper.py`.

- `default_target_branch`: fallback diff target when `--changed-ref` is not provided. Current default: `main`.
- `max_file_size_bytes`: maximum tracked file size allowed by policy.
- `forbidden_patterns`: regex list scanned in tracked files.
- `required_paths`: paths that must exist.
- `required_when_paths_change`: conditional required files based on changed path prefixes.
- `command_checks`: shell commands that must pass.
- `docs_policy`: documentation freshness policy.

`docs_policy` keys:
- `enabled`: enable docs checks.
- `target_branch`: branch used for docs-vs-implementation diff checks.
- `doc_roots`: documentation scope (`docs/` and `README.md` in this repo).
- `index_path`: required documentation index (`docs/index.md`).
- `impl_path_prefixes`: code paths considered implementation changes.
- `require_docs_for_impl_diff`: require docs changes when implementation changes are detected.

## `.config/labs/branch_swarm_lab.json`

Configuration for `branch_swarm_lab.py` strategy benchmarking.

- `base_ref`: git ref strategies branch from.
- `report_path`: markdown output report path.
- `strategies`: list of benchmark strategies.

Each strategy supports:
- `name`: display name.
- `branch`: temporary strategy branch name.
- `setup`: commands to prepare benchmark state.
- `quality`: commands that should pass (used in scoring).
- `benchmark`: commands printing a numeric metric (lower is better).

## `.config/labs/refactor_tournament.json`

Configuration for `refactor_tournament.py` refactor competitions.

- `base_ref`: git ref used as tournament base.
- `report_path`: markdown output report path.
- `strategies`: list of refactor strategies.

Each strategy supports:
- `name`: display name.
- `branch`: temporary strategy branch name.
- `mutate`: transformation commands to apply.
- `checks`: verification commands run after mutations.

## `.config/labs/release_rehearsal.json`

Configuration for `release_rehearsal.py` dry-run release checks.

- `target_branch`: branch for release readiness checks.
- `changelog_from`: git ref used to gather changelog context.
- `report_path`: markdown output report path.
- `artifacts`: required release artifact files.
- `checks`: command checks that must pass.
