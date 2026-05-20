<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Skill-pack risk and fit scoring

`skill_pack_score` is a deterministic, read-only report slice for repository workflow cards and offline imported-skill fixtures. It does not import, rewrite, delete, or mutate user skill files. The scorer emits `skill_pack_score.v1` rows with:

- `risk_score` from provenance/trust metadata, documentation completeness, external links, broad permissions, network/secret/file-system wording, dangerous shell/exfiltration patterns, and prompt-injection-like text.
- `fit_score` from task-query overlap, routing phrases, required-tool matches, language/path affinity, and optional local benchmark/pass-rate metadata.
- `decision`: `allow`, `allow_with_caveats`, `needs_human_review`, or `quarantine`.
- `evidence`: bounded redacted signals explaining why the score was assigned.
- `refinement_suggestions`: optional local-only advice for making noisy summaries more query-specific without modifying source content.

## Difference from workflow-card trust linting

Trust-tier linting answers “is this card structurally reviewable and within declared boundaries?” It checks required trust metadata, guardrails, broad permissions, dangerous shell/network/file-system wording, and high-risk sandbox guidance.

Skill-pack scoring answers “is this item safe and useful for this specific task?” It consumes trust-lint findings as one risk input, then adds task-fit scoring and a routing decision. A card can pass trust lint but still be demoted for low fit; a risky or suspicious imported skill can be quarantined even if it has enough fields to be syntactically linted.

The workflow selector consumes `skill_pack_score` defensively: quarantined high-risk items are suppressed from normal matches, while low-fit items are demoted so irrelevant skill context does not crowd out better workflow cards.

## Fixture corpus refresh

The offline test corpus lives at `tests/fixtures/skill_pack_score_skills.json` with a matching `.license` sidecar. To refresh it:

1. Keep fixtures synthetic or repository-local; do not fetch public skill marketplaces during tests.
2. Preserve at least three cases: benign/high-fit, benign low-fit/noisy, and risky/suspicious.
3. Include trust metadata, routing terms, prerequisites, permissions, network access, and any benchmark/pass-rate fields needed by the scenario.
4. Run the targeted gate: `python3 -m pytest tests/test_server_tools.py -k skill_pack_score`.

Do not include secrets, live URLs with credentials, private repository paths, or real user skill contents in the fixture corpus.
