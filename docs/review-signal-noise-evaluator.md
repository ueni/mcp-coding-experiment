<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Review signal/noise evaluator

`scripts/review_signal_noise_evaluator.py` is a deterministic, offline, read-only
fixture evaluator for code-review workflows such as `review_changed_files` or
`task_router(task='review')`. It tracks CR-Bench-style review quality: useful
blocker findings versus missed or spurious findings.

Run the checked-in fixture pack:

```bash
python3 scripts/review_signal_noise_evaluator.py \
  --fixture-dir tests/fixtures/review_evaluation
```

The command prints JSON with:

- `schema: review_signal_noise_evaluation.v1`
- `ok` and `threshold_status`
- precision/recall counts in `summary`
- top-level `spurious_findings`
- fixture-level `missed_findings`, `true_positives`, and `evidence_paths`

It does not call the network, shell out to a reviewer, or mutate repository files.
To score captured review output instead of the checked-in sample output, write one
JSON file per fixture named `<fixture-id>.json` and pass `--actual-output-dir`.
Each file should contain a normalized finding list, for example:

```json
{
  "schema": "review_output_findings.v1",
  "findings": [
    {
      "id": "authz-delete-user",
      "severity": "blocker",
      "title": "Missing authorization check in delete_user",
      "message": "delete_user now calls db.delete without the authorization guard.",
      "path": "source/auth.py",
      "line": 12,
      "evidence_paths": ["diff.patch"]
    }
  ]
}
```

## Adding a fixture

1. Create `tests/fixtures/review_evaluation/<fixture-id>/`.
2. Add `diff.patch` with the representative PR diff or scenario.
3. Add `fixture.json` with:
   - `schema: review_signal_noise_fixture.v1`
   - stable `id`
   - `expected_findings` for true blockers the reviewer should report
   - `should_not_flag` entries for plausible false positives/non-findings
   - `match_terms`, `path`, and optional `line`/`line_tolerance` for deterministic
     matching
   - `evidence_paths` pointing at the diff or other checked-in fixture evidence
4. Add `review_output.json` with the current expected normalized reviewer output.
5. Register the fixture path in `tests/fixtures/review_evaluation/manifest.json`.
6. Run:

```bash
python3 -m pytest tests/test_review_signal_noise_evaluator.py
```

Keep fixture output compact and synthetic. Do not include secrets, private
customer data, or host-specific absolute paths.

## Threshold maintenance

The manifest currently gates on perfect precision, perfect recall, and zero
spurious findings:

```json
{
  "min_precision": 1.0,
  "min_recall": 1.0,
  "max_spurious_findings": 0
}
```

Use CLI overrides only for experiments:

```bash
python3 scripts/review_signal_noise_evaluator.py \
  --min-precision 0.9 \
  --min-recall 1.0 \
  --max-spurious-findings 0
```

Change checked-in thresholds only when maintainers intentionally accept a new
quality bar. If thresholds are relaxed, add fixture evidence explaining why the
remaining noise is tolerable; if thresholds are tightened, keep at least one
false-positive scenario in `should_not_flag` so regressions remain measurable.
