# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
from pathlib import Path

from source.agents_context_lint import (
    EFFECTIVENESS_REPORT_SCHEMA,
    REPORT_SCHEMA,
    analyze_agents_context,
    evaluate_context_effectiveness,
    load_effectiveness_fixtures,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "agents_context_minimal_routing.json"


def test_agents_context_lint_reports_budget_classes_and_safe_metadata():
    report = analyze_agents_context(ROOT)

    assert report["schema"] == REPORT_SCHEMA
    assert report["read_only"] is True
    assert report["advisory_only"] is True
    assert report["security"]["repo_boundary_enforced"] is True
    assert report["security"]["records_file_contents"] is False
    assert report["summary"]["total_estimated_tokens"] > 0
    assert report["classification"]["safety-critical"]["count"] > 0
    assert report["classification"]["workflow-routing"]["count"] > 0
    assert report["classification"]["optional-background"]["count"] > 0

    serialized = json.dumps(report)
    assert str(ROOT) not in serialized
    assert "Default to read-only operations" not in serialized
    assert "Authorization: Bearer" not in serialized


def test_agents_context_lint_flags_missing_links_and_repeated_concepts(tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        "# AGENTS.md\n"
        "- ALWAYS read [Missing](./missing.md) before any task.\n"
        "- Use `task_router(mode=\"workflow_select\")` when unsure.\n"
        "- Use `task_router(mode=\"task\")` for natural-language tasks.\n"
        "- Use task_router for routing decisions.\n"
        "- Use workflow_select routing before specialist workflows.\n",
        encoding="utf-8",
    )

    report = analyze_agents_context(tmp_path)

    assert report["status"] == "findings"
    assert report["stale_guidance"]["missing_links"] == [
        {"path": "AGENTS.md", "target": "missing.md", "reason": "missing_relative_link"}
    ]
    assert any(item.get("concept") == "task-router-routing" for item in report["duplicates"])
    assert any(finding["kind"] == "broad_global_instruction" for finding in report["findings"])


def test_agents_context_effectiveness_fixture_shape_and_fake_router():
    fixture_set = load_effectiveness_fixtures(FIXTURE)
    assert len(fixture_set["fixtures"]) >= 3

    def fake_router(*, mode, prompt, top_k, execution_mode):
        assert mode == "workflow_select"
        assert top_k == 3
        lowered = prompt.lower()
        if "release readiness" in lowered:
            card = "release-readiness"
        elif "pytest" in lowered or "impact verification" in lowered:
            card = "test-impact"
        else:
            card = "security-triage"
        return {"matches": [{"id": card}], "execution_mode": execution_mode}

    report = evaluate_context_effectiveness(FIXTURE, repo_root=ROOT, route_fn=fake_router)

    assert report["schema"] == EFFECTIVENESS_REPORT_SCHEMA
    assert report["summary"]["fixture_count"] == 3
    assert report["summary"]["baseline_top_workflow_card_accuracy"] == 1.0
    assert report["summary"]["with_context_top_workflow_card_accuracy"] == 1.0
    assert report["summary"]["routing_preservation"] == 1.0
    assert report["summary"]["passed_thresholds"] is True
    assert report["security"]["network_access"] is False
