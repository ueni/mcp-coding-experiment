# Skill privilege scope

`skill_privilege_scope` is a deterministic, read-only analyzer for imported workflow cards, prompts, and agent-skill-like bundles. It does not import packages, execute skill code, run shell commands, call networks, or dereference external URLs.

The analyzer expands untrusted card/skill metadata into action nodes such as `read_path`, `write_path`, `command`, `network`, `github_api`, `release_publish`, and `secret_adjacent`. It then compares those nodes with the declared user intent or a supplied `workflow_policy_plan` intent.

Outputs use `skill_privilege_scope.v1` and include redacted evidence, repository-relative targets where possible, `required_privileges`, `advisory_blockers`, and suggested constraints. A mutating node in a read-only task is reported as task-conditioned over-privilege; the same node is recorded as required when the user intent explicitly declares the write scope.

Integration points:

- `skill_pack_score` embeds `skill_privilege_scope` for each imported item so import review can see task-conditioned privilege blockers alongside static risk/fit evidence.
- `workflow_policy_plan` accepts an optional `imported_item` and folds advisory blockers into `least_privilege` policy findings without executing the imported content.
- `policy_governance_decision` carries the same `least_privilege_scope` through the governance decision so reports can preserve the advisory blocker evidence.

Security invariants: output is redacted, read-only, advisory by default, and records `executed_imported_code=false` and `external_services_called=false`.
