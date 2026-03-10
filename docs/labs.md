<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# MCP Fun Labs

Workflows implemented in this folder:

- `release_rehearsal.py` (12)
- `refactor_tournament.py` (1)
- `policy_gatekeeper.py` (5)
- `branch_swarm_lab.py` (6)
- `narrated_pr_generator.py` (7)
- `repo_digital_twin.py` (3)

## 5) Policy-as-Code Gatekeeper

Runs policy checks across repo content and command checks.

```bash
python source/labs/policy_gatekeeper.py --changed-ref HEAD
```

Expected result (example):

```text
Policy checks completed
Report written: .build/reports/POLICY_GATEKEEPER.md
```

Config: `.config/labs/policy_gatekeeper.json`
Output: `.build/reports/POLICY_GATEKEEPER.md`

Docs sync policy is config-driven via `docs_policy`:
- docs roots (for this repo: `docs/` and `README.md`)
- required docs index (`docs/index.md`)
- implementation-vs-docs diff check against `target_branch` (default `main`)

## 6) Branch Swarm Benchmark Lab

Runs strategy branches, executes quality + benchmark commands, and ranks strategies.

```bash
python source/labs/branch_swarm_lab.py --allow-dirty
```

Expected result (example):

```text
Benchmark complete
Report written: .build/reports/BRANCH_SWARM_REPORT.md
```

Config: `.config/labs/branch_swarm_lab.json`
Output: `.build/reports/BRANCH_SWARM_REPORT.md`

## 7) Narrated PR Generator

Builds a PR packet from a git range.

```bash
python source/labs/narrated_pr_generator.py --base HEAD~1 --head HEAD
```

Expected result (example):

```text
PR packet generated
Report written: .build/reports/PR_PACKET.md
```

Output: `.build/reports/PR_PACKET.md`

## 3) Repo Digital Twin

Builds a JSON + Markdown digital twin snapshot for architecture and drift monitoring.

```bash
python source/labs/repo_digital_twin.py
```

Expected result (example):

```text
Digital twin generated
Reports written under .build/reports/
```

Outputs:
- `.build/reports/REPO_DIGITAL_TWIN.json`
- `.build/reports/REPO_DIGITAL_TWIN.md`

## Existing Workflows

```bash
python source/labs/release_rehearsal.py --allow-dirty
python source/labs/refactor_tournament.py --allow-dirty
```

Expected result (example):

```text
Release/refactor workflow complete
Report written under .build/reports/
```

Configs:
- `.config/labs/release_rehearsal.json`
- `.config/labs/refactor_tournament.json`
- `.config/labs/release_rehearsal_cpp_gtest.json` (Linux C/C++ + CMake/CTest/GoogleTest)
- `.config/labs/release_rehearsal_mcu_gcc.json` (GCC MCU + optional host-side gtest stage)
- `.config/labs/refactor_tournament_cpp_tooling.json` (clang-format/cppcheck oriented strategies)
- `.config/labs/policy_gatekeeper_cpp_mcu.json` (policy profile for C/C++ + MCU repos)
