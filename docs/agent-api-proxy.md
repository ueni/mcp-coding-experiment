<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Explicit Agent API Proxy

`codebase-tooling-mcp` includes an opt-in OpenAI-compatible agent API proxy for clients that deliberately set this server as their `base_url`.

The proxy is disabled by default. It is not a hidden MITM, TLS interception layer, provider credential capture path, or transparent network interceptor. Clients must explicitly send requests to:

```text
POST /v1/chat/completions
```

The first slice supports OpenAI-style chat completions, including `stream: true` Server-Sent Events chunks and final `data: [DONE]` semantics.

## Minimal local/offline configuration

```bash
export MCP_AGENT_PROXY_ENABLED=true
export MCP_AGENT_PROXY_ALLOW_ONLINE=false
export MCP_AGENT_PROXY_NO_NETWORK=true
```

With online disabled or no-network mode enabled, requests route to the local/offline facade and no provider request is made. The response includes `agent_proxy.routing` metadata showing the selected backend and reason.

## Explicit online forwarding configuration

Online forwarding is allowed only when all required controls pass:

```bash
export MCP_AGENT_PROXY_ENABLED=true
export MCP_AGENT_PROXY_ALLOW_ONLINE=true
export MCP_AGENT_PROXY_PROVIDER_BASE_URL="https://provider.example/v1"
export MCP_AGENT_PROXY_PROVIDER_API_KEY="..."
export MCP_AGENT_PROXY_MODEL_ALLOWLIST="gpt-4.1-mini,gpt-4o-mini"
export MCP_AGENT_PROXY_TIMEOUT_SECONDS=30
export MCP_AGENT_PROXY_MAX_INPUT_TOKENS=12000
export MCP_AGENT_PROXY_MAX_OUTPUT_TOKENS=4096
export MCP_AGENT_PROXY_MAX_COST_USD=0.25
```

No provider URL is configured by default. Online calls are blocked unless online mode is explicitly enabled, a provider endpoint is configured, and the requested model matches `MCP_AGENT_PROXY_MODEL_ALLOWLIST`.

## Privacy behavior

Before an online provider call, the proxy applies local-only prompt transformation:

- configured sensitive terms from `MCP_AGENT_PROXY_ANONYMIZE_TERMS` become request-local placeholders;
- email addresses and absolute host paths become request-local placeholders;
- secrets, passwords, API keys, bearer/JWT-like tokens, and common provider token shapes are irreversibly redacted;
- placeholder mappings remain in process memory only and are never written to audit logs;
- safe placeholders are de-anonymized on return, while redacted secret placeholders remain `[REDACTED_SECRET]`.

The proxy stores no raw prompts or raw responses in disclosure audit events by default.

## Disclosure audit and fail-closed mode

Every online provider call writes a local disclosure audit event to:

```text
.codebase-tooling-mcp/audit/agent_proxy_disclosures.jsonl
```

The event contains trace ID, backend/model, routing reason, policy limits, prompt/response digests, placeholder inventory counts, and privacy flags. It does not contain provider keys, authorization headers, raw prompts, raw responses, or placeholder mappings.

Strict audit mode is enabled by default (`MCP_AGENT_PROXY_STRICT_DISCLOSURE_AUDIT=true`). If the disclosure audit event cannot be written, online forwarding is blocked before the provider call.

Summaries are available through the protected endpoint:

```text
GET /v1/agent-proxy/disclosures?trace_id=<trace>
```

## Memory capture gate

Memory capture is disabled by default:

```bash
export MCP_AGENT_PROXY_MEMORY_CAPTURE_ENABLED=false
```

When enabled, the proxy records compact redacted summaries only: trace ID, backend, routing reason, and prompt/response digests. Raw conversations are not stored. By default memory capture also requires mutation mode (`ALLOW_MUTATIONS=true`), otherwise it is skipped and audited.

## Inspecting routing controls

The protected status endpoint returns current proxy controls without exposing secrets:

```text
GET /v1/agent-proxy/status
```

Use it to verify whether online forwarding, no-network mode, model allowlists, token/cost/time limits, strict audit mode, anonymization, and memory capture gates are active.
