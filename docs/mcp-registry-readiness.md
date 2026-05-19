<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# MCP Registry server.json readiness

`server.json` is release/package metadata for the official MCP Registry. It is
separate from the runtime `/.well-known/mcp-server.json` discovery endpoint,
which describes one running HTTP server instance.

## Selected registry identity and package channel

Initial registry identity:

- MCP Registry name: `io.github.ueni/codebase-tooling-mcp`
- Package type: OCI image
- Package channel: `ghcr.io/ueni/codebase-tooling-mcp:<version>`
- Checked-in readiness version: `0.0.0-local-build`

OCI/GHCR is the first channel because this repository already ships as a Docker
image and the server is intended to run against one mounted repository at
`/repo`. The project does not currently publish a first-class npm or PyPI
package, so those package types would add an extra ownership and install surface
before the existing container channel is release-ready.

The OCI ownership marker is the Dockerfile label required by the MCP Registry:

```dockerfile
LABEL io.modelcontextprotocol.server.name="io.github.ueni/codebase-tooling-mcp"
```

The label value must match `server.json.name` exactly.

## Local dry-run/readiness gate

Run the local dry-run before release metadata changes, PR handoff, or registry
publication:

```bash
python3 scripts/registry_readiness.py validate --compact
```

The gate validates the checked-in manifest against the vendored official schema
snapshot at `schemas/mcp-registry-server-2025-12-11.schema.json` and then checks
repository-specific official-registry readiness rules:

- official schema URL and manifest shape;
- supported registry type/base URL/OCI host;
- OCI package identifier and Dockerfile ownership label consistency;
- `_meta` restricted to `io.modelcontextprotocol.registry/publisher-provided`
  and under 4096 serialized bytes;
- no checked-in secret-looking literal values or secret input metadata;
- no host absolute paths in metadata; and
- version consistency between `server.json`, the OCI tag/package version, and
  `source/version_metadata.py` defaults (or an explicit `--expected-version`
  release override).

Regression coverage lives in `tests/test_registry_readiness.py`:

```bash
python3 -m pytest tests/test_registry_readiness.py
```

## Maintainer-gated publishing path

Ordinary CI and pull requests must not publish to the MCP Registry. Publication
is a maintainer action after a real release artifact exists.

Before public publication, maintainers should:

1. Replace the readiness version (`0.0.0-local-build`) with the release version
   in `server.json` and the OCI identifier tag.
2. Ensure the image build uses matching runtime/MCP version arguments rather than
   the local-build defaults.
3. Publish the matching OCI image to `ghcr.io/ueni/codebase-tooling-mcp:<version>`
   with the `io.modelcontextprotocol.server.name` label preserved.
4. Run the readiness gate on the exact release metadata:
   `python3 scripts/registry_readiness.py validate --compact --expected-version "${VERSION}"`.
5. Use `mcp-publisher` only from a maintainer-controlled release workflow or a
   manually approved GitHub Actions environment.

A future release workflow should use GitHub OIDC and an environment with required
reviewers, for example:

```yaml
name: Publish MCP Registry Metadata

on:
  workflow_dispatch:
    inputs:
      release_tag:
        description: Existing release tag, for example v1.2.3
        required: true

jobs:
  publish-mcp-registry:
    runs-on: ubuntu-latest
    environment: mcp-registry-production
    permissions:
      contents: read
      id-token: write
      packages: read
    steps:
      - uses: actions/checkout@v5
      - name: Validate server.json readiness
        run: |
          VERSION="${{ inputs.release_tag }}"
          VERSION="${VERSION#v}"
          python3 scripts/registry_readiness.py validate --compact --expected-version "${VERSION}"
      - name: Install mcp-publisher
        run: |
          curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher
      - name: Authenticate to MCP Registry with OIDC
        run: ./mcp-publisher login github-oidc
      - name: Publish server.json
        run: ./mcp-publisher publish
```

Do not add this as an automatic pull-request or normal branch CI publisher. Keep
actual publication behind maintainer release gates.
