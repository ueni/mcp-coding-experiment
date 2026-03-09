const vscode = require("vscode");

function cfg() {
  return vscode.workspace.getConfiguration("mcpInlineAutocomplete");
}

function makeRequestBody(id, method, params) {
  return JSON.stringify({
    jsonrpc: "2.0",
    id,
    method,
    params,
  });
}

async function postJson(endpoint, payload, timeoutMs, sessionId) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
    };
    if (sessionId) {
      headers["mcp-session-id"] = sessionId;
    }
    const resp = await fetch(endpoint, {
      method: "POST",
      headers,
      body: payload,
      signal: controller.signal,
    });
    const text = await resp.text();
    const nextSessionId = resp.headers.get("mcp-session-id");
    return { ok: resp.ok, status: resp.status, text, nextSessionId };
  } finally {
    clearTimeout(timer);
  }
}

function parsePossiblySseJson(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) {
    return null;
  }
  if (trimmed.startsWith("{")) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return null;
    }
  }

  const dataLines = trimmed
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim())
    .filter((line) => line && line !== "[DONE]");
  for (let i = dataLines.length - 1; i >= 0; i -= 1) {
    try {
      return JSON.parse(dataLines[i]);
    } catch {
      // continue
    }
  }
  return null;
}

function parseToolResult(result) {
  if (!result) {
    return "";
  }
  if (typeof result.completion === "string") {
    return result.completion;
  }
  if (result.structuredContent && typeof result.structuredContent.completion === "string") {
    return result.structuredContent.completion;
  }
  if (Array.isArray(result.content)) {
    for (const chunk of result.content) {
      if (chunk && typeof chunk.text === "string") {
        const text = chunk.text;
        if (text.trim().startsWith("{")) {
          try {
            const parsed = JSON.parse(text);
            if (parsed && typeof parsed.completion === "string") {
              return parsed.completion;
            }
          } catch {
            // keep raw text fallback
          }
        }
        return text;
      }
    }
  }
  return "";
}

class McpHttpClient {
  constructor(endpoint, timeoutMs) {
    this.endpoint = endpoint;
    this.timeoutMs = timeoutMs;
    this.sessionId = "";
    this.nextId = 1;
    this.initialized = false;
  }

  async call(method, params) {
    const id = this.nextId++;
    const payload = makeRequestBody(id, method, params);
    const raw = await postJson(this.endpoint, payload, this.timeoutMs, this.sessionId);
    if (raw.nextSessionId) {
      this.sessionId = raw.nextSessionId;
    }
    if (!raw.ok) {
      throw new Error(`HTTP ${raw.status}: ${raw.text.slice(0, 400)}`);
    }
    const msg = parsePossiblySseJson(raw.text);
    if (!msg) {
      throw new Error("MCP response was empty or not JSON.");
    }
    if (msg.error) {
      throw new Error(msg.error.message || JSON.stringify(msg.error));
    }
    return msg.result;
  }

  async ensureInitialized() {
    if (this.initialized) {
      return;
    }
    await this.call("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "mcp-inline-autocomplete", version: "0.0.1" },
    });
    const payload = JSON.stringify({
      jsonrpc: "2.0",
      method: "notifications/initialized",
      params: {},
    });
    try {
      const raw = await postJson(this.endpoint, payload, this.timeoutMs, this.sessionId);
      if (raw.nextSessionId) {
        this.sessionId = raw.nextSessionId;
      }
    } catch {
      // Non-fatal for tolerant servers.
    }
    this.initialized = true;
  }

  async autocomplete(args) {
    await this.ensureInitialized();
    const result = await this.call("tools/call", {
      name: "autocomplete",
      arguments: args,
    });
    return parseToolResult(result);
  }
}

function languageEnabled(languageId, enabledLanguages) {
  if (!Array.isArray(enabledLanguages) || enabledLanguages.length === 0) {
    return true;
  }
  return enabledLanguages.includes("*") || enabledLanguages.includes(languageId);
}

function textSlices(document, position, maxPrefixChars, maxSuffixChars) {
  const full = document.getText();
  const offset = document.offsetAt(position);
  const start = Math.max(0, offset - maxPrefixChars);
  const end = Math.min(full.length, offset + maxSuffixChars);
  return {
    prefix: full.slice(start, offset),
    suffix: full.slice(offset, end),
  };
}

function activate(context) {
  let client = null;

  function getClient() {
    const endpoint = String(cfg().get("endpoint", "http://localhost:8000/mcp"));
    const timeoutMs = Number(cfg().get("timeoutMs", 1500));
    if (!client || client.endpoint !== endpoint || client.timeoutMs !== timeoutMs) {
      client = new McpHttpClient(endpoint, timeoutMs);
    }
    return client;
  }

  const provider = {
    async provideInlineCompletionItems(document, position) {
      const enabledLanguages = cfg().get("enabledLanguages", ["*"]);
      if (!languageEnabled(document.languageId, enabledLanguages)) {
        return { items: [] };
      }

      const maxPrefixChars = Number(cfg().get("maxPrefixChars", 4000));
      const maxSuffixChars = Number(cfg().get("maxSuffixChars", 1000));
      const minPrefixChars = Number(cfg().get("minPrefixChars", 8));
      const maxTokens = Number(cfg().get("maxTokens", 64));
      const temperature = Number(cfg().get("temperature", 0.1));
      const backend = String(cfg().get("backend", "auto"));
      const model = String(cfg().get("model", ""));
      const { prefix, suffix } = textSlices(document, position, maxPrefixChars, maxSuffixChars);

      if (prefix.trim().length < minPrefixChars) {
        return { items: [] };
      }

      try {
        const completion = await getClient().autocomplete({
          prefix,
          suffix,
          language: document.languageId,
          backend,
          model,
          max_tokens: maxTokens,
          temperature,
          output_profile: "compact",
        });
        if (!completion) {
          return { items: [] };
        }
        return {
          items: [
            new vscode.InlineCompletionItem(
              completion,
              new vscode.Range(position, position)
            ),
          ],
        };
      } catch {
        return { items: [] };
      }
    },
  };

  context.subscriptions.push(
    vscode.languages.registerInlineCompletionItemProvider({ pattern: "**" }, provider)
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("mcpInlineAutocomplete.status", async () => {
      const endpoint = String(cfg().get("endpoint", "http://localhost:8000/mcp"));
      try {
        const c = getClient();
        await c.ensureInitialized();
        vscode.window.showInformationMessage(
          `MCP inline autocomplete ready at ${endpoint}`
        );
      } catch (err) {
        vscode.window.showErrorMessage(
          `MCP inline autocomplete not reachable at ${endpoint}: ${String(err)}`
        );
      }
    })
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
