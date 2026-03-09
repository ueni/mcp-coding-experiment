<!--
SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt

SPDX-License-Identifier: MIT
-->

# Release Notes and Documentation Policy

## Purpose

Keep behavior, naming, and usage documentation aligned with implementation changes.

## Required with behavior changes

When any of the following changes, update docs in the same change set:

- tool names, arguments, or defaults
- server name, alias examples, image/service names
- environment variables or safety controls
- output/report paths
- onboarding and quickstart commands

## Minimum documentation updates

For relevant changes, update all impacted files:

- `README.md`
- `docs/index.md`
- topic pages under `docs/` (for example `docs/labs.md`, `docs/json-settings.md`)
- troubleshooting entries when failure modes change

## Release notes entry

Each release should include a short notes section containing:

- Added
- Changed
- Fixed
- Breaking Changes (if any)
- Documentation Updates

## Definition of done

A change is not complete unless:

- commands in docs are runnable as written
- names are consistent (`codebase-tooling-mcp`)
- examples include expected output where practical
- docs index links are valid and current

## Verification checklist

- Run quickstart commands from a clean environment.
- Validate endpoint examples (`/mcp`, `/healthz`).
- Validate at least one lab command and report path.
- Confirm no host-specific absolute paths remain in docs.
