<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Build download cache verification

Docker builds use explicit BuildKit cache mounts for every build-time download
class that the Dockerfile controls:

- `codebase-tooling-apt-cache` -> `/var/cache/apt` for Debian package archives.
- `codebase-tooling-apt-lists` -> `/var/lib/apt/lists` for APT package indexes.
- `codebase-tooling-build-downloads` -> `/var/cache/buildkit/downloads` for
  standalone artifacts such as the Docker repository signing key.
- `codebase-tooling-pip` -> `/var/cache/buildkit/pip` for pip's own cache.
- `codebase-tooling-pip-wheelhouse` -> `/var/cache/buildkit/pip-wheelhouse` for
  requirements-digest and Python-platform keyed wheelhouses used with
  `pip install --no-index`.
- `codebase-tooling-ollama-binary` -> `/var/cache/buildkit/ollama` for the
  versioned Ollama binary archive.
- `codebase-tooling-ollama-models` -> `/var/cache/buildkit/ollama-models` for
  preloaded Ollama model blobs.
- `codebase-tooling-vscode-vsix` -> `/var/cache/buildkit/vscode-vsix` for VSIX
  archives keyed by extension id and optional pinned version.

These cache IDs are stable and do not depend on Dockerfile line numbers or layer
cache hits. If an early Dockerfile edit invalidates a layer, the rerun step still
reads from the same BuildKit cache mount before attempting network access.

## Offline/no-network gate

The build arg `MCP_BUILD_OFFLINE=true` makes required missing cached resources
fail closed instead of reaching the network. Use it only after a warm online build
has populated the cache store:

```bash
docker build \
  --build-arg MCP_BUILD_OFFLINE=true \
  --build-arg OLLAMA_PRELOAD_MODELS= \
  --build-arg VSCODE_PRELOAD_EXTENSIONS= \
  --file source/Dockerfile \
  --tag codebase-tooling-mcp:offline-cache-check \
  source
```

For a full image with models/extensions, omit the empty preload overrides after
the persistent builder cache has those artifacts. On disposable or remote
builders, export and import the BuildKit cache explicitly:

```bash
docker buildx build \
  --cache-to=type=local,dest=.buildx-cache,mode=max \
  --cache-from=type=local,src=.buildx-cache \
  --file source/Dockerfile \
  --tag codebase-tooling-mcp:cache-check \
  --load \
  source
```

To intentionally refresh downloaded resources, run an online build with
`--build-arg MCP_REFRESH_BUILD_DOWNLOAD_CACHE=true`. Refreshing in offline mode
fails because the requested replacement cannot be downloaded.

## Static CI/local audit

Run the fast cache-contract audit locally:

```bash
python3 scripts/build_download_cache_check.py --compact
```

The audit verifies the required stable cache IDs, offline/refresh build args, the
cached download helper, pip wheelhouse install path, and guards around external
network downloads. Unit tests also mutate the first Dockerfile line and assert the
cache IDs remain unchanged, then inject an uncached external `curl` to prove the
audit fails.

CI runs the same script before building the devcontainer image.

## Invalidation rules

- Requirement file content changes intentionally create a new pip wheelhouse key.
- Python version/platform changes intentionally create a new pip wheelhouse key.
- Ollama binary cache paths include requested version, architecture, and archive
  format.
- VSIX cache paths include publisher, extension name, and a version label
  (`latest` unless the extension ref is `publisher.name@version`).
- APT package archives and lists are tied to the configured Debian/Docker APT
  repositories and the persistent BuildKit cache store.

If a cache entry is corrupt, the Dockerfile discards it and re-downloads online;
with `MCP_BUILD_OFFLINE=true`, missing required cache entries fail the build.
