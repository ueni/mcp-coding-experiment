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
            self.assertFalse(list(cache_file.parent.glob("*.part")))

    def test_failed_download_can_resume_stable_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cache_file = tmp_path / "cache" / "artifact.bin"
            counter = tmp_path / "curl-count"
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            (fake_bin / "curl").write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    count=0
                    if [ -f {counter} ]; then
                      count="$(cat {counter})"
                    fi
                    count=$((count + 1))
                    echo "${{count}}" > {counter}
                    out=""
                    saw_continue=false
                    while [ "$#" -gt 0 ]; do
                      case "$1" in
                        --continue-at)
                          [ "$2" = "-" ] || exit 64
                          saw_continue=true
                          shift 2
                          ;;
                        -o)
                          out="$2"
                          shift 2
                          ;;
                        *)
                          shift
                          ;;
                      esac
                    done
                    [ "${{saw_continue}}" = "true" ] || exit 65
                    if [ "${{count}}" -eq 1 ]; then
                      mkdir -p "$(dirname "${{out}}")"
                      echo partial >"${{out}}"
                      exit 56
                    fi
                    grep -q partial "${{out}}" || exit 66
                    echo complete >"${{out}}"
                    """
                ),
                encoding="utf-8",
            )
            (fake_bin / "curl").chmod(0o755)

            proc = self._run_helper(
                textwrap.dedent(
                    f"""\
                    set +e
                    build_cache_download {cache_file} https://example.invalid/artifact artifact
                    first_rc=$?
                    set -e
                    [ "${{first_rc}}" -ne 0 ]
                    build_cache_download {cache_file} https://example.invalid/artifact artifact
                    """
                ),
                env={
                    "BUILD_CACHE_DOWNLOAD_ATTEMPTS": "1",
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                },
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(counter.read_text(encoding="utf-8").strip(), "2")
            self.assertEqual(cache_file.read_text(encoding="utf-8").strip(), "complete")
            self.assertFalse((cache_file.parent / "artifact.bin.part").exists())

    def test_download_retries_resume_partial_within_single_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cache_file = tmp_path / "cache" / "artifact.bin"
            counter = tmp_path / "curl-count"
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            (fake_bin / "curl").write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    count=0
                    if [ -f {counter} ]; then
                      count="$(cat {counter})"
                    fi
                    count=$((count + 1))
                    echo "${{count}}" > {counter}
                    out=""
                    while [ "$#" -gt 0 ]; do
                      case "$1" in
                        -o)
                          out="$2"
                          shift 2
                          ;;
                        *)
                          shift
                          ;;
                      esac
                    done
                    mkdir -p "$(dirname "${{out}}")"
                    if [ "${{count}}" -eq 1 ]; then
                      echo partial >"${{out}}"
                      exit 56
                    fi
                    grep -q partial "${{out}}" || exit 66
                    echo complete >"${{out}}"
                    """
                ),
                encoding="utf-8",
            )
            (fake_bin / "curl").chmod(0o755)

            proc = self._run_helper(
                f"build_cache_download {cache_file} https://example.invalid/artifact artifact",
                env={
                    "BUILD_CACHE_DOWNLOAD_ATTEMPTS": "2",
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                },
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(counter.read_text(encoding="utf-8").strip(), "2")
            self.assertEqual(cache_file.read_text(encoding="utf-8").strip(), "complete")
            self.assertFalse((cache_file.parent / "artifact.bin.part").exists())

    def test_pip_download_failure_does_not_mark_complete_or_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            requirements = tmp_path / "requirements.lock"
            requirements.write_text("missing-wheel==1.0 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8")
            install_marker = tmp_path / "pip-install-called"
            helper_bin = tmp_path / "bin"
            helper_bin.mkdir()
            (helper_bin / "python").symlink_to(sys.executable)
            fake_python = tmp_path / "python"
            fake_python.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    if [ "$1" = "-" ]; then
                      cat >/dev/null
                      echo py313-test
                      exit 0
                    fi
                    if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "download" ]; then
                      exit 44
                    fi
                    if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
                      touch {install_marker}
                      exit 0
                    fi
                    exit 64
                    """
                ),
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            proc = self._run_helper(
                f"build_cache_pip_install {fake_python} runtime {requirements} true",
                env={
                    "MCP_BUILD_PIP_WHEELHOUSE_ROOT": str(tmp_path / "wheelhouse"),
                    "PATH": f"{helper_bin}:{os.environ['PATH']}",
                },
            )

            self.assertEqual(proc.returncode, 44, proc.stderr)
            self.assertIn("failed to populate pip wheelhouse", proc.stderr)
            self.assertFalse(install_marker.exists(), "install must not run after pip download fails")
            self.assertFalse(list((tmp_path / "wheelhouse").glob("**/.complete")))

    def test_corrupt_cached_pip_wheelhouse_rebuilds_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            requirements = tmp_path / "requirements.lock"
            requirements.write_text("cached-wheel==1.0 --hash=sha256:" + "1" * 64 + "\n", encoding="utf-8")
            download_count = tmp_path / "download-count"
            install_count = tmp_path / "install-count"
            helper_bin = tmp_path / "bin"
            helper_bin.mkdir()
            (helper_bin / "python").symlink_to(sys.executable)
            fake_python = tmp_path / "python"
            fake_python.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    if [ "$1" = "-" ]; then
                      cat >/dev/null
                      echo py313-test
                      exit 0
                    fi
                    if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "download" ]; then
                      count=0
                      [ -f {download_count} ] && count="$(cat {download_count})"
                      count=$((count + 1))
                      echo "$count" > {download_count}
                      out=""
                      while [ "$#" -gt 0 ]; do
                        if [ "$1" = "-d" ]; then
                          out="$2"
                          shift 2
                        else
                          shift
                        fi
                      done
                      echo wheel >"$out/cached-wheel-1.0-py3-none-any.whl"
                      exit 0
                    fi
                    if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
                      count=0
                      [ -f {install_count} ] && count="$(cat {install_count})"
                      count=$((count + 1))
                      echo "$count" > {install_count}
                      [ "$count" -eq 1 ] && exit 45
                      exit 0
                    fi
                    exit 64
                    """
                ),
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            proc = self._run_helper(
                textwrap.dedent(
                    f"""\
                    wheelhouse="$MCP_BUILD_PIP_WHEELHOUSE_ROOT/runtime-locked-$(build_cache_python_tag {fake_python})-$(build_cache_requirement_digest {requirements})"
                    mkdir -p "$wheelhouse"
                    touch "$wheelhouse/.complete"
                    build_cache_pip_install {fake_python} runtime {requirements} true
                    """
                ),
                env={
                    "MCP_BUILD_PIP_WHEELHOUSE_ROOT": str(tmp_path / "wheelhouse"),
                    "PATH": f"{helper_bin}:{os.environ['PATH']}",
                },
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("cached pip wheelhouse for runtime failed install; rebuilding once", proc.stderr)
            self.assertEqual(download_count.read_text(encoding="utf-8").strip(), "1")
            self.assertEqual(install_count.read_text(encoding="utf-8").strip(), "2")

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
