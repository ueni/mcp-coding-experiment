# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio
import json
import os
import unittest
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


_VERSION_ENV_KEYS = [
    "RUNTIME_IMAGE_VERSION_COMPATIBILITY",
    "RUNTIME_IMAGE_VERSION_FEATURE",
    "RUNTIME_IMAGE_VERSION_BUGFIX",
    "RUNTIME_IMAGE_VERSION_SUFFIX",
    "MCP_CODING_EXPERIMENT_VERSION_COMPATIBILITY",
    "MCP_CODING_EXPERIMENT_VERSION_FEATURE",
    "MCP_CODING_EXPERIMENT_VERSION_BUGFIX",
    "MCP_CODING_EXPERIMENT_VERSION_SUFFIX",
]


class VersionMetadataTest(ServerToolsTestBase):
    def _healthz_payload(self):
        response = asyncio.run(self.server.healthz(None))
        return json.loads(response.body.decode("utf-8"))

    def test_healthz_exposes_default_local_versions_separately(self):
        clean_env = {key: os.environ[key] for key in os.environ if key not in _VERSION_ENV_KEYS}
        with patch.dict(os.environ, clean_env, clear=True):
            payload = self._healthz_payload()

        self.assertEqual(payload["runtime_image_version"], "0.0.0-local-build")
        self.assertEqual(payload["mcp_coding_experiment_version"], "0.0.0-local-build")

    def test_healthz_uses_independent_version_counter_and_suffix_overrides(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_IMAGE_VERSION_COMPATIBILITY": "1",
                "RUNTIME_IMAGE_VERSION_FEATURE": "2",
                "RUNTIME_IMAGE_VERSION_BUGFIX": "3",
                "RUNTIME_IMAGE_VERSION_SUFFIX": "+image.abc123",
                "MCP_CODING_EXPERIMENT_VERSION_COMPATIBILITY": "4",
                "MCP_CODING_EXPERIMENT_VERSION_FEATURE": "5",
                "MCP_CODING_EXPERIMENT_VERSION_BUGFIX": "6",
                "MCP_CODING_EXPERIMENT_VERSION_SUFFIX": "+server.def456",
            },
            clear=False,
        ):
            payload = self._healthz_payload()

        self.assertEqual(payload["runtime_image_version"], "1.2.3+image.abc123")
        self.assertEqual(payload["mcp_coding_experiment_version"], "4.5.6+server.def456")


if __name__ == "__main__":
    unittest.main()
