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

## Repository-local YAML routing configuration

The Model Fallback assistant can create or update the local runtime file:

```text
.codebase-tooling-mcp/agent-proxy.yaml
```

That path is ignored by git because it is user-specific runtime state. A sanitized non-runtime reference is checked in at [`docs/agent-proxy-config.example.yaml`](agent-proxy-config.example.yaml). Do not store raw API keys in YAML; `apiKey` is only a symbolic Continue secret reference.

Minimal runtime YAML defaults to `model-fallback` until a real provider, model, endpoint, and required Continue secret reference are configured:

```yaml
agent_proxy:
  enabled: true
  allow_online: false
  provider: model-fallback
  model: model-fallback
  apiBase: ""
  apiType: ""
  apiVersion: ""
  apiKey: ""
```

Existing `MCP_AGENT_PROXY_*` environment variables remain supported and override the YAML values when present. Provider API keys are never stored raw in YAML; keyed providers use a Continue secret reference such as `${{ secrets.AZURE_OPENAI_API_KEY }}` and route to fallback until that secret is usable.

## Minimal local/offline configuration

```bash
export MCP_AGENT_PROXY_ENABLED=true
export MCP_AGENT_PROXY_ALLOW_ONLINE=false
export MCP_AGENT_PROXY_NO_NETWORK=true
```

With online disabled or no-network mode enabled, requests route to the local/offline facade and no provider request is made. The response includes `agent_proxy.routing` metadata showing the selected backend and reason. Agent/reasoning facade metadata is proxy-generated (`chat-completions-controlled-facade.v1`) and does not claim that an upstream chat provider has native agent mode.

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

No provider URL is configured by default. Online calls are blocked unless online mode is explicitly enabled, a provider endpoint is configured, the requested model matches the provider-style YAML `model` (or the legacy YAML/env allowlist), and any required provider secret resolves from a Continue secret or `MCP_AGENT_PROXY_PROVIDER_API_KEY`.

## Local reversible anonymisation profile

Before online target-LLM forwarding, the proxy applies `local-reversible-anonymization.v2`. This is a deterministic local transformer, not a remote service dependency. It runs before disclosure audit persistence and before the provider request is made. The default online mode is `balanced`.

Operator controls:

```bash
export MCP_AGENT_PROXY_ANONYMIZATION_MODE=balanced   # balanced | strict | off
export MCP_AGENT_PROXY_ANONYMIZE_TERMS="Customer Alpha,NDA Codename"
export MCP_AGENT_PROXY_ANONYMIZATION_MAX_PLACEHOLDERS=512
export MCP_AGENT_PROXY_STRICT_NDA_FAIL_CLOSED=true
```

Modes:

- `balanced` (default): anonymises configured NDA terms and high-confidence identifiers while preserving code, prose, role hints, and task shape for useful model quality.
- `strict`: additionally anonymises broader likely person-name matches and fails closed for NDA-sensitive inputs when no safe transformation happened or placeholder bounds are exceeded. Use this for high-NDA workflows.
- `off`: disables reversible anonymisation. Secret redaction still applies, but online use with NDA-sensitive inputs should be avoided unless an operator has made an explicit risk decision.

The transformer replaces configured NDA terms plus likely organisations, people, project/customer names, emails, host paths, URLs/domains, repository remotes/slugs, branch names, and ticket IDs with stable typed placeholders within the request, such as `__MCP_ANON_ORG_0001__`, `__MCP_ANON_PERSON_0001__`, `__MCP_ANON_REPO_0001__`, `__MCP_ANON_PATH_0001__`, and `__MCP_ANON_TICKET_0001__`. Typed placeholders keep enough signal for the target model to reason about roles and relationships without receiving raw customer/company identifiers. Secrets, passwords, API keys, bearer/JWT-like tokens, and common provider token shapes become irreversible `__MCP_REDACTED_SECRET_*__` placeholders and are never deanonymised.

Mappings are request-local, process-memory only, bounded by `MCP_AGENT_PROXY_ANONYMIZATION_MAX_PLACEHOLDERS`, and never sent to target LLMs or written to audit/disclosure files. Normal and streaming responses are deanonymised locally before reaching the caller; streaming deanonymisation keeps a small local placeholder-boundary buffer so placeholders split across chunks are restored. Redacted secret placeholders remain `[REDACTED_SECRET]`.

This differs from disclosure audit redaction: audit redaction protects persisted evidence/log records, while the anonymisation profile protects data before it leaves the host for a target LLM. The audit stores compact privacy evidence only (profile/mode/version, counts/categories, confidence status, digests, and disclosure receipts) and stores no raw prompts, raw responses, configured NDA terms, secrets, or reversible mappings.

Known limits: deterministic local detection is conservative. Generic nouns, ambiguous short names, values embedded in unusual encodings, or domain-specific identifiers may require `MCP_AGENT_PROXY_ANONYMIZE_TERMS` or strict/local-only operation. For maximum NDA protection, configure sensitive customer/project terms and use `strict` or `MCP_AGENT_PROXY_NO_NETWORK=true` when confidence is low.

## Disclosure audit and fail-closed mode

Every online provider call writes a local disclosure audit event to:

```text
.codebase-tooling-mcp/audit/agent_proxy_disclosures.jsonl
```

The audit line is a durable buyer/auditor-facing evidence packet, not only an internal debug log. It contains trace/request ID, workflow/task ID when supplied, provider/model route, policy decision and reason (`online_allowed`, anonymizer profile, offline/no-network controls, and limits), canonical input digest, provider/anonymized input digest, redaction/anonymization result, output digest, memory-admission state, tool/repo context boundary, disclosure review/cure state, and a deterministic disclosure receipt digest for regression checks. Secret/token/password redactions are reported as `opaque_redactions` counts rather than raw values. It does not contain provider keys, authorization headers, raw prompts, raw responses, raw repository paths, NDA terms, or placeholder mappings.

Strict audit mode is enabled by default (`MCP_AGENT_PROXY_STRICT_DISCLOSURE_AUDIT=true`). If the disclosure audit event cannot be written, online forwarding is blocked before the provider call.

Summaries are available through the protected endpoint. The summary returns event and trace counts, disclosure categories, backend counts, evidence packet counts, and stable disclosure receipt digests without returning raw prompts or responses:

```text
GET /v1/agent-proxy/disclosures?trace_id=<trace>&since=<iso8601>&until=<iso8601>
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

Use it to verify whether online forwarding, no-network mode, provider/model/API-base state, secret-reference state, model allowlists, token/cost/time limits, policy/anonymization/facade versions, strict audit mode, anonymization, and memory capture gates are active.
