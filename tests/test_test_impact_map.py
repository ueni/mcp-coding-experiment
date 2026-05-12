# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

from tests.server_test_support import ServerToolsTestBase


class TestImpactMapTests(ServerToolsTestBase):
    def test_query_static_map_for_direct_source_reference(self):
        out = self.server.test_impact_map(
            changed_files=["src/sample.py"], refresh=True, output_profile="normal"
        )

        self.assertEqual(out["artifact_status"], "fresh")
        self.assertIn("tests/test_sample.py", out["selected_tests"])
        self.assertEqual(out["unmapped_changed_files"], [])
        self.assertGreater(out["confidence"], 0)
        source = out["impacted_sources"][0]
        self.assertIn("source_reference_in_test", source["mapping_reasons"])

    def test_static_map_includes_reverse_import_dependents(self):
        self.write_repo_text(
            "src/wrapper.py",
            "from src.sample import alpha\n\n"
            "def wrapped(value):\n"
            "    return alpha(value)\n",
        )
        self.write_repo_text(
            "tests/test_wrapper.py",
            "from src.wrapper import wrapped\n\n"
            "def test_wrapped():\n"
            "    assert wrapped(1) == 2\n",
        )
        self.commit_all("add wrapper")

        out = self.server.test_impact_map(
            changed_files=["src/sample.py"], refresh=True, output_profile="normal"
        )

        self.assertIn("tests/test_sample.py", out["selected_tests"])
        self.assertIn("tests/test_wrapper.py", out["selected_tests"])
        details = {row["path"]: row for row in out["test_details"]}
        self.assertIn("reverse_import_dependent", details["tests/test_wrapper.py"]["reasons"])

    def test_impact_tests_falls_back_when_artifact_absent_or_stale(self):
        self.write_repo_text(
            "src/sample.py",
            "import os\n\n"
            "def alpha(x):\n"
            "    return x + 2\n\n"
            "def beta(y):\n"
            "    return alpha(y)\n",
        )
        absent = self.server.impact_tests(base_ref="HEAD", head_ref="HEAD", output_profile="normal")
        self.assertEqual(absent["impact_map"]["artifact_status"], "absent")

        self.server.test_impact_map(refresh=True)
        self.write_repo_text(
            "src/sample.py",
            "import os\n\n"
            "def alpha(x):\n"
            "    return x + 3\n\n"
            "def beta(y):\n"
            "    return alpha(y)\n",
        )
        self.commit_all("change sample")
        stale = self.server.impact_tests(base_ref="HEAD~1", head_ref="HEAD", output_profile="normal")

        self.assertEqual(stale["impact_map"]["artifact_status"], "stale")
        self.assertTrue(stale["impact_map"]["fallback_used"])
        self.assertIn("tests/test_sample.py", stale["tests"])

    def test_changed_test_file_remains_selected_with_artifact(self):
        self.server.test_impact_map(refresh=True)
        self.write_repo_text(
            "tests/test_sample.py",
            "from src.sample import alpha\n\n"
            "def test_alpha():\n"
            "    assert alpha(2) == 3\n",
        )
        self.commit_all("change test")

        out = self.server.impact_tests(
            base_ref="HEAD~1", head_ref="HEAD", output_profile="normal"
        )

        self.assertIn("tests/test_sample.py", out["tests"])

    def test_unmapped_changed_files_are_reported(self):
        self.write_repo_text("src/orphan.py", "def lonely():\n    return 1\n")
        self.commit_all("add orphan")

        out = self.server.test_impact_map(
            changed_files=["src/orphan.py"], refresh=True, output_profile="normal"
        )

        self.assertEqual(out["selected_tests"], [])
        self.assertIn("src/orphan.py", out["unmapped_changed_files"])
        self.assertEqual(out["coverage_gaps"][0]["reason"], "no_static_test_mapping")

    def test_change_impact_gate_exposes_unmapped_files(self):
        self.write_repo_text("src/orphan.py", "def lonely():\n    return 1\n")
        self.commit_all("add orphan")
        self.server.test_impact_map(refresh=True)
        self.write_repo_text("src/orphan.py", "def lonely():\n    return 2\n")
        self.commit_all("change orphan")

        gate = self.server.change_impact_gate(
            base_ref="HEAD~1",
            head_ref="HEAD",
            require_docs_for_impl_diff=False,
            block_on_risk_level="none",
        )

        self.assertIn("selected_tests", gate)
        self.assertIn("src/orphan.py", gate["unmapped_changed_files"])
        self.assertIn("impact_tests", gate)
