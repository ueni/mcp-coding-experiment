# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/coding-scenarios.jsonl"
DOC = REPO_ROOT / "docs/evaluations/qwen3.6-35b-a3b-local.md"
REPORT = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/report-template.md"
DOCKER_RUNTIME = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/docker-gpu-runtime-2026-05-08.md"
AUTH_REQUEST = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/model-authorization-request-2026-05-09.md"
RUNNER = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py"

REQUIRED_CATEGORIES = {
    "c_cpp_embedded",
    "bash",
    "python",
    "javascript",
    "debugging_review",
    "long_context",
    "structured_output",
}

REQUIRED_MEASUREMENT_FIELDS = {
    "first_token_latency_s",
    "end_to_end_latency_s",
    "input_tokens",
    "output_tokens",
    "sustained_tokens_per_sec",
    "peak_ram_mb",
    "cpu_notes",
    "gpu_vram_notes",
    "verdict",
}


def _load_scenarios() -> list[dict]:
    scenarios = []
    for line_number, line in enumerate(SCENARIOS.read_text().splitlines(), start=1):
        assert line.strip(), f"blank JSONL line at {line_number}"
        scenario = json.loads(line)
        scenario["_line_number"] = line_number
        scenarios.append(scenario)
    return scenarios


def test_qwen_scenario_manifest_covers_required_categories() -> None:
    scenarios = _load_scenarios()

    categories = {scenario["category"] for scenario in scenarios}
    assert REQUIRED_CATEGORIES <= categories
    assert len({scenario["id"] for scenario in scenarios}) == len(scenarios)


def test_qwen_scenarios_have_measurement_and_quality_contract() -> None:
    for scenario in _load_scenarios():
        context = f"{scenario['id']} line {scenario['_line_number']}"
        assert scenario["title"], context
        assert scenario["prompt"], context
        assert scenario["expected_observations"], context
        assert scenario["quality_checks"], context
        assert REQUIRED_MEASUREMENT_FIELDS <= set(scenario["measurement_fields"]), context


def test_qwen_evaluation_docs_link_canonical_artifacts() -> None:
    doc = DOC.read_text()
    report = REPORT.read_text()
    docker_runtime = DOCKER_RUNTIME.read_text()
    auth_request = AUTH_REQUEST.read_text()

    assert "Lenovo ThinkPad T14 Gen1 AMD" in doc
    assert "current orchestrator" in doc
    assert "approximately 14 sustained tokens/sec" in doc
    assert "GitHub-hosted" in doc
    assert "evaluation/qwen3.6-35b-a3b/coding-scenarios.jsonl" in doc
    assert "evaluation/qwen3.6-35b-a3b/report-template.md" in doc
    assert "evaluation/qwen3.6-35b-a3b/docker-gpu-runtime-2026-05-08.md" in doc
    assert "evaluation/qwen3.6-35b-a3b/model-authorization-request-2026-05-09.md" in doc
    assert "evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py" in doc
    assert RUNNER.exists()

    for text in (doc, report, docker_runtime, auth_request):
        assert "source/Dockerfile" in text
        assert ".devcontainer/devcontainer.json" in text
        assert "--device=/dev/dri" in text
        assert "OLLAMA_VULKAN=1" in text
        assert "Qwen3.6-35B-A3B weights" in text

    assert "qwen2.5-coder:1.5b" in docker_runtime
    assert "29/29" in docker_runtime
    assert "validates the Docker GPU/Ollama runtime only" in docker_runtime

    assert "explicit blocked evaluation artifact" in auth_request
    assert "external/non-code blocker" in auth_request
    assert "Authorize exactly one" in auth_request
    assert "unsloth/Qwen3.6-35B-A3B-GGUF" in auth_request
    assert "a483e9e6cbd595906af30beda3187c2663a1118c" in auth_request
    assert "issue #1 acceptance criteria" in auth_request
    assert "does **not** close or satisfy the full issue #1 benchmark acceptance criteria" in doc
    assert "does **not** satisfy the full issue #1 benchmark acceptance criteria" in report
    assert "must not close or claim issue #1" in auth_request
    assert "first-token latency" in auth_request
    assert "sustained tokens/sec" in auth_request

    for recommendation in (
        "suitable for productive coding usage",
        "suitable only for limited/offline scenarios",
        "not viable",
    ):
        assert recommendation in doc
        assert recommendation in report
