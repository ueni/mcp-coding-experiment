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
python toolchain/dev/labs/policy_gatekeeper.py --changed-ref HEAD
```

Config: `.config/dev/labs/policy_gatekeeper.json`
Output: `.build/reports/POLICY_GATEKEEPER.md`

Docs sync policy is config-driven via `docs_policy`:
- docs roots (for this repo: `docs/` and `README.md`)
- required docs index (`docs/index.md`)
- implementation-vs-docs diff check against `target_branch` (default `main`)

## 6) Branch Swarm Benchmark Lab

Runs strategy branches, executes quality + benchmark commands, and ranks strategies.

```bash
python toolchain/dev/labs/branch_swarm_lab.py --allow-dirty
```

Config: `.config/dev/labs/branch_swarm_lab.json`
Output: `.build/reports/BRANCH_SWARM_REPORT.md`

## 7) Narrated PR Generator

Builds a PR packet from a git range.

```bash
python toolchain/dev/labs/narrated_pr_generator.py --base HEAD~1 --head HEAD
```

Output: `.build/reports/PR_PACKET.md`

## 3) Repo Digital Twin

Builds a JSON + Markdown digital twin snapshot for architecture and drift monitoring.

```bash
python toolchain/dev/labs/repo_digital_twin.py
```

Outputs:
- `.build/reports/REPO_DIGITAL_TWIN.json`
- `.build/reports/REPO_DIGITAL_TWIN.md`

## Existing Workflows

```bash
python toolchain/dev/labs/release_rehearsal.py --allow-dirty
python toolchain/dev/labs/refactor_tournament.py --allow-dirty
```

Configs:
- `.config/dev/labs/release_rehearsal.json`
- `.config/dev/labs/refactor_tournament.json`
