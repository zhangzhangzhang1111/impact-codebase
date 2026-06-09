import json
import tempfile
import unittest
from pathlib import Path

from impact_ai.analysis import ImpactAnalyzer
from impact_ai.ai_client import OpenAICompatibleClient
from impact_ai.codebase_memory_graph import CodebaseMemoryKnowledgeGraph
from impact_ai.job_store import JsonFileJobStore
from impact_ai.runtime import build_analyzer_from_env, create_configured_server


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
