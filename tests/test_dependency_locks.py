# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.server_test_support import ServerToolsTestBase


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DependencyLockTests(ServerToolsTestBase):
    def test_dependency_locks_check_is_current(self):
        proc = subprocess.run(
            [sys.executable, "scripts/dependency_lock.py", "check", "--compact"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["manifest_digest"].startswith("sha256:"))
        self.assertGreater(payload["sections"]["runtime"]["package_count"], 0)
        self.assertGreater(payload["sections"]["runtime"]["hash_count"], 0)

    def test_dependency_locks_detect_stale_requirement_inputs(self):
        lock_inputs = (
            "requirements.txt",
            "requirements-embedding.txt",
            "requirements-coding-tools.txt",
        )
        lock_artifacts = (
            *lock_inputs,
            "requirements.lock",
            "requirements-embedding.lock",
            "requirements-coding-tools.lock",
            "dependency-locks.json",
        )
        for stale_input in lock_inputs:
            with self.subTest(stale_input=stale_input), tempfile.TemporaryDirectory() as tmp:
                temp_root = Path(tmp)
                temp_source = temp_root / "source"
                temp_source.mkdir()
                for artifact in lock_artifacts:
                    shutil.copy2(PROJECT_ROOT / "source" / artifact, temp_source / artifact)
                with (temp_source / stale_input).open("a", encoding="utf-8") as handle:
                    handle.write("\n# stale-lock-test\n")

                proc = subprocess.run(
                    [
                        sys.executable,
                        "scripts/dependency_lock.py",
                        "check",
                        "--compact",
                        "--project-root",
                        str(temp_root),
                    ],
                    cwd=PROJECT_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                payload = json.loads(proc.stdout)
                section_name = {
                    "requirements.txt": "runtime",
                    "requirements-embedding.txt": "embedding",
                    "requirements-coding-tools.txt": "coding-tools",
                }[stale_input]
                self.assertFalse(payload["sections"][section_name]["ok"])
                self.assertIn(
                    "input requirements digest changed; refresh lock",
                    payload["sections"][section_name]["errors"],
                )

    def test_runtime_state_exposes_dependency_lock_digest_status(self):
        out = self.server.runtime_state()
        locks = out["dependency_locks"]
        self.assertEqual(locks["schema"], "dependency_locks.runtime.v1")
        self.assertTrue(locks["ok"], locks)
        self.assertTrue(locks["manifest_digest"].startswith("sha256:"))
        self.assertIn("runtime", locks["sections"])
        self.assertGreater(locks["sections"]["runtime"]["package_count"], 0)

    def test_self_test_includes_dependency_lock_status(self):
        out = self.server.self_test(
            runner="unittest",
            target="tests/test_smoke.py",
            verbose=False,
            timeout_seconds=60,
        )
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["dependency_locks"]["ok"], out["dependency_locks"])
        self.assertIn("runtime", out["dependency_locks"]["sections"])
