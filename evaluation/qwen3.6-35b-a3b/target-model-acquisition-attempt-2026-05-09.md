<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Target Model Acquisition Attempt: Qwen3.6-35B-A3B

Date: 2026-05-09
Host: `user-thinkpad-t14`
Repository branch: `builder/qwen36-local-eval-plan`
Docker image present locally: `codebase-tooling-mcp:qwen36-eval`

## Objective

Address the Gatekeeper request by attempting the actual Qwen3.6-35B-A3B local evaluation through the repository Docker/Ollama runtime rather than only documenting a blocker.

## Selected target artifact

Hugging Face repository: `unsloth/Qwen3.6-35B-A3B-GGUF`

Selected quantization: `Qwen3.6-35B-A3B-UD-IQ1_M.gguf`

Reason: this was the smallest advertised directly downloadable GGUF target-model artifact found during inspection. HEAD checks showed larger alternatives such as `Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf` and `Qwen3.6-35B-A3B-MXFP4_MOE.gguf`.

Observed remote size:

```text
Qwen3.6-35B-A3B-UD-IQ1_M.gguf Content-Length: 10047749088 bytes
Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf Content-Length: 10756586464 bytes
Qwen3.6-35B-A3B-MXFP4_MOE.gguf Content-Length: 21706144736 bytes
```

The model repository was publicly reachable during this attempt; the blocker is not a gated-model authorization failure for this selected artifact.

## Commands attempted

Download target GGUF outside the repository history into an ignored local cache:

```bash
mkdir -p /home/user/source/mcp-server-git-local-files/.qwen-eval-models
cd /home/user/source/mcp-server-git-local-files/.qwen-eval-models
curl -L --fail --continue-at - \
  "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-IQ1_M.gguf" \
  -o Qwen3.6-35B-A3B-UD-IQ1_M.gguf
```

When single-connection transfer became too slow, resume was also attempted with `aria2c`:

```bash
aria2c --continue=true \
  --max-connection-per-server=16 \
  --split=16 \
  --min-split-size=8M \
  --file-allocation=none \
  --out=Qwen3.6-35B-A3B-UD-IQ1_M.gguf \
  "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-IQ1_M.gguf"
```

## Result

The target-model evaluation could not be executed in this run because the model artifact did not complete downloading.

Observed partial local state after stopping the unsuccessful acquisition attempt:

```text
Remote Content-Length: 10047749088 bytes
Local apparent size: 9692151808 bytes
Local disk blocks used: approximately 4.1G
Aria2 progress before stop: approximately 42% of 9.3GiB with ~1.2 MiB/s, ETA over 1 hour
Root filesystem free space before/around attempt: approximately 69-72G free, 92-93% used
```

Because the file was incomplete and sparse after interrupted segmented resume, it was not imported into Ollama and no Qwen3.6-35B-A3B inference result was produced. This avoids recording fake target-model latency, throughput, or quality data.

## Docker/runtime status

The repository Docker/devcontainer runtime path remains the correct execution path once the Qwen3.6-35B-A3B weights are present:

- `source/Dockerfile` image present locally as `codebase-tooling-mcp:qwen36-eval`;
- `.devcontainer/devcontainer.json` remains the canonical developer entrypoint for the same Docker runtime path;
- Ollama `0.18.2` available in the image;
- `/dev/dri` pass-through and `OLLAMA_VULKAN=1` verified separately for the runtime path;
- a new dependency-free harness is available at `evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py` to run the scenario manifest against the Ollama `/api/generate` endpoint.

## Reproducible next command after a complete download

Once the GGUF is complete and checksummed, create an Ollama model in the running repository Docker image, then run the harness:

```bash
cat > /tmp/Modelfile.qwen36 <<'EOF'
FROM /models/Qwen3.6-35B-A3B-UD-IQ1_M.gguf
PARAMETER temperature 0.1
PARAMETER num_ctx 4096
EOF

docker run --rm \
  --security-opt=seccomp=unconfined \
  --security-opt=apparmor=unconfined \
  --device=/dev/dri \
  -e OLLAMA_VULKAN=1 \
  -e OLLAMA_HOST=127.0.0.1:11434 \
  -v "$PWD:/repo" \
  -v "$PWD/.qwen-eval-models:/models:ro" \
  codebase-tooling-mcp:qwen36-eval \
  bash -lc 'ollama serve >/tmp/ollama.log 2>&1 &
            for i in $(seq 1 60); do curl -fsS http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done
            ollama create qwen3.6-35b-a3b-iq1m -f /tmp/Modelfile.qwen36
            python3 /repo/evaluation/qwen3.6-35b-a3b/run-docker-ollama-eval.py \
              --scenarios /repo/evaluation/qwen3.6-35b-a3b/coding-scenarios.jsonl \
              --model qwen3.6-35b-a3b-iq1m \
              --backend qwen3.6-35b-a3b-local-docker-ollama \
              --output /repo/evaluation/qwen3.6-35b-a3b/results-docker-ollama-2026-05-09.json'
```

Do not commit downloaded model weights or `.qwen-eval-models/` contents.
