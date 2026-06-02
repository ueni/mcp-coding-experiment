<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Skill privilege scope

`skill_privilege_scope` is a read-only, offline analyzer for imported workflow cards and agent-skill-like bundles. It expands the item text/metadata into deterministic action nodes and compares them with the declared task intent before any imported/generated code is executed.

## Scope

- Input: a user task prompt plus optional workflow-card/skill dictionaries.
- Output schema: `skill_privilege_scope.v1`.
- Actions detected: `read_path`, `write_path`, `command`, `network`, `github_api`, `release_publish`, and `secret_adjacent`.
- Evidence is redacted and paths are reported as repository-relative values; outside-repository absolute paths are collapsed to `<outside-repo>`.

## Decisions

- `required`: the privilege is allowed by the task and explicitly declared by the item or is the default read privilege.
- `allowed`: the privilege appears allowed by the task but should be documented or narrowed before import.
- `over_privileged`: the privilege is not needed for the task; mutating, release, network, and secret-adjacent excesses become advisory blockers.

The report includes `advisory_blockers` and a `governance` section that can be consumed by `workflow_policy_plan`, `policy_governance_decision`, and skill-pack import review flows without granting permission or executing the imported item.

## Router mode

Call the public router with:

```python
task_router(
    mode="skill_privilege_scope",
    prompt="Read-only audit of docs/guide.md; do not write anything.",
    candidates=[workflow_card_or_skill_dict],
)
```

The direct `skill_privilege_scope` tool accepts the same prompt and `items` list for deterministic tests and import review.
