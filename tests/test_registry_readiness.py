# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import registry_readiness  # noqa: E402

SERVER_JSON = REPO_ROOT / "server.json"
SCHEMA_JSON = REPO_ROOT / "schemas" / "mcp-registry-server-2025-12-11.schema.json"
DOCKERFILE = REPO_ROOT / "source" / "Dockerfile"
VERSION_SOURCE = REPO_ROOT / "source" / "version_metadata.py"


def _base_manifest() -> dict:
    return json.loads(SERVER_JSON.read_text(encoding="utf-8"))


def _finding_codes(report: dict) -> set[str]:
    return {finding["code"] for finding in report["findings"]}


class RegistryReadinessTests(unittest.TestCase):
    def _validate_fixture(
        self,
        manifest: dict,
        *,
        dockerfile_text: str | None = None,
        version_source_text: str | None = None,
        expected_version: str | None = None,
    ) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest_path = tmp_path / "server.json"
            dockerfile_path = tmp_path / "Dockerfile"
            version_source_path = tmp_path / "version_metadata.py"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            dockerfile_path.write_text(
                dockerfile_text
                if dockerfile_text is not None
                else DOCKERFILE.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            version_source_path.write_text(
                version_source_text
                if version_source_text is not None
                else VERSION_SOURCE.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            return registry_readiness.validate_registry_readiness(
                manifest_path=manifest_path,
                schema_path=SCHEMA_JSON,
                dockerfile_path=dockerfile_path,
                version_source_path=version_source_path,
                expected_version=expected_version,
            )

    def test_checked_in_manifest_is_registry_ready(self):
        report = registry_readiness.validate_registry_readiness(
            manifest_path=SERVER_JSON,
            schema_path=SCHEMA_JSON,
            dockerfile_path=DOCKERFILE,
            version_source_path=VERSION_SOURCE,
        )

        self.assertTrue(report["ok"], report["findings"])
        self.assertEqual(report["schema_url"], registry_readiness.SERVER_SCHEMA_URL)

    def test_schema_error_is_reported(self):
        manifest = _base_manifest()
        manifest.pop("name")

        report = self._validate_fixture(manifest)

        self.assertFalse(report["ok"])
        self.assertIn("schema_error", _finding_codes(report))

    def test_secret_literal_and_secret_input_metadata_are_rejected(self):
        manifest = _base_manifest()
        env = copy.deepcopy(manifest["packages"][0]["environmentVariables"][0])
        env["name"] = "MCP_HTTP_BEARER_TOKEN"
        env["value"] = "Bearer abcdefghijklmnopqrstuvwxyz123456"
        env["isSecret"] = True
        manifest["packages"][0]["environmentVariables"].append(env)

        report = self._validate_fixture(manifest)
        codes = _finding_codes(report)

        self.assertFalse(report["ok"])
        self.assertIn("secret_looking_value", codes)
        self.assertIn("secret_input_metadata", codes)

    def test_unsupported_registry_type_and_base_url_are_rejected(self):
        manifest = _base_manifest()
        manifest["packages"][0]["registryType"] = "custom"
        custom_type_report = self._validate_fixture(manifest)

        manifest = _base_manifest()
        manifest["packages"][0]["registryBaseUrl"] = "https://registry.example.invalid"
        manifest["packages"][0]["identifier"] = (
            "registry.example.invalid/ueni/codebase-tooling-mcp:0.0.0-local-build"
        )
        bad_base_report = self._validate_fixture(manifest)

        self.assertFalse(custom_type_report["ok"])
        self.assertIn("unsupported_registry_type", _finding_codes(custom_type_report))
        self.assertFalse(bad_base_report["ok"])
        self.assertIn("unsupported_registry_base_url", _finding_codes(bad_base_report))
        self.assertIn("unsupported_oci_registry", _finding_codes(bad_base_report))

    def test_missing_oci_ownership_marker_is_rejected(self):
        manifest = _base_manifest()

        report = self._validate_fixture(manifest, dockerfile_text="FROM scratch\n")

        self.assertFalse(report["ok"])
        self.assertIn("missing_oci_ownership_label", _finding_codes(report))

    def test_mismatched_registry_name_is_rejected(self):
        manifest = _base_manifest()
        manifest["name"] = "io.github.ueni/other-server"

        report = self._validate_fixture(manifest)
        codes = _finding_codes(report)

        self.assertFalse(report["ok"])
        self.assertIn("registry_name_mismatch", codes)
        self.assertIn("oci_ownership_label_mismatch", codes)

    def test_host_absolute_path_and_disallowed_meta_key_are_rejected(self):
        manifest = _base_manifest()
        manifest["packages"][0]["runtimeArguments"][0]["value"] = (
            "type=bind,src=/home/ueni/private-repo,dst=/repo"
        )
        manifest["_meta"]["io.github.ueni/extra"] = {"unexpected": True}

        report = self._validate_fixture(manifest)
        codes = _finding_codes(report)

        self.assertFalse(report["ok"])
        self.assertIn("host_absolute_path", codes)
        self.assertIn("disallowed_meta_key", codes)

    def test_version_drift_is_rejected(self):
        manifest = _base_manifest()
        manifest["version"] = "1.2.3"

        report = self._validate_fixture(manifest)
        codes = _finding_codes(report)

        self.assertFalse(report["ok"])
        self.assertIn("version_drift", codes)
        self.assertIn("package_version_drift", codes)
        self.assertIn("oci_tag_version_drift", codes)

    def test_expected_version_override_supports_release_metadata(self):
        manifest = _base_manifest()
        manifest["version"] = "1.2.3"
        manifest["packages"][0]["version"] = "1.2.3"
        manifest["packages"][0]["identifier"] = "ghcr.io/ueni/codebase-tooling-mcp:1.2.3"

        report = self._validate_fixture(manifest, expected_version="1.2.3")

        self.assertTrue(report["ok"], report["findings"])


if __name__ == "__main__":
    unittest.main()
