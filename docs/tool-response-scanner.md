<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# MCP tool-response scanner

The tool-response scanner is an opt-in enforcement point in the explicit Agent API
Proxy. It scans `role: "tool"` chat messages before they are forwarded into the
agent/model context through `/v1/chat/completions`.

It is disabled by default to preserve existing clients and offline-safe behavior.
Enable it only when an operator wants deterministic handling for risky tool
responses.

## Configuration

Set `MCP_TOOL_RESPONSE_SCANNER_MODE`:

- `off` (default): do not scan or alter tool-response messages.
- `log`: scan and include redacted scanner metadata in `agent_proxy.policy`; do
  not alter forwarded content.
- `sanitize`: redact secret-looking values, local absolute paths, and email
  addresses/PII, and replace prompt-injection-like instruction lines before
  forwarding to the model.
- `block`: reject the chat completion with `403` before any provider call when a
  risky tool-response message is detected.

Optional bound:

- `MCP_TOOL_RESPONSE_SCANNER_MAX_CHARS` (default `12000`): maximum characters per
  tool-response message considered by the deterministic scanner.

## First-slice risk classes

The first slice is local/offline and heuristic. It reports only bounded metadata,
not raw tool-response text. Detection covers:

- prompt-injection-like instructions such as overriding previous/system/developer
  instructions, role switching, or tool manipulation;
- credential/secret leakage and credential exfiltration instructions;
- email-address PII in text-bearing tool responses;
- sensitive local absolute paths;
- unsafe repository or credential exfiltration hints.

This scanner does not replace the existing non-blocking untrusted-content signal
metadata. Those signals remain advisory. The scanner reuses the same deterministic
prompt-injection signal shapes where practical and adds explicit enforcement
outcomes: `LOG`, `SANITIZE`, and `BLOCK`.

## Operator notes

Use `log` first to measure false positives without changing model context. Move to
`sanitize` for online provider calls where tool output may include secrets or
local paths. Use `block` for stricter deployments where suspicious tool responses
must never enter model context.

Scanner reports intentionally include only counts, categories, severity, bounds,
and privacy flags. They do not include raw excerpts, secrets, email addresses,
or host paths.
