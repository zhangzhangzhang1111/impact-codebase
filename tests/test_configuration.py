import tempfile
import unittest
from pathlib import Path

from impact_ai.ai_providers import provider_catalog
from impact_ai.model_config import JsonFileModelConfigStore
from impact_ai.project_profiles import ProjectProfileLoader
from impact_ai.review_standards import standard_for_language
from impact_ai.token_budget import BudgetExceededError, TokenBudget


class ConfigurationTests(unittest.TestCase):
    def test_provider_catalog_includes_global_and_chinese_mainstream_models(self):
        providers = provider_catalog()
        provider_ids = {provider.id for provider in providers}

        self.assertTrue({"openai", "anthropic", "gemini"}.issubset(provider_ids))
        self.assertTrue({"deepseek", "qwen", "zhipu", "moonshot", "doubao", "hunyuan", "ernie"}.issubset(provider_ids))

        for provider in providers:
            self.assertGreater(provider.max_input_tokens, 0)
            self.assertGreater(provider.max_output_tokens, 0)
            self.assertTrue(provider.api_key_env.endswith("_API_KEY"))
            self.assertTrue(provider.model_env.endswith("_MODEL"))
            self.assertTrue(provider.default_model)

    def test_token_budget_chunks_context_and_reserves_output_tokens(self):
        budget = TokenBudget(max_input_tokens=40, max_output_tokens=12, reserved_output_tokens=8)
        chunks = budget.chunk_text(" ".join([f"symbol_{index}_changed_context" for index in range(20)]))

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(budget.estimate_tokens(chunk), 32)

    def test_token_budget_rejects_impossible_output_reservation(self):
        with self.assertRaises(BudgetExceededError):
            TokenBudget(max_input_tokens=20, max_output_tokens=8, reserved_output_tokens=21)

    def test_project_profile_loader_reads_business_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "payments"
            profile_dir.mkdir()
            (profile_dir / "business.md").write_text("# Payments\n\nRefunds require audit logs.", encoding="utf-8")

            profile = ProjectProfileLoader(Path(temp_dir)).load("payments")

        self.assertEqual(profile.project_name, "payments")
        self.assertIn("Refunds require audit logs", profile.business_context)
        self.assertEqual(profile.source_path.name, "business.md")

    def test_json_model_config_store_persists_default_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "model_config.json"
            store = JsonFileModelConfigStore(config_path)

            store.save("deepseek", model="deepseek-reasoner", base_url="https://example.test/v1", api_key="sk-test")
            store.set_default_provider_id("deepseek")
            reloaded = JsonFileModelConfigStore(config_path)

        self.assertEqual(reloaded.default_provider_id(), "deepseek")
        self.assertEqual(reloaded.get("deepseek").model, "deepseek-reasoner")

    def test_project_profile_loader_sanitizes_project_name_for_business_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "profiles"
            outside_root = Path(temp_dir) / "team"
            loader = ProjectProfileLoader(root)

            saved = loader.save("../team/payments api", "# Payments\n\nSpecial settlement rules.")
            loaded = loader.load("../team/payments api")

            self.assertEqual(saved.project_name, "../team/payments api")
            self.assertEqual(saved.source_path, root / "team-payments-api" / "business.md")
            self.assertFalse((outside_root / "payments api" / "business.md").exists())
            self.assertIn("Special settlement rules", loaded.business_context)
            self.assertEqual(loaded.source_path, saved.source_path)

    def test_review_standard_defaults_for_languages_and_unknown_language(self):
        python_standard = standard_for_language("python")
        lua_standard = standard_for_language("lua")
        generic_standard = standard_for_language("made-up-language")

        self.assertEqual(python_standard.language, "python")
        self.assertIn("正确性", python_standard.sections)
        self.assertIn("安全性", python_standard.sections)
        self.assertIn("测试", python_standard.sections)
        self.assertEqual(lua_standard.language, "lua")
        self.assertIn("语言专项", lua_standard.sections)
        self.assertTrue(any("协程" in item or "元表" in item for item in lua_standard.sections["语言专项"]))
        self.assertEqual(generic_standard.language, "generic")
        self.assertIn("可维护性", generic_standard.sections)


if __name__ == "__main__":
    unittest.main()
