# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

"""Version metadata helpers for runtime and MCP server surfaces."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_VERSION_COMPATIBILITY = "0"
DEFAULT_VERSION_FEATURE = "0"
DEFAULT_VERSION_BUGFIX = "0"
DEFAULT_VERSION_SUFFIX = "-local-build"


@dataclass(frozen=True)
class VersionMetadata:
    """Independent semantic-ish version counters plus a build suffix."""

    compatibility: str = DEFAULT_VERSION_COMPATIBILITY
    feature: str = DEFAULT_VERSION_FEATURE
    bugfix: str = DEFAULT_VERSION_BUGFIX
    suffix: str = DEFAULT_VERSION_SUFFIX

    @property
    def rendered(self) -> str:
        return f"{self.compatibility}.{self.feature}.{self.bugfix}{self.suffix}"


def _env_value(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def version_metadata_from_env(prefix: str) -> VersionMetadata:
    """Load version counters from ``<prefix>_VERSION_*`` environment variables."""

    return VersionMetadata(
        compatibility=_env_value(
            f"{prefix}_VERSION_COMPATIBILITY", DEFAULT_VERSION_COMPATIBILITY
        ),
        feature=_env_value(f"{prefix}_VERSION_FEATURE", DEFAULT_VERSION_FEATURE),
        bugfix=_env_value(f"{prefix}_VERSION_BUGFIX", DEFAULT_VERSION_BUGFIX),
        suffix=_env_value(f"{prefix}_VERSION_SUFFIX", DEFAULT_VERSION_SUFFIX),
    )


def runtime_image_version() -> VersionMetadata:
    return version_metadata_from_env("RUNTIME_IMAGE")


def mcp_coding_experiment_version() -> VersionMetadata:
    return version_metadata_from_env("MCP_CODING_EXPERIMENT")
