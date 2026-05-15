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

import yaml

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


    def test_qwen36_continue_agent_context_window_matches_ollama_alias(self):
        expected_context = 32768
        for config_path in [
            REPO_ROOT / ".continue" / "models" / "coding-qwen3.6-35b-a3b.yaml",
            REPO_ROOT / "source" / "defaults" / "continue" / "models" / "coding-qwen3.6-35b-a3b.yaml",
        ]:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            model = config["models"][0]
            options = model["defaultCompletionOptions"]

            self.assertEqual("Coding - Qwen3.6 35B A3B", model["name"], str(config_path))
            self.assertIn("tool_use", model.get("capabilities", []), str(config_path))
            self.assertEqual(expected_context, options["contextLength"], str(config_path))
            self.assertLess(options["maxTokens"], options["contextLength"], str(config_path))
            self.assertLessEqual(options["maxTokens"], 2048, str(config_path))
            self.assertIn("<|im_end|>", options["stop"], str(config_path))
            self.assertNotIn("completionOptions", model, str(config_path))

        devcontainer = json.loads(
            (REPO_ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")
        )
        self.assertEqual(str(expected_context), devcontainer["containerEnv"]["OLLAMA_CONTEXT_LENGTH"])
        self.assertEqual(str(expected_context), devcontainer["containerEnv"]["OLLAMA_TEXT_ALIAS_NUM_CTX"])

        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (REPO_ROOT / "source" / "entrypoint.sh").read_text(encoding="utf-8")
        setup_repository = (REPO_ROOT / "setup-repository.sh").read_text(encoding="utf-8")
        self.assertIn(f"OLLAMA_CONTEXT_LENGTH={expected_context}", dockerfile)
        self.assertIn(f'DEFAULT_OLLAMA_TEXT_ALIAS_NUM_CTX="{expected_context}"', entrypoint)
        self.assertIn(f'"OLLAMA_CONTEXT_LENGTH": "{expected_context}"', setup_repository)
        self.assertIn(f'"OLLAMA_TEXT_ALIAS_NUM_CTX": "{expected_context}"', setup_repository)

    def test_devcontainer_does_not_override_default_ollama_model_policy(self):
        config = json.loads(
            (REPO_ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("CONTINUE_OLLAMA_MODELS", config["containerEnv"])
        self.assertNotIn("OLLAMA_ALLOW_PULL", config["containerEnv"])

    def test_devcontainer_mounts_host_docker_config_for_container_use(self):
        config = json.loads(
            (REPO_ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")
        )
        self.assertEqual("/home/app/.docker", config["containerEnv"]["DOCKER_CONFIG"])
        self.assertIn(
            "source=${localEnv:HOME}/.docker,target=/host/.docker,type=bind,consistency=cached,readOnly=true",
            config["mounts"],
        )

    def test_continue_mcp_config_sends_bearer_header_via_secret_reference(self):
        for config_path in [
            REPO_ROOT / ".continue" / "mcpServers" / "codebase-tooling-mcp.yaml",
            REPO_ROOT / "source" / "defaults" / "continue" / "codebase-tooling-mcp.yaml",
        ]:
            text = config_path.read_text(encoding="utf-8")
            config = yaml.safe_load(text)
            auth_header = config["mcpServers"][0]["requestOptions"]["headers"]["Authorization"]

            self.assertEqual(
                auth_header,
                "Bearer ${{ secrets.MCP_HTTP_BEARER_TOKEN }}",
                str(config_path),
            )
            self.assertNotIn("secret-token", text)
            self.assertNotIn("MCP_HTTP_BEARER_TOKEN=", text)

    def test_devcontainer_exposes_dri_device_for_vulkan_ollama(self):
        config = json.loads(
            (REPO_ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")
        )
        self.assertIn("--device=/dev/dri", config.get("runArgs", []))
        self.assertEqual("1", config["containerEnv"]["OLLAMA_VULKAN"])

    def test_setup_script_generates_devcontainer_with_ollama_ports(self):
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
        self.assertIn("127.0.0.1:8000:8000", config.get("runArgs", []))
        self.assertIn("127.0.0.1:2345:2345", config.get("runArgs", []))
        self.assertIn("--security-opt=seccomp=unconfined", config.get("runArgs", []))
        self.assertIn("--security-opt=apparmor=unconfined", config.get("runArgs", []))
        self.assertEqual("0.0.0.0:2345", config["containerEnv"]["OLLAMA_HOST"])
        self.assertEqual(
            "0.0.0.0:2345", config["containerEnv"]["OLLAMA_FALLBACK_HOST"]
        )
        self.assertEqual("32768", config["containerEnv"]["OLLAMA_CONTEXT_LENGTH"])
        self.assertEqual("32768", config["containerEnv"]["OLLAMA_TEXT_ALIAS_NUM_CTX"])
        self.assertEqual(
            "http://127.0.0.1:2345/api/generate",
            config["containerEnv"]["LOCAL_INFER_ENDPOINT"],
        )
        self.assertEqual("Bundled LLM", config["portsAttributes"]["2345"]["label"])

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
        self.assertNotIn('sandbox_mode = "danger-full-access"', config_toml)
        self.assertNotIn('sandbox_mode = "danger-full-access"', default_config_toml)

    def test_devcontainer_relaxes_security_profile_for_nested_client_sandboxes(self):
        config = json.loads(
            (REPO_ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")
        )
        self.assertIn("--security-opt=seccomp=unconfined", config.get("runArgs", []))
        self.assertIn("--security-opt=apparmor=unconfined", config.get("runArgs", []))

        entrypoint = (REPO_ROOT / "source" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertNotIn("configure_codex_inner_sandbox", entrypoint)
        self.assertNotIn("CODEX_DISABLE_INNER_SANDBOX", entrypoint)
        self.assertNotIn('sandbox_mode = "danger-full-access"', entrypoint)

    def test_dockerfile_keeps_qwen36_default_coding_model_and_micro_fast_path(self):
        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("CODING_DEFAULT_MODEL=qwen3.6-35b-a3b:iq1", dockerfile)
        self.assertIn("CODING_MICRO_MODEL=qwen2.5-coder:1.5b", dockerfile)
        self.assertIn("OLLAMA_CONTEXT_LENGTH=32768", dockerfile)
        full_default_models = "qwen3.6-35b-a3b:iq1,qwen2.5-coder:1.5b"
        self.assertIn(f"CONTINUE_OLLAMA_MODELS={full_default_models}", dockerfile)
        self.assertIn('ARG OLLAMA_PRELOAD_MODELS=""', dockerfile)
        self.assertIn("OLLAMA_ALLOW_PULL=false", dockerfile)
        self.assertIn('ollama pull "$model"', dockerfile)
        self.assertIn('/opt/codebase-tooling/preloaded-ollama-models', dockerfile)
        self.assertIn('cp -a /tmp/ollama-models/. /opt/codebase-tooling/preloaded-ollama-models/', dockerfile)

    def test_dockerfile_uses_python_313_trixie_base_image(self):
        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.13-slim-trixie", dockerfile)

    def test_dockerfile_installs_app_requirements_into_coding_venv(self):
        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('python -m venv /opt/codebase-tooling/coding-venv', dockerfile)
        self.assertIn('/opt/codebase-tooling/coding-venv/bin/pip install \\', dockerfile)
        self.assertIn('--root-user-action=ignore \\', dockerfile)
        self.assertIn('-r requirements.txt \\', dockerfile)

    def test_sentence_transformers_dependency_is_optional_for_default_image(self):
        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        requirements = (REPO_ROOT / "source" / "requirements.txt").read_text(encoding="utf-8")
        embedding_requirements = (
            REPO_ROOT / "source" / "requirements-embedding.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("ARG INSTALL_SENTENCE_TRANSFORMERS=false", dockerfile)
        self.assertIn("requirements-embedding.txt", dockerfile)
        self.assertIn('if [ "${INSTALL_SENTENCE_TRANSFORMERS}" = "true" ]', dockerfile)
        self.assertNotIn("sentence-transformers", requirements)
        self.assertIn("sentence-transformers", embedding_requirements)

    def test_preloaded_artifacts_are_not_duplicated_in_image_layers(self):
        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            'cp -a /tmp/ollama-models/. /opt/codebase-tooling/preloaded-ollama-models/',
            dockerfile,
        )
        self.assertNotIn('cp -a /tmp/ollama-models/. /home/app/.ollama/models/', dockerfile)
        self.assertIn('ln -sfn /opt/codebase-tooling/defaults/extensions "${server_root}/extensions"', dockerfile)

    def test_continue_model_routing_uses_qwen36_quality_profile(self):
        obsolete_models = (
            "qwen2.5-coder:3b",
            "granite3.3:2b",
            "phi4-mini:3.8b",
            "phi4-mini-reasoning:3.8b",
            "deepseek-r1:1.5b",
            "deepscaler:1.5b",
            "granite3.2-vision:2b",
            "llama3.2:1b",
        )
        for routing_path in [
            REPO_ROOT / ".continue" / "model-routing.yaml",
            REPO_ROOT / "source" / "defaults" / "continue" / "model-routing.yaml",
        ]:
            routing = routing_path.read_text(encoding="utf-8")
            self.assertIn("model: qwen3.6-35b-a3b:iq1", routing, str(routing_path))
            self.assertIn("file: .continue/models/coding-qwen3.6-35b-a3b.yaml", routing, str(routing_path))
            self.assertIn("model: qwen2.5-coder:1.5b", routing, str(routing_path))
            self.assertIn("file: .continue/models/coding-qwen2.5-coder-1.5b.yaml", routing, str(routing_path))
            for model in obsolete_models:
                self.assertNotIn(model, routing, str(routing_path))

        for models_root in [
            REPO_ROOT / ".continue" / "models",
            REPO_ROOT / "source" / "defaults" / "continue" / "models",
        ]:
            self.assertTrue((models_root / "coding-qwen3.6-35b-a3b.yaml").exists())
            self.assertTrue((models_root / "coding-qwen2.5-coder-1.5b.yaml").exists())
            self.assertFalse((models_root / "coding-qwen2.5-coder-3b.yaml").exists())
            self.assertFalse((models_root / "router-granite3.3-2b.yaml").exists())
            self.assertFalse((models_root / "research-llama3.2-1b.yaml").exists())

    def test_dockerfile_installs_vulkan_runtime_for_ollama(self):
        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG OLLAMA_VERSION=0.18.2", dockerfile)
        self.assertIn("libvulkan1", dockerfile)
        self.assertIn("mesa-vulkan-drivers", dockerfile)
        self.assertIn("vulkan-tools", dockerfile)
        self.assertIn("zstd", dockerfile)
        self.assertIn('ver_param="${OLLAMA_VERSION:+?version=${OLLAMA_VERSION}}"', dockerfile)
        self.assertIn('https://ollama.com/download/ollama-linux-${ollama_arch}.tar.zst${ver_param}', dockerfile)
        self.assertIn('https://ollama.com/download/ollama-linux-${ollama_arch}.tgz${ver_param}', dockerfile)
        self.assertIn('zstd -dc "${ollama_archive}" | tar -xf - -C /usr/local', dockerfile)
        self.assertIn('tgz) tar -xzf "${ollama_archive}" -C /usr/local', dockerfile)

    def test_dockerfile_caches_vscode_vsix_downloads(self):
        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "--mount=type=cache,target=/var/cache/buildkit/vscode-vsix,sharing=locked",
            dockerfile,
        )
        self.assertIn(
            'vsix_path="/var/cache/buildkit/vscode-vsix/${publisher}.${extension_name}.vsix"',
            dockerfile,
        )
        self.assertIn('if [ ! -f "${vsix_path}" ] && ! curl -fsSL', dockerfile)

    def test_dockerfile_writes_app_sudoers_rule_with_single_shell_command(self):
        dockerfile = (REPO_ROOT / "source" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "echo 'app ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/app",
            dockerfile,
        )

    def test_server_defers_heavy_optional_dependency_imports_until_tool_use(self):
        server = (REPO_ROOT / "source" / "server.py").read_text(encoding="utf-8")
        eager_imports = (
            "import sympy as sp",
            "import sqlparse",
            "from PIL import Image",
            "import pytesseract",
            "from pypdf import PdfReader",
            "import docx",
            "import openpyxl",
            "import xlrd",
        )

        for eager_import in eager_imports:
            self.assertNotIn(eager_import, server)
        self.assertIn("_OPTIONAL_DEPENDENCY_UNLOADED = object()", server)
        self.assertIn("_import_optional_dependency", server)
        self.assertIn('PdfReader = _import_optional_dependency("pypdf", "pypdf").PdfReader', server)
        self.assertIn('Image = _import_optional_dependency("PIL.Image", "Pillow")', server)

    def test_entrypoint_recreates_qwen36_alias_when_num_ctx_is_stale(self):
        entrypoint = (REPO_ROOT / "source" / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn('ollama show "${alias}" --modelfile', entrypoint)
        self.assertIn('PARAMETER[[:space:]]+num_ctx[[:space:]]+${num_ctx}', entrypoint)
        self.assertIn("stale num_ctx", entrypoint)
        self.assertIn("printf 'PARAMETER num_ctx %s\\n' \"${num_ctx}\"", entrypoint)

    def test_entrypoint_refreshes_stale_repo_continue_defaults(self):
        entrypoint = (REPO_ROOT / "source" / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("copy_continue_default_if_missing_or_stale()", entrypoint)
        self.assertIn("Continue Qwen3.6 profile has stale contextLength", entrypoint)
        self.assertIn("contextLength:[[:space:]]*32768", entrypoint)
        self.assertIn("Continue MCP server profile has stale auth header", entrypoint)
        self.assertIn("copy_continue_default_if_missing_or_stale", entrypoint)

    def test_entrypoint_seeds_preloaded_models_and_maps_gpu_device_groups(self):
        entrypoint = (REPO_ROOT / "source" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("seed_ollama_models_from_image_preload()", entrypoint)
        self.assertIn('/opt/codebase-tooling/preloaded-ollama-models', entrypoint)
        self.assertIn('cp -an "${image_models_dir}/." "${OLLAMA_MODELS}/"', entrypoint)
        self.assertIn('OLLAMA_ALLOW_PULL="${OLLAMA_ALLOW_PULL:-false}"', entrypoint)
        self.assertIn("OLLAMA_ALLOW_PULL=false; refusing runtime download", entrypoint)
        self.assertIn("maybe_fix_gpu_device_groups()", entrypoint)
        self.assertIn("/dev/dri/renderD*", entrypoint)
        self.assertIn("/dev/dri/card*", entrypoint)
        self.assertIn("/dev/kfd", entrypoint)
        self.assertIn('seed_ollama_models_from_image_preload', entrypoint)
        self.assertIn('export OLLAMA_VULKAN=1', entrypoint)
        before_drop = entrypoint.split(
            'exec su -m -s /bin/bash app -c "/app/entrypoint.sh --as-app"', 1
        )[0]
        self.assertIn("maybe_fix_gpu_device_groups", before_drop)

    def test_entrypoint_bootstraps_missing_http_bearer_token_to_local_continue_env(self):
        entrypoint = (REPO_ROOT / "source" / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("ensure_mcp_http_bearer_token()", entrypoint)
        self.assertIn("read_mcp_http_bearer_token_from_env_file", entrypoint)
        self.assertIn("MCP_HTTP_BEARER_TOKEN generated into local secret file", entrypoint)
        self.assertIn("/repo/.continue/.env", entrypoint)
        self.assertIn("openssl rand -hex 32", entrypoint)
        self.assertLess(
            entrypoint.index("ensure_mcp_http_bearer_token"),
            entrypoint.index("exec python /app/server.py"),
        )


class ServerOllamaContractStatusTest(ServerToolsTestBase):
    def test_qwen36_endpoint_requests_use_template_stops_and_sanitize_output(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "response": "<think>hidden reasoning</think>actual answer<|im_end|><|endoftext|>"
                    }
                ).encode("utf-8")

        def fake_urlopen(req, timeout):
            captured["timeout"] = timeout
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        with patch.object(self.server, "_urlopen_with_host_certs", side_effect=fake_urlopen):
            output = self.server._local_infer_via_endpoint(
                prompt="hello",
                model="qwen3.6-35b-a3b:iq1",
                max_tokens=32,
                temperature=0.1,
                system="system prompt",
            )

        self.assertEqual(output, "actual answer")
        self.assertIn("template", captured["payload"])
        self.assertIn("<|im_start|>user", captured["payload"]["template"])
        self.assertIn("<|im_end|>", captured["payload"]["options"]["stop"])
        self.assertNotIn("<think>", captured["payload"]["options"]["stop"])
        self.assertNotIn("</think>", captured["payload"]["options"]["stop"])

    def test_local_model_status_reports_bootstrap_opt_out(self):
        with patch.dict(os.environ, {"CONTINUE_OLLAMA_MODELS": ""}, clear=False), patch.object(
            self.server, "LOCAL_INFER_BACKEND", "endpoint"
        ), patch.object(
            self.server, "LOCAL_INFER_ENDPOINT", f"{NATIVE_OLLAMA_BASE}/api/generate"
        ), patch.object(
            self.server, "CODING_DEFAULT_MODEL", "qwen3.6-35b-a3b:iq1"
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
        self.assertFalse(out["ollama"]["runtime_pull_enabled"])
        self.assertEqual(out["ollama"]["installed_models_count"], 0)
        self.assertFalse(out["coding"]["default_model_installed"])
        self.assertFalse(out["coding"]["default_model_in_bootstrap_list"])
        self.assertFalse(out["coding"]["micro_model_installed"])
        self.assertFalse(out["coding"]["micro_model_in_bootstrap_list"])
        self.assertTrue(
            any("no default bundled model set is declared" in msg for msg in out["diagnostics"])
        )
        self.assertTrue(any("without /v1" in msg for msg in out["diagnostics"]))

    def test_local_model_status_reports_native_contract_and_installed_default(self):
        with patch.dict(
            os.environ,
            {
                "CONTINUE_OLLAMA_MODELS": "qwen3.6-35b-a3b:iq1,qwen2.5-coder:1.5b",
                "OLLAMA_ALLOW_PULL": "false",
            },
            clear=False,
        ), patch.object(self.server, "LOCAL_INFER_BACKEND", "endpoint"), patch.object(
            self.server, "LOCAL_INFER_ENDPOINT", f"{NATIVE_OLLAMA_BASE}/api/generate"
        ), patch.object(
            self.server, "CODING_DEFAULT_MODEL", "qwen3.6-35b-a3b:iq1"
        ), patch.object(
            self.server,
            "_fetch_ollama_tags",
            return_value={
                "url": f"{NATIVE_OLLAMA_BASE}/api/tags",
                "reachable": True,
                "status": 200,
                "model_ids": ["qwen3.6-35b-a3b:iq1"],
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
        self.assertFalse(out["ollama"]["runtime_pull_enabled"])
        self.assertEqual(out["ollama"]["installed_models"], ["qwen3.6-35b-a3b:iq1"])
        self.assertTrue(out["coding"]["default_model_installed"])
        self.assertTrue(out["coding"]["default_model_in_bootstrap_list"])
        self.assertFalse(out["coding"]["micro_model_installed"])
        self.assertTrue(out["coding"]["micro_model_in_bootstrap_list"])
        self.assertTrue(any("without /v1" in msg for msg in out["diagnostics"]))
