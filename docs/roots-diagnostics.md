<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# MCP Roots Diagnostics

`roots_diagnostics()` is a read-only advisory MCP tool for comparing client-advertised MCP roots with the server repository boundary (`REPO_PATH`). It is intended for setup/debugging only: it never grants filesystem access, never relaxes `_resolve_repo_path`, and does not change mutation permissions.

## What it reports

The tool returns `roots_diagnostics.v1` with:

- `fetch.status`: whether roots were fetched (`fetched`) or could not be queried (`unsupported`, `unavailable`, `timeout`, `error`).
- `relationship.classification`: one of `exact_match`, `repo_contains_root`, `root_contains_repo`, `multiple_roots`, `no_overlap`, `unsupported`, `unavailable`, or `error`.
- `roots`: counts, URI scheme summaries, invalid-root counts, redaction markers, and per-root relationship metadata.
- `guidance`: operator-facing next steps for the current state.
- `safety`: confirmation that client paths are redacted and authorization remains `REPO_PATH/_resolve_repo_path`.

Absolute client paths outside the repository are not returned. File roots under the repository are normalized to repository-relative paths; overlapping parent/outside paths are represented with redacted relationship markers.

## Classifications

| Classification | Meaning | Typical action |
|---|---|---|
| `exact_match` | One `file://` client root exactly equals `REPO_PATH`. | Healthy/default state. |
| `repo_contains_root` | The client root is inside the configured repository. | Client context may be narrower than server access; prefer launching from the repo root when possible. |
| `root_contains_repo` | The client root is a parent of `REPO_PATH`. | Server access is still limited to `REPO_PATH`; consider narrowing client roots to the repo. |
| `multiple_roots` | More than one valid `file://` root was advertised. | Inspect per-root relationships and prefer one root matching `REPO_PATH`. |
| `no_overlap` | File roots do not overlap `REPO_PATH`; non-file roots alone also cannot establish overlap. | Reconnect the MCP client from the mounted repository workspace or adjust `REPO_PATH`. |
| `unsupported` | The active session has no roots API or did not advertise roots capability. | Continue relying on repository-boundary checks; no client-specific roots data is available. |
| `unavailable` | No active MCP request/session exists, or no roots were returned. | Call the tool through an MCP client session for request-scoped details. `/healthz` may remain coarse. |
| `error` | Roots data was malformed, timed out, or list-roots failed. | Treat as diagnostic-only failure and keep using `REPO_PATH` enforcement. |

## Transport behavior

For stdio and HTTP MCP tool calls, the tool feature-detects session support before querying `list_roots`. If there is no request-scoped session, no client roots capability, a timeout, malformed root data, or an exception, it returns a diagnostic state instead of failing unrelated tools. Health endpoints may keep reporting coarse process health because they do not necessarily have request/session roots context.
