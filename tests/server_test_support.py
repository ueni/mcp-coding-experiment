# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


def load_server_module():
    module_path = Path(__file__).resolve().parents[1] / "source" / "server.py"
    spec = importlib.util.spec_from_file_location("dev_server", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ServerToolsTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server_module()

    def setUp(self):
        self._orig_cwd = Path.cwd()
        self._orig_repo_path = self.server.REPO_PATH
        self._orig_allow_mutations = self.server.ALLOW_MUTATIONS
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.tmp.name).resolve()

        subprocess.run(["git", "-C", str(self.repo_path), "init", "-b", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo_path), "config", "user.email", "ci@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo_path), "config", "user.name", "CI Bot"],
            check=True,
        )

        (self.repo_path / "src").mkdir(parents=True, exist_ok=True)
        (self.repo_path / "tests").mkdir(parents=True, exist_ok=True)
        (self.repo_path / "docs").mkdir(parents=True, exist_ok=True)
        (self.repo_path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        (self.repo_path / "src" / "sample.py").write_text(
            "import os\n\n"
            "def alpha(x):\n"
            "    return x + 1\n\n"
            "def beta(y):\n"
            "    return alpha(y)\n",
            encoding="utf-8",
        )
        (self.repo_path / "tests" / "test_sample.py").write_text(
            "from src.sample import alpha\n\n"
            "def test_alpha():\n"
            "    assert alpha(1) == 2\n",
            encoding="utf-8",
        )
        (self.repo_path / "tests" / "test_smoke.py").write_text(
            "import unittest\n\n"
            "class SmokeTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (self.repo_path / "docs" / "a.md").write_text("hello world\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo_path), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo_path), "commit", "-m", "init"], check=True)

        self.server.REPO_PATH = self.repo_path
        self.server.ALLOW_MUTATIONS = True
        os.chdir(self.repo_path)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        self.server.REPO_PATH = self._orig_repo_path
        self.server.ALLOW_MUTATIONS = self._orig_allow_mutations
        self.tmp.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repo_path), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def write_repo_text(self, rel_path: str, content: str) -> Path:
        path = self.repo_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def commit_all(self, message: str) -> None:
        self.git("add", ".")
        self.git("commit", "-m", message)
