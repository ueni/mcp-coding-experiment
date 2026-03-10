<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Troubleshooting

## No tests discovered

Symptom:

```text
Ran 0 tests in 0.000s
NO TESTS RAN
```

Checks:

- Confirm tests are under `tests/` or configured for your runner.
- Confirm filenames match your framework conventions.
- Run collection-only mode:

```bash
pytest --collect-only tests
```

## Mount path issues (`/repo`)

Symptom:

- Tools cannot find expected files.
- Writes fail unexpectedly.

Checks:

- Confirm Docker run uses `-v "$PWD:/repo"`.
- Confirm `REPO_PATH=/repo` (or your chosen mount) matches container mount.

## Port `8000` already in use

Symptom:

```text
bind: address already in use
```

Fix options:

- Stop the process using port `8000`.
- Or map a different host port:

```bash
docker run --rm -p 8001:8000 ... codebase-tooling-mcp
```

Then use `http://localhost:8001/mcp`.

## Git commit identity missing

Symptom:

```text
Please tell me who you are
```

Fix:

```bash
git config user.name "Your Name"
git config user.email "you@example.com"
```

## Permission or mutation denied

Symptom:

- Write or git mutation tools return permission/mutation errors.

Checks:

- Confirm `ALLOW_MUTATIONS=true` when mutation tools are required.
- Keep it `false` for read-only sessions.
- Confirm paths are inside the mounted repository root.

## Default bootstrap files not created

Symptom:

- `.continue/mcpServers/http-mcp-server.yaml` is missing.
- `.config/labs/` is missing.
- `/.build/` was not added to `.gitignore`.

Checks:

- Confirm the repository was opened with the generated `.devcontainer/devcontainer.json`.
- Confirm the container environment includes `MCP_APPLY_REPO_DEFAULTS=true`.
- Rebuild or reopen the devcontainer so the image entrypoint runs again.
