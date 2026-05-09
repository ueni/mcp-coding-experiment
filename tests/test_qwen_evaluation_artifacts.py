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
ACQUISITION_ATTEMPT = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/target-model-acquisition-attempt-2026-05-09.md"
RUNNER = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py"
SMOKE = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/target-model-smoke-2026-05-09.md"
SMOKE_RESULT = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-smoke-2026-05-09.json"
BOUNDED_RESULT = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-verifier-bounded-2026-05-09.json"
BOUNDED_LOG = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-verifier-bounded-2026-05-09.log"
FULL_RESULT = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-full-2026-05-09.json"
FULL_LOG = REPO_ROOT / "evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-full-2026-05-09.log"

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
    acquisition_attempt = ACQUISITION_ATTEMPT.read_text()
    smoke = SMOKE.read_text()

    assert "Lenovo ThinkPad T14 Gen1 AMD" in doc
    assert "current orchestrator" in doc
    assert "approximately 14 sustained tokens/sec" in doc
    assert "GitHub-hosted" in doc
    assert "evaluation/qwen3.6-35b-a3b/coding-scenarios.jsonl" in doc
    assert "evaluation/qwen3.6-35b-a3b/report-template.md" in doc
    assert "evaluation/qwen3.6-35b-a3b/docker-gpu-runtime-2026-05-08.md" in doc
    assert "evaluation/qwen3.6-35b-a3b/model-authorization-request-2026-05-09.md" in doc
    assert "evaluation/qwen3.6-35b-a3b/target-model-acquisition-attempt-2026-05-09.md" in doc
    assert "evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py" in doc
    assert "evaluation/qwen3.6-35b-a3b/target-model-smoke-2026-05-09.md" in doc
    assert "evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-smoke-2026-05-09.json" in doc
    assert "evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-verifier-bounded-2026-05-09.json" in doc
    assert "evaluation/qwen3.6-35b-a3b/results/results-docker-ollama-full-2026-05-09.json" in doc
    assert ACQUISITION_ATTEMPT.exists()
    assert RUNNER.exists()
    assert SMOKE.exists()
    assert SMOKE_RESULT.exists()
    assert BOUNDED_RESULT.exists()
    assert BOUNDED_LOG.exists()
    assert FULL_RESULT.exists()
    assert FULL_LOG.exists()

    for text in (doc, report, docker_runtime, auth_request, acquisition_attempt, smoke):
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

    assert "Qwen3.6-35B-A3B-UD-IQ1_M.gguf" in acquisition_attempt
    assert "Content-Length: 10047749088" in acquisition_attempt
    assert "no Qwen3.6-35B-A3B inference result was produced" in acquisition_attempt

    assert "First-token latency | 19.161 s" in smoke
    assert "Sustained output rate | 7.929 tokens/sec" in smoke
    assert "offloaded 41/41 layers to GPU" in smoke
    assert "Vulkan backend" in smoke
    assert "0dc2488c89d916c5599f7c03a286cd8f37a6a75a02bc13caf41c6bac26d70c9e" in smoke
    smoke_result = json.loads(SMOKE_RESULT.read_text())
    assert smoke_result["aggregate"]["completed"] == 1
    assert smoke_result["aggregate"]["median_tokens_per_sec"] == 7.929

    bounded_result = json.loads(BOUNDED_RESULT.read_text())
    assert bounded_result["aggregate"]["completed"] == 2
    assert bounded_result["aggregate"]["median_tokens_per_sec"] == 7.997

    full_result = json.loads(FULL_RESULT.read_text())
    full_log = FULL_LOG.read_text(errors="replace")
    assert full_result["aggregate"]["completed"] == 7
    assert full_result["aggregate"]["scenario_count"] == 7
    assert full_result["aggregate"]["median_tokens_per_sec"] == 8.056
    assert {result["category"] for result in full_result["results"]} == REQUIRED_CATEGORIES
    assert "offloaded 41/41 layers to GPU" in full_log
    assert "AMD Radeon Graphics (RADV RENOIR)" in full_log
    assert "offloaded `0/41` layers to GPU" not in doc
    assert "offloaded `0/41` layers to GPU" not in report
    assert "Current target-model results are GPU-backed" in report
    assert "current-orchestrator comparison" in report

    for recommendation in (
        "suitable for productive coding usage",
        "suitable only for limited/offline scenarios",
        "not viable",
    ):
        assert recommendation in doc
        assert recommendation in report
