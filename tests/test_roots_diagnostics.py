# SPDX-License-Identifier: MIT
# Copyright (c) Nico Ueberfeldt

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class _Context:
    def __init__(self, session):
        self.session = session


class _Session:
    def __init__(self, roots=None, *, supported=True, error=None, delay=0):
        self._roots = roots or []
        self._supported = supported
        self._error = error
        self._delay = delay

    def check_client_capability(self, _capability):
        return self._supported

    async def list_roots(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return SimpleNamespace(roots=self._roots)


class RootsDiagnosticsTest(ServerToolsTestBase):
    def _run_with_session(self, session, **kwargs):
        with patch.object(self.server.mcp, "get_context", return_value=_Context(session)):
            return asyncio.run(self.server.roots_diagnostics(**kwargs))

    def test_roots_diagnostics_unavailable_without_active_session(self):
        with patch.object(self.server.mcp, "get_context", side_effect=RuntimeError("no ctx")):
            out = asyncio.run(self.server.roots_diagnostics())

        self.assertEqual(out["schema"], "roots_diagnostics.v1")
        self.assertEqual(out["fetch"]["status"], "unavailable")
        self.assertEqual(out["relationship"]["classification"], "unavailable")
        self.assertTrue(out["read_only"])
        self.assertTrue(out["advisory_only"])
        self.assertTrue(out["repo_boundary_enforced"])

    def test_roots_diagnostics_unsupported_without_capability(self):
        out = self._run_with_session(_Session(supported=False))

        self.assertEqual(out["fetch"]["status"], "unsupported")
        self.assertEqual(out["relationship"]["classification"], "unsupported")

    def test_roots_diagnostics_exact_match(self):
        root = SimpleNamespace(uri=self.repo_path.as_uri(), name="repo")
        out = self._run_with_session(_Session([root]))

        self.assertEqual(out["fetch"]["status"], "fetched")
        self.assertEqual(out["relationship"]["classification"], "exact_match")
        self.assertEqual(out["roots"]["file_count"], 1)
        self.assertEqual(out["roots"]["items"][0]["normalized_path"], ".")

    def test_roots_diagnostics_repo_child_and_root_parent_overlap(self):
        child = SimpleNamespace(uri=(self.repo_path / "src").as_uri(), name="src")
        child_out = self._run_with_session(_Session([child]))
        self.assertEqual(child_out["relationship"]["classification"], "repo_contains_root")
        self.assertEqual(child_out["roots"]["items"][0]["normalized_path"], "src")

        parent = SimpleNamespace(uri=self.repo_path.parent.as_uri(), name="workspace")
        parent_out = self._run_with_session(_Session([parent]))
        self.assertEqual(parent_out["relationship"]["classification"], "root_contains_repo")
        self.assertNotIn(str(self.repo_path.parent), str(parent_out["roots"]))
        self.assertIn("outside_repo_client_path", parent_out["roots"]["redactions_applied"])

    def test_roots_diagnostics_multiple_roots_and_no_overlap(self):
        exact = SimpleNamespace(uri=self.repo_path.as_uri(), name="repo")
        child = SimpleNamespace(uri=(self.repo_path / "docs").as_uri(), name="docs")
        multiple = self._run_with_session(_Session([exact, child]))
        self.assertEqual(multiple["relationship"]["classification"], "multiple_roots")
        self.assertEqual(multiple["roots"]["file_count"], 2)

        outside_path = self.repo_path.parent / "different-workspace"
        outside = SimpleNamespace(uri=outside_path.as_uri(), name="outside")
        no_overlap = self._run_with_session(_Session([outside]))
        self.assertEqual(no_overlap["relationship"]["classification"], "no_overlap")
        self.assertNotIn(str(outside_path), str(no_overlap["roots"]))
        self.assertIn("outside_repo_client_path", no_overlap["roots"]["redactions_applied"])

    def test_roots_diagnostics_non_file_malformed_failure_and_timeout(self):
        non_file = SimpleNamespace(uri="https://example.invalid/workspace", name="remote")
        malformed = SimpleNamespace(uri="file://remote-host/tmp/repo", name="bad")
        out = self._run_with_session(_Session([non_file, malformed]))
        self.assertEqual(out["roots"]["scheme_counts"]["https"], 1)
        self.assertEqual(out["roots"]["invalid_count"], 1)
        self.assertEqual(out["relationship"]["classification"], "error")
        self.assertIn("non_file_uri_omitted", out["roots"]["redactions_applied"])

        failed = self._run_with_session(_Session(error=RuntimeError("boom")))
        self.assertEqual(failed["fetch"]["status"], "error")
        self.assertEqual(failed["relationship"]["classification"], "error")
        self.assertNotIn("boom", str(failed))

        timed_out = self._run_with_session(_Session(delay=0.05), timeout_seconds=0.01)
        self.assertEqual(timed_out["fetch"]["status"], "timeout")
        self.assertEqual(timed_out["relationship"]["classification"], "error")
