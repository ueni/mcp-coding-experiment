<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# MCP sampling safety adapter

`model_assisted_summary` is a first-slice MCP Sampling adapter for bounded,
client-mediated repository summaries and classifications. It is disabled by
default and no workflow depends on it for release, security, mutation, or policy
decisions.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `MCP_SAMPLING_ENABLED` | `false` | Must be true before the server sends any `sampling/createMessage` request. |
| `MCP_SAMPLING_ALLOWED_USE_CASES` | `summary,classification,workflow_selection` | Allowed advisory purposes. Mutation, credential handling, release approval, and final security conclusions are always denied. |
| `MCP_SAMPLING_MAX_PATHS` | `5` | Maximum repository-relative files per request. |
| `MCP_SAMPLING_MAX_BYTES` | `12000` | Maximum bytes read across all included files before redaction/compression. |
| `MCP_SAMPLING_MAX_CONTEXT_TOKENS` | `2000` | Approximate redacted context-token cap. |
| `MCP_SAMPLING_MAX_OUTPUT_TOKENS` | `512` | Maximum generated tokens requested from the client. |
| `MCP_SAMPLING_MODEL_HINTS` | unset | Optional comma-separated model hints passed as preferences, not requirements. |

The adapter also requires an active MCP request session whose client declares the
`sampling` capability and exposes `create_message`. Otherwise it returns
`disabled` or `unsupported` explicitly and does not call a model.

## Safety boundaries

Before constructing a prompt, the adapter:

- resolves all input paths through `REPO_PATH` and omits paths outside the
  repository;
- denies secret-bearing file paths such as `.env`, private keys, `*.pem`, and
  credentials/secrets files;
- applies the audit redaction patterns to repository text, user questions, and
  returned model text;
- redacts private-key blocks, secret-looking values, and host absolute paths;
- enforces path, byte, estimated context-token, and output-token budgets; and
- sends `include_context="none"` so the sampling request contains only the
  adapter-built, bounded context.

Audit and trace metadata record purpose, status, approval/denial status when
observable, context source refs, redaction codes, prompt digest, and output
digest. They do not store raw prompts, raw model responses, secrets, or full file
contents.

## Online/offline behavior

Sampling is always explicit and client-mediated. In online/cloud-assisted mode,
the client may use its configured model only after its normal user review path.
In offline/onboard-only mode, the server still cannot verify model locality; the
client must keep any sampling call onboard/local if offline privacy is required.
If that cannot be guaranteed, leave `MCP_SAMPLING_ENABLED=false`.

## Human review and advisory-only output

MCP Sampling approval UI is controlled by the client. The adapter marks every
request with `human_review_expected=true` and records denial/approval metadata
when the client exposes it, but it cannot guarantee how every client renders the
review.

Generated text is advisory only. Destructive actions, release readiness, and
security findings must cite raw tool results, tests, scans, diffs, or generated
artifacts; model-assisted summaries cannot be the sole authority.
