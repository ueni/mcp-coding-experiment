# Qwen3.6-35B-A3B Evaluation Artifact

This directory contains the reproducible validation harness for the production local-coding target:

```text
qwen3.6-35b-a3b:iq1
```

The local tag used during evaluation was created from:

```text
.qwen-eval-models/Qwen3.6-35B-A3B-UD-IQ1_M.gguf
```

The GGUF file is intentionally not tracked in git.

## Run

Create or pull the local Ollama tag, then run:

```bash
python3 evaluation/qwen3.6-35b-a3b/run-ollama-eval.py \
  --endpoint http://127.0.0.1:11434/api/generate \
  --model qwen3.6-35b-a3b:iq1 \
  --output evaluation/qwen3.6-35b-a3b/latest-results.json
```

The output JSON records scenario IDs, latency, Ollama token counters, throughput, output previews, and hygiene checks for reasoning/chat sentinel leakage.

## Validation gates used for this change

Repository contract tests were run with the in-repo virtualenv:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q tests/test_continue_ollama_contract.py
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q tests/test_continue_ollama_contract.py tests/test_server_tools.py -k 'local_infer or autocomplete or task_router or model_status or continue_model_routing or dockerfile_keeps_qwen36'
.venv/bin/python -m py_compile source/server.py tests/test_continue_ollama_contract.py tests/test_server_tools.py tests/test_server_memory_workspace.py
```

A broader local pytest run was attempted, but this workstation virtualenv does not include `sympy` and has no `pip`, so the unrelated math-router surface test fails before exercising Qwen routing.
