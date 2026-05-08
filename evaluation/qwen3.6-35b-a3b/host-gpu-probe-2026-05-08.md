<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Host GPU Probe for Qwen3.6-35B-A3B Evaluation

Date: 2026-05-08
Host: `user-thinkpad-t14`
Target: Lenovo ThinkPad T14 Gen1 AMD

## Commands run

```bash
git status --short
git branch --show-current
git remote -v
uname -a
lscpu | sed -n '1,28p'
free -h
df -h .
lspci -nn | grep -Ei 'vga|3d|display|amd|nvidia|intel'
ls -l /dev/dri
getfacl -p /dev/dri/renderD128 /dev/dri/card1
vulkaninfo --summary
clinfo
python3 --version
python3 - <<'PY'
try:
 import torch
 print('torch', torch.__version__)
 print('cuda available', torch.cuda.is_available())
 print('hip', getattr(torch.version,'hip',None))
except Exception as e: print('torch import error', repr(e))
try:
 import llama_cpp
 print('llama_cpp', llama_cpp.__version__)
except Exception as e: print('llama_cpp import error', repr(e))
PY
command -v ollama || true
command -v llama-server || true
command -v llama-cli || true
powerprofilesctl get
cat /sys/class/power_supply/AC/online
cat /sys/class/power_supply/BAT0/status
cat /sys/class/power_supply/BAT0/capacity
```

## Hardware/runtime facts observed

- OS/kernel: `Linux user-thinkpad-t14 7.0.0-15-generic #15-Ubuntu SMP PREEMPT_DYNAMIC Wed Apr 22 16:06:43 UTC 2026 x86_64 GNU/Linux`.
- CPU: AMD Ryzen 7 PRO 4750U with Radeon Graphics, 8 cores / 16 threads.
- RAM: 28 GiB total, 23 GiB available at probe time.
- Swap: 975 MiB total.
- Disk at repo filesystem: 914 GiB total, 721 GiB used, 147 GiB available.
- GPU PCI device: `07:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Renoir [Radeon Vega Series / Radeon Vega Mobile Series] [1002:1636] (rev d1)`.
- DRM devices present: `/dev/dri/card1`, `/dev/dri/renderD128`.
- DRM ACLs grant `user:user:rw-` on both card and render device.
- Vulkan runtime present. `vulkaninfo --summary` reports `AMD Radeon Graphics (RADV RENOIR)`, integrated GPU, Mesa RADV `26.0.3-1ubuntu1`.
- AMDVLK ICD emits `VK_ERROR_INITIALIZATION_FAILED` during probing and is skipped, but RADV is usable.
- OpenCL runtime is not usable for this GPU: `clinfo` reports `Number of platforms 0`.
- ROCm CLI/runtime tools are not installed (`rocminfo` absent).
- NVIDIA runtime is not applicable (`nvidia-smi` absent).
- Ollama is not installed (`command -v ollama` failed).
- llama.cpp CLI/server binaries are not installed (`llama-server`, `llama-cli`, `llamafile` absent).
- Python model runtimes are not installed in the active interpreter: `torch` and `llama_cpp` imports fail with `ModuleNotFoundError`.
- Power profile: `performance`.
- Power state: AC offline, battery discharging at 64%.

## Model acquisition size check

No model weights were downloaded.

Hugging Face API metadata for `unsloth/Qwen3.6-35B-A3B-GGUF` reports repository SHA `a483e9e6cbd595906af30beda3187c2663a1118c`, not gated. Relevant GGUF file sizes:

- `Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf`: 10,756,586,464 bytes (~10.0 GiB).
- `Qwen3.6-35B-A3B-UD-IQ2_M.gguf`: 11,522,702,304 bytes (~10.7 GiB).
- `Qwen3.6-35B-A3B-UD-IQ4_XS.gguf`: 17,730,509,792 bytes (~16.5 GiB).
- `Qwen3.6-35B-A3B-UD-IQ4_NL.gguf`: 18,040,888,288 bytes (~16.8 GiB).
- `Qwen3.6-35B-A3B-MXFP4_MOE.gguf`: 21,706,144,736 bytes (~20.2 GiB).
- `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`: 22,134,528,992 bytes (~20.6 GiB).
- `Qwen3.6-35B-A3B-Q8_0.gguf`: 36,903,140,320 bytes (~34.4 GiB).

Because the task says to stop before a very large model download, the evaluation did not fetch these files.

## Blocker

The direct host shell has a usable AMD integrated GPU through Vulkan/RADV but no installed host-level inference backend and no local Qwen3.6-35B-A3B model weights. This is not a blocker for the official evaluation runtime because `source/Dockerfile` / `.devcontainer/devcontainer.json` now define the supported Docker GPU path with Ollama Vulkan.

The remaining blocker is the absent Qwen3.6-35B-A3B weights. GPU-backed target inference cannot be started until ueni authorizes a specific large model download/pull (~10.0-20.6 GiB for practical GGUF quantizations) or provides the target model weights/cache.

CPU fallback was not attempted because the clarified requirement says GPU must be used.

## Smallest fix needed

1. Confirm the acceptable quantization/model tag and authorize the model download size, minimally `UD-IQ2_XXS` (~10.0 GiB) or preferably a higher-quality quant if storage/RAM are acceptable, or provide a ready model cache/weights.
2. Use the official Docker/devcontainer runtime documented in `evaluation/qwen3.6-35b-a3b/docker-gpu-runtime-2026-05-08.md`.
3. Run on AC power; battery-only performance and thermal throttling would make throughput measurements less representative.
4. Start the target model with GPU offload enabled and rerun the scenario manifest, recording first-token latency, end-to-end latency, tokens/sec, RAM, and GPU observations.
