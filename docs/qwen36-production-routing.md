# Qwen3.6 Production Routing

The production local-coding profile routes quality work to `qwen3.6-35b-a3b:iq1`.

## Default model set

- Quality/default route: `qwen3.6-35b-a3b:iq1`
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

Production deployments may use an equivalent Ollama tag alias by setting both:

```bash
export CODING_DEFAULT_MODEL="your-qwen36-tag"
export CONTINUE_OLLAMA_MODELS="your-qwen36-tag,qwen2.5-coder:1.5b"
```

Keep `OLLAMA_ALLOW_PULL=false` for offline-only startup. If the Qwen3.6 tag is local/private, pre-create it with `ollama create` or mount/seed the model store before startup; GitHub-hosted CI intentionally builds with `OLLAMA_PRELOAD_MODELS=` so CI does not require private GGUF artifacts.

## Continue Agent context window

The Qwen3.6 Continue model profile declares `defaultCompletionOptions.contextLength: 32768`, `maxTokens: 2048`, and `capabilities: [tool_use]`. The devcontainer and text-only Ollama alias use the same `32768` `num_ctx` value (`OLLAMA_CONTEXT_LENGTH` / `OLLAMA_TEXT_ALIAS_NUM_CTX`). Keep these values aligned: a low alias context such as 512 can make Continue Agent mode fail before generation with `Message exceeds context limit` even for trivial prompts once MCP/tool instructions are included.

When `MCP_APPLY_REPO_DEFAULTS=true`, startup refreshes the repository-managed Qwen3.6 Continue profile if it still points at `qwen3.6-35b-a3b:iq1` but lacks the `32768` context window. This preserves custom unrelated model files while repairing stale repo defaults copied before the context-window fix.

## Output hygiene

Qwen3.6 endpoint requests use a chat template and default stop sequences for chat sentinels:

- `<|im_start|>`
- `<|im_end|>`
- `<|endoftext|>`

Reasoning markers are not stop sequences because some Qwen3.6 builds emit a short `<think>...</think>` block before the answer. Server-side output sanitization strips leaked reasoning blocks plus `<think>` / `</think>` and chat sentinel tokens before tool responses are returned.

## Rollback

Safe rollback is configuration-only: set `CODING_DEFAULT_MODEL` and `CONTINUE_OLLAMA_MODELS` to a previously validated local model tag and restore matching Continue model YAML outside the steady-state defaults. Do not reintroduce `qwen2.5-coder:3b` as the repository default unless a new evaluation decision explicitly supersedes the Qwen3.6 production profile.
