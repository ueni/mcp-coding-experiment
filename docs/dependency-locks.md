<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Hash-pinned Python dependency locks

The repository keeps pip-compatible, hash-pinned dependency locks under
`source/` for reproducible MCP runtime builds:

- `source/requirements.lock` - default runtime dependencies from
  `source/requirements.txt`.
- `source/requirements-embedding.lock` - optional `sentence-transformers`
  backend dependencies from `source/requirements-embedding.txt`.
- `source/requirements-coding-tools.lock` - coding virtualenv tool dependencies
  from `source/requirements-coding-tools.txt`.
- `source/dependency-locks.json` - compact install manifest with source and lock
  SHA-256 digests, package counts, and target metadata.

The lock files are generated artifacts, but they are checked in intentionally so
Docker builds can opt into `pip install --require-hashes` without resolving new
transitive packages at build time.

## Check locks

Run this before changing dependency pins or Docker coding-tool pins:

```bash
python3 scripts/dependency_lock.py check --compact
```

The check fails when any `source/requirements*.txt` input no longer matches the
manifest, when a lock file was edited by hand, or when a lock line is not exact
and hash-pinned.

CI runs the same check before building the devcontainer image. `self_test` and
`runtime_state` also expose a compact `dependency_locks` status with manifest,
lock digests, package counts, and stale-lock errors.

## Refresh locks

Refresh in the same Python/runtime family used by the Docker image:

```bash
docker run --rm \
  -v "$PWD:/repo" \
  -w /repo \
  python:3.13-slim-trixie \
  python scripts/dependency_lock.py refresh --compact
```

Review the changed lock files and manifest before committing. The refresh command
uses pip's resolver report and writes exact `name==version --hash=sha256:...`
rows for every resolved transitive package.

## Locked Docker builds

Locked installs are opt-in so normal local builds keep their current behavior:

```bash
docker build \
  --build-arg MCP_USE_LOCKED_DEPS=true \
  -t codebase-tooling-mcp \
  ./source
```

When `MCP_USE_LOCKED_DEPS=true`, the Dockerfile first validates
`source/dependency-locks.json` against the copied requirements and lock files,
then installs with `pip install --require-hashes --only-binary=:all:`. Missing
hashes, stale lock metadata, source-only distributions, or resolver drift fail
the build closed.

Optional embeddings remain separate. To build the larger sentence-transformers
stack with hashes:

```bash
docker build \
  --build-arg MCP_USE_LOCKED_DEPS=true \
  --build-arg INSTALL_SENTENCE_TRANSFORMERS=true \
  -t codebase-tooling-mcp \
  ./source
```

## Offline bootstrap

The locks can seed an offline wheelhouse for non-Docker or pre-cached Docker
bootstrap flows:

```bash
mkdir -p wheelhouse/runtime wheelhouse/coding-tools
python3 -m pip download --require-hashes --only-binary=:all: \
  -r source/requirements.lock \
  -d wheelhouse/runtime
python3 -m pip download --require-hashes --only-binary=:all: \
  -r source/requirements-coding-tools.lock \
  -d wheelhouse/coding-tools

python3 -m pip install --no-index --find-links wheelhouse/runtime \
  --require-hashes --only-binary=:all: -r source/requirements.lock
```

For optional embeddings, prefetch `source/requirements-embedding.lock` into a
separate wheelhouse. That stack is intentionally not part of the default image
because it can pull a large PyTorch/CUDA dependency set.
