# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

import os
import shutil
import subprocess
import unittest
from pathlib import Path


class ContainerSmokeTests(unittest.TestCase):
    def test_selftest_bundle_is_readable(self) -> None:
        self.assertTrue(Path(__file__).is_file())

    def test_runtime_has_expected_repo_env(self) -> None:
        self.assertTrue(bool(os.getenv("REPO_PATH", "/repo")))

    def test_app_user_has_passwordless_sudo(self) -> None:
        self.assertEqual(os.getenv("USER"), "app")
        self.assertIsNotNone(shutil.which("sudo"))
        result = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr.strip() or result.stdout.strip())

    def test_vulkaninfo_is_available_for_gpu_diagnostics(self) -> None:
        self.assertIsNotNone(shutil.which("vulkaninfo"))


    def test_unprivileged_user_namespaces_work_for_nested_sandboxes(self) -> None:
        unshare = shutil.which("unshare")
        self.assertIsNotNone(unshare)
        result = subprocess.run(
            [unshare, "-Ur", "true"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr.strip() or result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
