# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.server_test_support import ServerToolsTestBase

REPO_ROOT = Path(__file__).resolve().parents[1]
NATIVE_OLLAMA_BASE = "http://127.0.0.1:2345"


class ContinueOllamaContractConfigTest(unittest.TestCase):
    def test_continue_model_configs_use_native_ollama_base(self):
        model_paths = sorted((REPO_ROOT / ".continue" / "models").glob("*.yaml"))
        model_paths += sorted(
            (REPO_ROOT / "source" / "defaults" / "continue" / "models").glob("*.yaml")
        )
        self.assertGreater(len(model_paths), 0)
        for path in model_paths:
            text = path.read_text(encoding="utf-8")
            self.assertIn("provider: ollama", text, str(path))
            self.assertIn(f"apiBase: {NATIVE_OLLAMA_BASE}", text, str(path))
            self.assertNotIn(f"apiBase: {NATIVE_OLLAMA_BASE}/v1", text, str(path))

    def test_devcontainer_does_not_disable_ollama_bootstrap(self):
        config = json.loads(
            (REPO_ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("CONTINUE_OLLAMA_MODELS", config["containerEnv"])

    def test_codex_config_uses_hyphenated_server_key(self):
        config_toml = (REPO_ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
        default_config_toml = (
            REPO_ROOT / "source" / "defaults" / "codex" / "config.toml"
        ).read_text(encoding="utf-8")
        self.assertIn('[mcp_servers."codebase-tooling-mcp"]', config_toml)
        self.assertNotIn("[mcp_servers.codebase_tooling_mcp]", config_toml)
        self.assertIn('[mcp_servers."codebase-tooling-mcp"]', default_config_toml)
        self.assertNotIn("[mcp_servers.codebase_tooling_mcp]", default_config_toml)

    def test_dockerfile_keeps_default_coding_model_and_preloads_it(self):
        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("CODING_DEFAULT_MODEL=qwen2.5-coder:7b", dockerfile)
        self.assertIn("CONTINUE_OLLAMA_MODELS=qwen2.5-coder:7b", dockerfile)
        self.assertIn('ARG OLLAMA_PRELOAD_MODELS="qwen2.5-coder:7b"', dockerfile)
        self.assertIn('ollama pull "$model"', dockerfile)


class ServerOllamaContractStatusTest(ServerToolsTestBase):
    def test_local_model_status_reports_bootstrap_opt_out(self):
        with patch.dict(os.environ, {"CONTINUE_OLLAMA_MODELS": ""}, clear=False), patch.object(
            self.server, "LOCAL_INFER_BACKEND", "endpoint"
        ), patch.object(
            self.server, "LOCAL_INFER_ENDPOINT", f"{NATIVE_OLLAMA_BASE}/api/generate"
        ), patch.object(
            self.server, "CODING_DEFAULT_MODEL", "qwen2.5-coder:7b"
        ), patch.object(
            self.server,
            "_fetch_ollama_tags",
            return_value={
                "url": f"{NATIVE_OLLAMA_BASE}/api/tags",
                "reachable": True,
                "status": 200,
                "model_ids": [],
            },
        ), patch.object(
            self.server,
            "_probe_http",
            return_value={
                "url": f"{NATIVE_OLLAMA_BASE}/v1/",
                "reachable": False,
                "error": "HTTP Error 404: Not Found",
            },
        ):
            out = self.server.local_model_status()

        self.assertTrue(out["infer"]["endpoint_reachable"])
        self.assertFalse(out["infer"]["openai_compat_base_reachable"])
        self.assertFalse(out["ollama"]["bootstrap_enabled"])
        self.assertEqual(out["ollama"]["installed_models_count"], 0)
        self.assertFalse(out["coding"]["default_model_installed"])
        self.assertFalse(out["coding"]["default_model_in_bootstrap_list"])
        self.assertTrue(
            any("startup pre-pull is intentionally disabled" in msg for msg in out["diagnostics"])
        )
        self.assertTrue(any("without /v1" in msg for msg in out["diagnostics"]))

    def test_local_model_status_reports_native_contract_and_installed_default(self):
        with patch.dict(
            os.environ,
            {"CONTINUE_OLLAMA_MODELS": "qwen2.5-coder:7b,granite3.2:2b"},
            clear=False,
        ), patch.object(self.server, "LOCAL_INFER_BACKEND", "endpoint"), patch.object(
            self.server, "LOCAL_INFER_ENDPOINT", f"{NATIVE_OLLAMA_BASE}/api/generate"
        ), patch.object(
            self.server, "CODING_DEFAULT_MODEL", "qwen2.5-coder:7b"
        ), patch.object(
            self.server,
            "_fetch_ollama_tags",
            return_value={
                "url": f"{NATIVE_OLLAMA_BASE}/api/tags",
                "reachable": True,
                "status": 200,
                "model_ids": ["qwen2.5-coder:7b"],
            },
        ), patch.object(
            self.server,
            "_probe_http",
            return_value={
                "url": f"{NATIVE_OLLAMA_BASE}/v1/",
                "reachable": False,
                "error": "HTTP Error 404: Not Found",
            },
        ):
            out = self.server.local_model_status()

        self.assertEqual(out["infer"]["native_api_base"], NATIVE_OLLAMA_BASE)
        self.assertEqual(out["infer"]["openai_compat_base"], f"{NATIVE_OLLAMA_BASE}/v1/")
        self.assertTrue(out["ollama"]["bootstrap_enabled"])
        self.assertEqual(out["ollama"]["installed_models"], ["qwen2.5-coder:7b"])
        self.assertTrue(out["coding"]["default_model_installed"])
        self.assertTrue(out["coding"]["default_model_in_bootstrap_list"])
        self.assertTrue(any("without /v1" in msg for msg in out["diagnostics"]))
