# Optional Qwen3.6 High-Quality Profile

The production local-development default now routes Agent/MCP software-development work to `llama3.1:8b`. Qwen3.6 remains available only as a manual high-quality chat/edit/apply profile for stronger hosts.

## Default model set

- Quality/default route: `llama3.1:8b`
- Continue Agent/tool-calling route: `llama3.1:8b`
- Optional high-quality chat/edit route: `qwen3.6-35b-a3b:iq1`
- Micro fast path: `qwen2.5-coder:1.5b`

The micro model is retained only for explicit `task="micro_coding"` requests and short auto-routed coding prompts. The previous steady-state `qwen2.5-coder:3b` default and specialist small models (`phi4-mini`, `phi4-mini-reasoning`, `llama3.2`, `deepscaler`, `deepseek-r1`, and Granite router/vision/security profiles) are intentionally no longer part of the checked-in model routing or bootstrap lists.

## Local Qwen3.6 tag

The evaluated local tag is:

```text
qwen3.6-35b-a3b:iq1
```

It was created from:

```text
.qwen-eval-models/Qwen3.6-35B-A3B-UD-IQ1_M.gguf
```

Stronger hosts may opt into Qwen3.6 by pre-creating an equivalent Ollama tag alias and setting both:

```bash
export CODING_DEFAULT_MODEL="llama3.1:8b"
export CONTINUE_OLLAMA_MODELS="llama3.1:8b,qwen2.5-coder:1.5b,your-qwen36-tag"
```

Keep `OLLAMA_ALLOW_PULL=false` for offline-only startup. If the Qwen3.6 tag is local/private, pre-create it with `ollama create` or mount/seed the model store before startup; GitHub-hosted CI intentionally builds with `OLLAMA_PRELOAD_MODELS=` so CI does not require private GGUF artifacts.

## Continue Agent model and context window

The optional Qwen3.6 Continue model profile declares `defaultCompletionOptions.contextLength: 32768` and `maxTokens: 2048`, but it does not advertise `tool_use`. The local `qwen3.6-35b-a3b:iq1` Ollama tag rejects tool calls with `does not support tools`, so Continue Agent mode should select the separate `Coding Agent - Llama 3.1 8B` profile (`llama3.1:8b`), which is the repository-provided tool-calling fallback. The checked-in devcontainer includes `llama3.1:8b` in `OLLAMA_PRELOAD_MODELS`, sets `CODING_DEFAULT_MODEL=llama3.1:8b` and `CODING_AGENT_MODEL=llama3.1:8b`, and the runtime defaults include only Llama 3.1 plus the micro model in `CONTINUE_OLLAMA_MODELS` so bootstrap/ensure paths do not pull or require Qwen3.6 by default.

The devcontainer and text-only Ollama alias use the same `32768` `num_ctx` value (`OLLAMA_CONTEXT_LENGTH` / `OLLAMA_TEXT_ALIAS_NUM_CTX`). Keep these values aligned: a low alias context such as 512 can make Continue Agent mode fail before generation with `Message exceeds context limit` even for trivial prompts once MCP/tool instructions are included.

Hardware limits matter. `llama3.1:8b` Agent mode may be marginal on a ThinkPad T14 Gen 1 AMD, especially with 16GB RAM while VS Code, the devcontainer, and Ollama share memory at a 32768 context. Use 32GB RAM, or equivalent Docker memory allocation, as the recommended target for local 8B Agent mode. On 16GB-class hosts, reduce `OLLAMA_CONTEXT_LENGTH` / `OLLAMA_TEXT_ALIAS_NUM_CTX` to 8192 or 16384, preload fewer models, or explicitly configure a smaller verified tool-capable Agent model when one is available. The repo does not currently ship a smaller verified tool-capable Agent default, so the Llama 3.1 selection is explicit rather than a claim that it is comfortable on all laptop profiles. Keep the Qwen3.6 35B route limited to chat/edit/apply, and do not assume it is practical on a T14 Gen 1 AMD unless model storage is preloaded/accelerated and memory is sufficient.

When `MCP_APPLY_REPO_DEFAULTS=true`, startup refreshes the repository-managed Continue defaults, including the optional Qwen3.6 profile, the `Coding Agent - Llama 3.1 8B` default profile, and routing entries. This preserves custom unrelated model files while repairing stale repo defaults copied before the context/tool-capability fixes. Existing host or repository `.continue` config may remain stale until refreshed; after pulling these defaults, rebuild/reopen the devcontainer and keep `MCP_APPLY_REPO_DEFAULTS=true`, rerun `setup-repository.sh`, or manually copy `source/defaults/continue` into `.continue` so the host-visible Continue config picks up the Llama agent profile.

## Output hygiene

Qwen3.6 endpoint requests use a chat template and default stop sequences for chat sentinels:

- `<|im_start|>`
- `<|im_end|>`
- `<|endoftext|>`

Reasoning markers are not stop sequences because some Qwen3.6 builds emit a short `<think>...</think>` block before the answer. Server-side output sanitization strips leaked reasoning blocks plus `<think>` / `</think>` and chat sentinel tokens before tool responses are returned.

## Rollback

Safe rollback is configuration-only: set `CODING_DEFAULT_MODEL` and `CONTINUE_OLLAMA_MODELS` to a previously validated local model tag and restore matching Continue model YAML outside the steady-state defaults. Do not reintroduce Qwen3.6, `qwen2.5-coder:3b`, or other large/specialist models as repository defaults unless a new evaluation decision explicitly supersedes the Llama 3.1 local-development profile.
