# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "source" / "build-download-cache.sh"
CHECK_SCRIPT = REPO_ROOT / "scripts" / "build_download_cache_check.py"
DOCKERFILE = REPO_ROOT / "source" / "Dockerfile"


class BuildDownloadCacheTests(unittest.TestCase):
    def _run_helper(self, script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            ["bash", "-euo", "pipefail", "-c", f". {HELPER}; {script}"],
            cwd=REPO_ROOT,
            env=merged_env,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_cached_download_reuses_existing_file_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cache_file = tmp_path / "cache" / "artifact.bin"
            cache_file.parent.mkdir()
            cache_file.write_text("cached", encoding="utf-8")
            marker = tmp_path / "curl-was-called"
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            (fake_bin / "curl").write_text(
                f"#!/usr/bin/env bash\ntouch {marker}\nexit 99\n",
                encoding="utf-8",
            )
            (fake_bin / "curl").chmod(0o755)

            proc = self._run_helper(
                f"build_cache_download {cache_file} https://example.invalid/artifact artifact",
                env={
                    "MCP_BUILD_OFFLINE": "true",
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                },
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(cache_file.read_text(encoding="utf-8"), "cached")
            self.assertFalse(marker.exists(), "cached artifact path must not invoke curl")

    def test_offline_missing_cache_fails_closed_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cache_file = tmp_path / "cache" / "missing.bin"
            marker = tmp_path / "curl-was-called"
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            (fake_bin / "curl").write_text(
                f"#!/usr/bin/env bash\ntouch {marker}\nexit 99\n",
                encoding="utf-8",
            )
            (fake_bin / "curl").chmod(0o755)

            proc = self._run_helper(
                f"build_cache_download {cache_file} https://example.invalid/artifact artifact",
                env={
                    "MCP_BUILD_OFFLINE": "true",
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                },
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("MCP_BUILD_OFFLINE=true", proc.stderr)
            self.assertFalse(marker.exists(), "offline miss must fail before curl")

    def test_refresh_download_writes_cache_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cache_file = tmp_path / "cache" / "artifact.bin"
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            (fake_bin / "curl").write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    out=""
                    while [ "$#" -gt 0 ]; do
                      if [ "$1" = "-o" ]; then
                        out="$2"
                        shift 2
                      else
                        shift
                      fi
                    done
                    echo refreshed >"${out}"
                    """
                ),
                encoding="utf-8",
            )
            (fake_bin / "curl").chmod(0o755)

            proc = self._run_helper(
                f"build_cache_download {cache_file} https://example.invalid/artifact artifact",
                env={
                    "MCP_REFRESH_BUILD_DOWNLOAD_CACHE": "true",
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                },
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(cache_file.read_text(encoding="utf-8").strip(), "refreshed")
            self.assertFalse(list(cache_file.parent.glob("*.tmp.*")))

    def test_dockerfile_cache_contract_survives_first_line_change(self):
        original = subprocess.run(
            [sys.executable, str(CHECK_SCRIPT), "--compact"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        original_payload = json.loads(original.stdout)
        self.assertTrue(original_payload["ok"], original_payload)

        with tempfile.TemporaryDirectory() as tmp:
            mutated = Path(tmp) / "Dockerfile"
            lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
            lines[0] = "# first-line-cache-probe"
            mutated.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(CHECK_SCRIPT), "--dockerfile", str(mutated), "--compact"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            mutated_payload = json.loads(proc.stdout)

        self.assertEqual(mutated_payload["cache_ids"], original_payload["cache_ids"])
        self.assertIn("codebase-tooling-pip-wheelhouse", mutated_payload["cache_ids"])
        self.assertIn("codebase-tooling-build-downloads", mutated_payload["cache_ids"])

    def test_cache_audit_rejects_uncached_external_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text(
                DOCKERFILE.read_text(encoding="utf-8")
                + "\nRUN curl -fsSL https://example.invalid/tool -o /usr/local/bin/tool\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(CHECK_SCRIPT), "--dockerfile", str(dockerfile), "--compact"],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertTrue(
            any("external curl" in problem for problem in payload["problems"]),
            payload["problems"],
        )


if __name__ == "__main__":
    unittest.main()
