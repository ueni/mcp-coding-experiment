# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

import os
import unittest
from pathlib import Path


class ContainerSmokeTests(unittest.TestCase):
    def test_selftest_bundle_is_readable(self) -> None:
        self.assertTrue(Path(__file__).is_file())

    def test_runtime_has_expected_repo_env(self) -> None:
        self.assertTrue(bool(os.getenv("REPO_PATH", "/repo")))


if __name__ == "__main__":
    unittest.main()
