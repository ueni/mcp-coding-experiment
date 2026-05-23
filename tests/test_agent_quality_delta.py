# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import copy
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase


class AgentQualityDeltaTests(ServerToolsTestBase):
    def _refs_after_change(self, rel_path: str, content: str, message: str = "change quality"):
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.write_repo_text(rel_path, content)
        self.commit_all(message)
        head = self.git("rev-parse", "HEAD").stdout.strip()
        return base, head

    def _delta(self, base: str, head: str, **kwargs):
        with patch.object(self.server.shutil, "which", return_value=None):
            return self.server.agent_quality_delta(base_ref=base, head_ref=head, **kwargs)

    def _stable_copy(self, payload):
        return copy.deepcopy(payload)

    def test_clean_python_delta_passes_with_stable_schema(self):
        base, head = self._refs_after_change(
            "src/sample.py",
            "import os\n\n"
            "def alpha(x):\n"
            "    return x + 2\n\n"
            "def beta(y):\n"
            "    return alpha(y)\n",
        )

        out = self._delta(base, head)

        self.assertEqual(out["schema"], "agent_quality_delta.v1")
        self.assertTrue(out["read_only"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["decision"], "pass")
        self.assertEqual(out["summary"]["python_file_count"], 1)
        self.assertEqual(out["summary"]["positive_static_finding_delta"], 0)
        self.assertIn("static_analysis", out)
        self.assertIn("complexity", out)
        self.assertIn("policy", out)
        self.assertIn("provenance", out)
        self.assertEqual(out["provenance"]["raw_prompt_storage"], False)

    def test_warning_delta_reports_churn_normalized_findings_and_hints(self):
        base, head = self._refs_after_change(
            "src/sample.py",
            "import os\n\n"
            "def alpha(x):\n"
            "    value = x + 1  # TODO: simplify later\n"
            "    value = value + 1\n"
            "    value = value + 1\n"
            "    value = value + 1\n"
            "    return value\n\n"
            "def beta(y):\n"
            "    return alpha(y)\n",
        )

        out = self._delta(base, head)

        self.assertTrue(out["ok"])
        self.assertEqual(out["decision"], "warn")
        self.assertGreater(out["static_analysis"]["delta"]["by_severity"].get("low", 0), 0)
        self.assertGreater(out["static_analysis"]["normalized_delta_per_kloc"]["by_severity"].get("low", 0), 0)
        self.assertGreaterEqual(out["summary"]["duplication_hint_count"], 1)
        self.assertTrue(out["policy"]["warn_reasons"])

    def test_blocking_delta_supports_maintainer_override_and_complexity_deltas(self):
        base, head = self._refs_after_change(
            "src/sample.py",
            "import os\n\n"
            "def alpha(x):\n"
            "    if x:\n"
            "        for item in range(x):\n"
            "            if item % 2:\n"
            "                while item > 0:\n"
            "                    break\n"
            "    return eval(str(x))\n\n"
            "def beta(y):\n"
            "    return alpha(y)\n",
        )

        blocked = self._delta(base, head)
        overridden = self._delta(base, head, maintainer_override=True)

        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["decision"], "block")
        self.assertGreater(blocked["summary"]["max_cognitive_delta"], 0)
        self.assertGreater(blocked["summary"]["max_cyclomatic_delta"], 0)
        self.assertTrue(blocked["policy"]["block_reasons"])
        self.assertTrue(overridden["ok"])
        self.assertEqual(overridden["decision"], "warn")
        self.assertEqual(overridden["policy"]["raw_decision"], "block")
        self.assertTrue(overridden["policy"]["maintainer_override_applied"])

    def test_report_is_stable_and_redacts_prompt_material(self):
        base, head = self._refs_after_change(
            "src/sample.py",
            "import os\n\n"
            "def alpha(x):\n"
            "    return eval(str(x))\n\n"
            "def beta(y):\n"
            "    return alpha(y)\n",
        )

        first = self._stable_copy(self._delta(base, head))
        second = self._stable_copy(self._delta(base, head))

        self.assertEqual(first, second)
        provenance = first["provenance"]
        self.assertEqual(provenance["schema"], "agent_quality_delta.provenance.v1")
        self.assertIn("patch_survivorship", provenance)
        self.assertFalse(provenance["redaction"]["raw_prompts_stored"])
        self.assertNotIn("raw_prompt", str(first["static_analysis"]["findings"]).lower())

    def test_release_readiness_surfaces_agent_quality_delta_summary(self):
        base, head = self._refs_after_change(
            "src/sample.py",
            "import os\n\n"
            "def alpha(x):\n"
            "    return eval(str(x))\n\n"
            "def beta(y):\n"
            "    return alpha(y)\n",
        )

        with patch.object(self.server.shutil, "which", return_value=None):
            readiness = self.server.release_readiness(
                base_ref=base,
                head_ref=head,
                run_tests=False,
                run_docs_check=False,
                run_security_check=False,
                run_dependency_security_check=False,
                run_ci_workflow_security_check=False,
                run_secret_exposure_check=False,
                run_license_check=False,
                run_risk_check=False,
                run_impact_check=False,
                summary_mode="quick",
            )

        check = readiness["checks"]["agent_quality_delta"]
        self.assertFalse(readiness["ok"])
        self.assertFalse(check["ok"])
        self.assertEqual(check["decision"], "block")
        self.assertGreater(check["positive_static_finding_delta"], 0)
        self.assertFalse(check["maintainer_override_applied"])
