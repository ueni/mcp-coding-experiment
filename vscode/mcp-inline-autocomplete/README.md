# MCP Inline Autocomplete (VS Code Extension)

Inline completion provider for VS Code backed by this repository's MCP server tool `autocomplete`.

## Requirements

- MCP server running at `http://localhost:8000/mcp` (default in this repo's devcontainer).
- VS Code 1.85+.

## Run

1. Open this folder in VS Code: `vscode/mcp-inline-autocomplete`.
2. Press `F5` to launch an Extension Development Host.
3. In the dev host, run command `MCP Inline Autocomplete: Show Status`.
4. Type in any code file to trigger inline completion.

## Settings

- `mcpInlineAutocomplete.endpoint`
- `mcpInlineAutocomplete.timeoutMs`
- `mcpInlineAutocomplete.maxTokens`
- `mcpInlineAutocomplete.temperature`
- `mcpInlineAutocomplete.maxPrefixChars`
- `mcpInlineAutocomplete.maxSuffixChars`
- `mcpInlineAutocomplete.minPrefixChars`
- `mcpInlineAutocomplete.enabledLanguages`
- `mcpInlineAutocomplete.backend`
- `mcpInlineAutocomplete.model`
