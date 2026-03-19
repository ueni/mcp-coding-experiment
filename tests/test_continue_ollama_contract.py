# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import json
import os
import subprocess
import tempfile
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

    def test_devcontainer_mounts_host_docker_config_for_container_use(self):
        config = json.loads(
            (REPO_ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")
        )
        self.assertEqual("/home/app/.docker", config["containerEnv"]["DOCKER_CONFIG"])
        self.assertIn(
            "source=${localEnv:HOME}/.docker,target=/host/.docker,type=bind,consistency=cached,readOnly=true",
            config["mounts"],
        )

    def test_devcontainer_exposes_dri_device_for_vulkan_ollama(self):
        config = json.loads(
            (REPO_ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")
        )
        self.assertIn("--device=/dev/dri", config.get("runArgs", []))
        self.assertEqual("1", config["containerEnv"]["OLLAMA_VULKAN"])

    def test_setup_script_generates_devcontainer_with_ollama_ports_and_codex_mount(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / ".git").mkdir()
            result = subprocess.run(
                ["/bin/sh", str(REPO_ROOT / "setup-repository.sh")],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=result.stderr.strip() or result.stdout.strip(),
            )

            config = json.loads(
                (repo_root / ".devcontainer" / "devcontainer.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual([8000, 2345], config["forwardPorts"])
        self.assertEqual("0.0.0.0:2345", config["containerEnv"]["OLLAMA_HOST"])
        self.assertEqual(
            "0.0.0.0:2345", config["containerEnv"]["OLLAMA_FALLBACK_HOST"]
        )
        self.assertEqual(
            "http://127.0.0.1:2345/api/generate",
            config["containerEnv"]["LOCAL_INFER_ENDPOINT"],
        )
        self.assertEqual("Bundled LLM", config["portsAttributes"]["2345"]["label"])
        self.assertIn(
            "source=${localEnv:HOME}/.codex,target=/home/app/.codex,type=bind,consistency=cached,readOnly=false",
            config["mounts"],
        )
        self.assertNotIn(
            "source=/etc/ssl/certs,target=/etc/ssl/certs,type=bind,consistency=cached,readOnly=true",
            config["mounts"],
        )

    def test_setup_script_can_force_vulkan_gpu_passthrough(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / ".git").mkdir()
            result = subprocess.run(
                [
                    "/bin/sh",
                    str(REPO_ROOT / "setup-repository.sh"),
                    "--enable-vulkan-gpu",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=result.stderr.strip() or result.stdout.strip(),
            )

            config = json.loads(
                (repo_root / ".devcontainer" / "devcontainer.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertIn("--device=/dev/dri", config.get("runArgs", []))
        self.assertEqual("1", config["containerEnv"]["OLLAMA_VULKAN"])

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
        self.assertIn("CODING_DEFAULT_MODEL=qwen2.5-coder:3b", dockerfile)
        self.assertIn("CONTINUE_OLLAMA_MODELS=qwen2.5-coder:3b,granite3.3:2b", dockerfile)
        self.assertIn('ARG OLLAMA_PRELOAD_MODELS="qwen2.5-coder:3b"', dockerfile)
        self.assertIn('ollama pull "$model"', dockerfile)

    def test_continue_model_routing_uses_small_default_profile(self):
        for routing_path in [
            REPO_ROOT / ".continue" / "model-routing.yaml",
            REPO_ROOT / "source" / "defaults" / "continue" / "model-routing.yaml",
        ]:
            routing = routing_path.read_text(encoding="utf-8")
            self.assertIn("model: granite3.3:2b", routing, str(routing_path))
            self.assertIn("file: .continue/models/router-granite3.3-2b.yaml", routing, str(routing_path))
            self.assertIn("model: qwen2.5-coder:3b", routing, str(routing_path))
            self.assertIn("file: .continue/models/coding-qwen2.5-coder-3b.yaml", routing, str(routing_path))
            self.assertIn("model: llama3.2:1b", routing, str(routing_path))
            self.assertIn("file: .continue/models/research-llama3.2-1b.yaml", routing, str(routing_path))

        self.assertFalse((REPO_ROOT / ".continue" / "models" / "router-granite3.2-2b.yaml").exists())
        self.assertFalse((REPO_ROOT / ".continue" / "models" / "coding-qwen2.5-coder-7b.yaml").exists())
        self.assertFalse((REPO_ROOT / ".continue" / "models" / "research-llama3.2-3b.yaml").exists())

    def test_dockerfile_installs_vulkan_runtime_for_ollama(self):
        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("libvulkan1", dockerfile)
        self.assertIn("mesa-vulkan-drivers", dockerfile)
        self.assertIn("vulkan-tools", dockerfile)

    def test_dockerfile_writes_app_sudoers_rule_with_single_shell_command(self):
        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "echo 'app ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/app",
            dockerfile,
        )

    def test_entrypoint_maps_dri_groups_before_dropping_to_app(self):
        entrypoint = (REPO_ROOT / "source" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("maybe_fix_dri_device_groups()", entrypoint)
        self.assertIn("/dev/dri/renderD*", entrypoint)
        self.assertIn("/dev/dri/card*", entrypoint)
        before_drop = entrypoint.split(
            'exec su -m -s /bin/bash app -c "/app/entrypoint.sh --as-app"', 1
        )[0]
        self.assertIn("maybe_fix_dri_device_groups", before_drop)


class ServerOllamaContractStatusTest(ServerToolsTestBase):
    def test_local_model_status_reports_bootstrap_opt_out(self):
        with patch.dict(os.environ, {"CONTINUE_OLLAMA_MODELS": ""}, clear=False), patch.object(
            self.server, "LOCAL_INFER_BACKEND", "endpoint"
        ), patch.object(
            self.server, "LOCAL_INFER_ENDPOINT", f"{NATIVE_OLLAMA_BASE}/api/generate"
        ), patch.object(
            self.server, "CODING_DEFAULT_MODEL", "qwen2.5-coder:3b"
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
            {"CONTINUE_OLLAMA_MODELS": "qwen2.5-coder:3b,granite3.3:2b"},
            clear=False,
        ), patch.object(self.server, "LOCAL_INFER_BACKEND", "endpoint"), patch.object(
            self.server, "LOCAL_INFER_ENDPOINT", f"{NATIVE_OLLAMA_BASE}/api/generate"
        ), patch.object(
            self.server, "CODING_DEFAULT_MODEL", "qwen2.5-coder:3b"
        ), patch.object(
            self.server,
            "_fetch_ollama_tags",
            return_value={
                "url": f"{NATIVE_OLLAMA_BASE}/api/tags",
                "reachable": True,
                "status": 200,
                "model_ids": ["qwen2.5-coder:3b"],
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
        self.assertEqual(out["ollama"]["installed_models"], ["qwen2.5-coder:3b"])
        self.assertTrue(out["coding"]["default_model_installed"])
        self.assertTrue(out["coding"]["default_model_in_bootstrap_list"])
        self.assertTrue(any("without /v1" in msg for msg in out["diagnostics"]))
