from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, Union
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from impact_ai.analysis import ImpactAnalyzer
from impact_ai.ai_client import OpenAICompatibleClient
from impact_ai.codebase_memory_graph import CodebaseMemoryKnowledgeGraph
from impact_ai.job_store import JsonFileJobStore
from impact_ai.runtime import (
    build_analyzer_from_env,
    create_configured_server,
    start_managed_codebase_memory_process,
    with_claude_network_env,
)


class RuntimeAssemblyTests(unittest.TestCase):
    def test_build_analyzer_from_env_wires_runtime_components(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "IMPACT_AI_WORKSPACE_ROOT": str(Path(temp_dir) / "repos"),
                "IMPACT_AI_PROFILE_ROOT": str(Path(temp_dir) / "profiles"),
                "IMPACT_AI_MAX_INPUT_TOKENS": "4096",
                "IMPACT_AI_MAX_OUTPUT_TOKENS": "512",
                "IMPACT_AI_RESERVED_OUTPUT_TOKENS": "256",
                "CODEBASE_MEMORY_INDEX_MODE": "full",
            }

            analyzer = build_analyzer_from_env(env, codebase_memory_client=FakeCodebaseMemoryClient())

        self.assertIsInstance(analyzer, ImpactAnalyzer)
        self.assertIsInstance(analyzer.knowledge_graph, CodebaseMemoryKnowledgeGraph)
        self.assertIsInstance(analyzer.ai_client, OpenAICompatibleClient)
        self.assertEqual(analyzer.token_budget.max_input_tokens, 4096)
        self.assertEqual(analyzer.token_budget.max_output_tokens, 512)
        self.assertEqual(analyzer.token_budget.reserved_output_tokens, 256)

    def test_build_analyzer_from_env_passes_ai_provider_configuration_to_client(self):
        analyzer = build_analyzer_from_env(
            {
                "DEEPSEEK_API_KEY": "deepseek-token",
                "DEEPSEEK_BASE_URL": "https://internal.example/deepseek/v1",
                "DEEPSEEK_MODEL": "deepseek-reasoner",
            },
            codebase_memory_client=FakeCodebaseMemoryClient(),
        )

        self.assertEqual(analyzer.ai_client.api_keys["DEEPSEEK_API_KEY"], "deepseek-token")
        self.assertEqual(analyzer.ai_client.base_urls["DEEPSEEK_BASE_URL"], "https://internal.example/deepseek/v1")
        self.assertEqual(analyzer.ai_client.models["deepseek"], "deepseek-reasoner")

    def test_build_analyzer_from_env_configures_cli_index_mode(self):
        analyzer = build_analyzer_from_env(
            {
                "CODEBASE_MEMORY_MCP_BIN": "codebase-memory-mcp",
                "CODEBASE_MEMORY_INDEX_MODE": "full",
            }
        )

        self.assertEqual(analyzer.knowledge_graph.client.index_mode, "full")

    def test_build_analyzer_from_env_configures_isolated_codebase_memory_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "repos"
            analyzer = build_analyzer_from_env({"IMPACT_AI_WORKSPACE_ROOT": str(workspace_root)})

            self.assertEqual(analyzer.knowledge_graph.client.cache_dir, Path(temp_dir) / "codebase-memory-cache")

    def test_build_analyzer_from_env_allows_codebase_memory_cache_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "custom-cache"
            analyzer = build_analyzer_from_env({"CODEBASE_MEMORY_CACHE_DIR": str(cache_dir)})

            self.assertEqual(analyzer.knowledge_graph.client.cache_dir, cache_dir)

    def test_with_claude_network_env_merges_proxy_bypass_without_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            claude_dir = home / ".claude"
            claude_dir.mkdir()
            (claude_dir / "settings.json").write_text(
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_API_KEY": "secret-token",
                            "ANTHROPIC_BASE_URL": "https://example.test",
                            "NO_PROXY": "hithink-oslm.myhexin.com,.myhexin.com",
                            "no_proxy": "hithink-oslm.myhexin.com,.myhexin.com",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"HOME": str(home), "NO_PROXY": "localhost", "no_proxy": "localhost"}, clear=False):
                merged = with_claude_network_env({})

        self.assertEqual(merged["NO_PROXY"], "localhost,hithink-oslm.myhexin.com,.myhexin.com")
        self.assertEqual(merged["no_proxy"], "localhost,hithink-oslm.myhexin.com,.myhexin.com")
        self.assertNotIn("ANTHROPIC_API_KEY", merged)
        self.assertNotIn("ANTHROPIC_BASE_URL", merged)

    def test_create_configured_server_attaches_analyzer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "history.json"
            server = create_configured_server(
                ("127.0.0.1", 0),
                env={
                    "IMPACT_AI_WORKSPACE_ROOT": str(Path(temp_dir) / "repos"),
                    "IMPACT_AI_PROFILE_ROOT": str(Path(temp_dir) / "profiles"),
                    "IMPACT_AI_HISTORY_PATH": str(history_path),
                },
                codebase_memory_client=FakeCodebaseMemoryClient(),
            )
            try:
                self.assertIsInstance(server.analyzer, ImpactAnalyzer)
                self.assertTrue(server.execute_async)
                self.assertIsInstance(server.job_store, JsonFileJobStore)
                self.assertEqual(server.job_store.path, history_path)
                self.assertEqual(server.profile_loader.root, Path(temp_dir) / "profiles")
            finally:
                server.server_close()

    def test_create_configured_server_uses_default_json_history_path(self):
        server = create_configured_server(
            ("127.0.0.1", 0),
            env={},
            codebase_memory_client=FakeCodebaseMemoryClient(),
        )
        try:
            self.assertIsInstance(server.job_store, JsonFileJobStore)
            self.assertEqual(server.job_store.path, Path(".impact-ai/history.json"))
        finally:
            server.server_close()

    def test_create_configured_server_applies_persisted_model_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_config_path = Path(temp_dir) / "model_config.json"
            model_config_path.write_text(
                json.dumps(
                    {
                        "configs": [
                            {
                                "provider_id": "deepseek",
                                "model": "deepseek-reasoner",
                                "base_url": "https://example.test/v1",
                                "api_key": "sk-test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            server = create_configured_server(
                ("127.0.0.1", 0),
                env={"IMPACT_AI_MODEL_CONFIG_PATH": str(model_config_path)},
                codebase_memory_client=FakeCodebaseMemoryClient(),
            )
            try:
                self.assertEqual(server.analyzer.ai_client.models["deepseek"], "deepseek-reasoner")
                self.assertEqual(server.analyzer.ai_client.base_urls["DEEPSEEK_BASE_URL"], "https://example.test/v1")
                self.assertEqual(server.analyzer.ai_client.api_keys["DEEPSEEK_API_KEY"], "sk-test")
            finally:
                server.server_close()

    def test_create_configured_server_seeds_model_config_from_template(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_config_path = Path(temp_dir) / ".impact-ai" / "model_config.json"
            template_path = Path(temp_dir) / "model_config.default.json"
            template_path.write_text(
                json.dumps(
                    {
                        "default_provider_id": "deepseek",
                        "configs": [
                            {
                                "provider_id": "deepseek",
                                "model": "deepseek-chat",
                                "base_url": "https://api.deepseek.com/v1",
                                "api_key": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            server = create_configured_server(
                ("127.0.0.1", 0),
                env={
                    "IMPACT_AI_MODEL_CONFIG_PATH": str(model_config_path),
                    "IMPACT_AI_DEFAULT_MODEL_CONFIG_PATH": str(template_path),
                },
                codebase_memory_client=FakeCodebaseMemoryClient(),
            )
            try:
                self.assertTrue(model_config_path.exists())
                payload = json.loads(model_config_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["default_provider_id"], "deepseek")
                self.assertEqual(payload["configs"][0]["api_key"], "")
            finally:
                server.server_close()

    def test_create_configured_server_does_not_overwrite_existing_model_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_config_path = Path(temp_dir) / ".impact-ai" / "model_config.json"
            template_path = Path(temp_dir) / "model_config.default.json"
            model_config_path.parent.mkdir(parents=True)
            model_config_path.write_text(
                json.dumps(
                    {
                        "default_provider_id": "deepseek",
                        "configs": [
                            {
                                "provider_id": "deepseek",
                                "model": "custom-model",
                                "base_url": "https://example.test/v1",
                                "api_key": "local-secret",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            template_path.write_text(
                json.dumps(
                    {
                        "default_provider_id": "openai",
                        "configs": [
                            {
                                "provider_id": "openai",
                                "model": "gpt-4.1",
                                "base_url": "https://api.openai.com/v1",
                                "api_key": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            server = create_configured_server(
                ("127.0.0.1", 0),
                env={
                    "IMPACT_AI_MODEL_CONFIG_PATH": str(model_config_path),
                    "IMPACT_AI_DEFAULT_MODEL_CONFIG_PATH": str(template_path),
                },
                codebase_memory_client=FakeCodebaseMemoryClient(),
            )
            try:
                payload = json.loads(model_config_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["configs"][0]["model"], "custom-model")
                self.assertEqual(payload["configs"][0]["api_key"], "local-secret")
            finally:
                server.server_close()

    def test_create_configured_server_applies_persisted_review_standards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            review_standards_path = Path(temp_dir) / "review_standards.json"
            review_standards_path.write_text(
                json.dumps(
                    {
                        "standards": [
                            {
                                "language": "lua",
                                "sections": {
                                    "正确性": ["检查 Lua 返回值契约。"],
                                    "语言专项": ["检查协程、元表和 require 缓存。"],
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            server = create_configured_server(
                ("127.0.0.1", 0),
                env={"IMPACT_AI_REVIEW_STANDARDS_PATH": str(review_standards_path)},
                codebase_memory_client=FakeCodebaseMemoryClient(),
            )
            try:
                self.assertEqual(server.review_standard_store.get("lua").sections["正确性"], ["检查 Lua 返回值契约。"])
                self.assertIs(server.analyzer.review_standard_store, server.review_standard_store)
            finally:
                server.server_close()

    def test_create_configured_server_manages_codebase_memory_process_lifecycle(self):
        launcher = FakeProcessLauncher()
        server = create_configured_server(
            ("127.0.0.1", 0),
            env={"CODEBASE_MEMORY_ENABLE_UI": "false"},
            codebase_memory_launcher=launcher,
        )

        self.assertEqual(launcher.calls[0][0], ["codebase-memory-mcp"])
        self.assertIs(server.codebase_memory_process.process, launcher.processes[0])

        server.server_close()

        self.assertTrue(launcher.processes[0].terminated)

    def test_create_configured_server_can_disable_codebase_memory_process_management(self):
        launcher = FakeProcessLauncher()
        server = create_configured_server(
            ("127.0.0.1", 0),
            env={
                "IMPACT_AI_MANAGE_CODEBASE_MEMORY": "false",
                "CODEBASE_MEMORY_ENABLE_UI": "false",
            },
            codebase_memory_launcher=launcher,
        )
        try:
            self.assertEqual(launcher.calls, [])
            self.assertIsNone(server.codebase_memory_process.process)
        finally:
            server.server_close()

    def test_start_managed_codebase_memory_process_launches_ui_without_blocking(self):
        launcher = FakeProcessLauncher()
        with patch("impact_ai.runtime.subprocess.run") as blocking_run:
            managed_process = start_managed_codebase_memory_process(
                {
                    "CODEBASE_MEMORY_ENABLE_UI": "true",
                    "CODEBASE_MEMORY_UI_PORT": "9900",
                },
                launcher=launcher,
            )

        self.assertEqual(blocking_run.call_count, 0)
        self.assertEqual(launcher.calls[0][0], ["codebase-memory-mcp", "--ui=true", "--port=9900"])
        self.assertEqual(launcher.calls[1][0], ["codebase-memory-mcp"])

        managed_process.close()

        self.assertTrue(launcher.processes[0].terminated)
        self.assertTrue(launcher.processes[1].terminated)


class FakeCodebaseMemoryClient:
    def index_repository(self, repo_path, project_name):
        return project_name

    def trace_two_hop(self, project_id, function_name, direction, depth=2):
        return []


class FakeProcessLauncher:
    def __init__(self):
        self.calls = []
        self.processes = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args[0], kwargs))
        process = FakeProcess()
        self.processes.append(process)
        return process


class FakeProcess:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


if __name__ == "__main__":
    unittest.main()
