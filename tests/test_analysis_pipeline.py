import tempfile
import json
import unittest
from pathlib import Path

from impact_ai.analysis import ImpactAnalyzer
from impact_ai.ai_providers import provider_catalog
from impact_ai.knowledge_graph import CallGraph, ChangedFunction, DiffAnalysis
from impact_ai.models import ImpactAnalysisRequest
from impact_ai.project_profiles import ProjectProfileLoader
from impact_ai.token_budget import TokenBudget


class FakeKnowledgeGraph:
    def __init__(self):
        self.diff_request = None
        self.call_graph_request = None

    def changed_functions(self, request):
        self.diff_request = request
        return DiffAnalysis(
            project_name=request.project_name,
            changed_functions=[
                ChangedFunction(
                    qualified_name="payments.service.refund",
                    language="python",
                    file_path="payments/service.py",
                    signature="def refund(order_id: str) -> RefundResult",
                    diff_hunk="+    audit_refund(order_id)",
                )
            ],
        )

    def two_hop_call_graph(self, project_name, functions, depth=2):
        self.call_graph_request = (project_name, tuple(functions), depth)
        return CallGraph(
            project_name=project_name,
            depth=depth,
            inbound={"payments.service.refund": ["api.refunds.post_refund", "jobs.refunds.retry_refund"]},
            outbound={"payments.service.refund": ["payments.audit.audit_refund"]},
        )


class FakeAIClient:
    def __init__(self):
        self.prompts = []

    def complete(self, prompt, provider, max_output_tokens):
        self.prompts.append((prompt, provider.id, max_output_tokens))
        return {
            "impact_summary": "Refund changes affect API and retry jobs.",
            "review_findings": ["Check audit logging failure behavior."],
            "test_cases": ["Add API refund test covering audit failure."],
        }


class StructuredAIClient:
    def __init__(self):
        self.prompts = []

    def complete(self, prompt, provider, max_output_tokens):
        self.prompts.append((prompt, provider.id, max_output_tokens))
        return {
            "impact_summary": "Refund callers must handle audit failures.",
            "review_findings": [
                {
                    "function": "payments.service.refund",
                    "standard": "Correctness",
                    "severity": "high",
                    "finding": "Audit write failure is not handled before returning success.",
                    "impacted_callers": ["api.refunds.post_refund"],
                }
            ],
            "test_cases": [
                {
                    "name": "Refund API audit failure regression",
                    "type": "integration",
                    "target": "api.refunds.post_refund",
                    "covers": ["payments.service.refund", "audit failure"],
                }
            ],
        }


class AnalysisPipelineTests(unittest.TestCase):
    def test_analyzer_combines_diff_graph_business_context_and_ai_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "payments"
            profile_dir.mkdir()
            (profile_dir / "business.md").write_text("Refunds require immutable audit logs.", encoding="utf-8")
            kg = FakeKnowledgeGraph()
            ai = FakeAIClient()
            provider = next(provider for provider in provider_catalog() if provider.id == "deepseek")
            analyzer = ImpactAnalyzer(
                knowledge_graph=kg,
                ai_client=ai,
                profile_loader=ProjectProfileLoader(Path(temp_dir)),
                token_budget=TokenBudget(max_input_tokens=4_096, max_output_tokens=64, reserved_output_tokens=40),
            )

            request = ImpactAnalysisRequest(
                git_url="https://github.com/acme/payments.git",
                branch="feature/refund-audit",
                before_commit="abc123",
                after_commit="def456",
                project_name="payments",
                provider_id="deepseek",
            )
            progress = []
            result = analyzer.analyze(request)
            progressed_result = analyzer.analyze(request, progress.append)

        self.assertEqual(kg.diff_request, request)
        self.assertEqual(kg.call_graph_request[0], "payments")
        self.assertEqual(kg.call_graph_request[2], 2)
        self.assertEqual(result.project_name, "payments")
        self.assertEqual(result.changed_functions[0].qualified_name, "payments.service.refund")
        self.assertEqual(result.call_graph.depth, 2)
        self.assertIn("Refund changes affect API", result.impact_summary)
        self.assertIn("Check audit logging", result.review_findings[0])
        self.assertIn("API refund test", result.test_cases[0])
        self.assertEqual(result.token_usage["max_input_tokens"], 4_096)
        self.assertEqual(result.token_usage["max_output_tokens"], 64)
        self.assertEqual(result.token_usage["reserved_output_tokens"], 40)
        self.assertEqual(result.token_usage["prompt_chunks"], 1)
        self.assertEqual(len(result.token_usage["chunk_input_tokens"]), 1)

        joined_prompts = "\n".join(prompt for prompt, _provider_id, _max_output in ai.prompts)
        self.assertIn("Refunds require immutable audit logs", joined_prompts)
        self.assertIn("所有报告结果必须使用中文输出", joined_prompts)
        self.assertIn("中文影响面分析", joined_prompts)
        self.assertIn("business_feature", joined_prompts)
        self.assertIn("business_subfeature", joined_prompts)
        self.assertIn("impact_level", joined_prompts)
        self.assertIn("test_id", joined_prompts)
        self.assertIn("affected_business_flows", joined_prompts)
        self.assertIn("changed_symbols", joined_prompts)
        self.assertIn("affected_files", joined_prompts)
        self.assertIn("Stock HQ Query", joined_prompts)
        self.assertIn("业务大类", joined_prompts)
        self.assertIn("业务小功能", joined_prompts)
        self.assertIn("正确性", joined_prompts)
        self.assertIn('\\"change_type\\": \\"modified\\"', joined_prompts)
        self.assertIn("call_graph_summary", joined_prompts)
        self.assertIn("inbound_count", joined_prompts)
        self.assertIn("outbound_count", joined_prompts)
        self.assertIn("不得写无下游", joined_prompts)
        self.assertIn("payments.audit.audit_refund", joined_prompts)
        self.assertEqual(progressed_result.project_name, "payments")
        self.assertEqual(
            progress,
            [
                "changed_functions",
                "two_hop_call_graph",
                "prompt_build",
                "ai_request",
                "ai_response",
            ],
        )

    def test_analyzer_preserves_structured_review_findings_and_test_cases(self):
        kg = FakeKnowledgeGraph()
        ai = StructuredAIClient()
        analyzer = ImpactAnalyzer(
            knowledge_graph=kg,
            ai_client=ai,
            profile_loader=ProjectProfileLoader(Path("/missing")),
            token_budget=TokenBudget(max_input_tokens=4_096, max_output_tokens=256, reserved_output_tokens=80),
        )

        result = analyzer.analyze(
            ImpactAnalysisRequest(
                git_url="https://github.com/acme/payments.git",
                branch="main",
                before_commit="abc123",
                after_commit="def456",
                project_name="payments",
                provider_id="openai",
            )
        )

        self.assertEqual(result.structured_review_findings[0]["standard"], "Correctness")
        self.assertEqual(result.structured_review_findings[0]["function"], "payments.service.refund")
        self.assertIn("Audit write failure", result.review_findings[0])
        self.assertEqual(result.structured_test_cases[0]["type"], "integration")
        self.assertIn("Refund API audit failure regression", result.test_cases[0])

    def test_analyzer_passes_requested_call_graph_depth_to_knowledge_graph(self):
        kg = FakeKnowledgeGraph()
        ai = FakeAIClient()
        analyzer = ImpactAnalyzer(
            knowledge_graph=kg,
            ai_client=ai,
            profile_loader=ProjectProfileLoader(Path("/missing")),
            token_budget=TokenBudget(max_input_tokens=4_096, max_output_tokens=256, reserved_output_tokens=80),
        )

        result = analyzer.analyze(
            ImpactAnalysisRequest(
                git_url="https://github.com/acme/payments.git",
                branch="main",
                before_commit="abc123",
                after_commit="def456",
                project_name="payments",
                provider_id="openai",
                call_graph_depth=4,
            )
        )

        self.assertEqual(kg.call_graph_request[2], 4)
        self.assertEqual(result.call_graph.depth, 4)

    def test_analyzer_does_not_call_ai_when_no_changed_functions_are_detected(self):
        kg = FakeKnowledgeGraph()
        kg.changed_functions = lambda request: DiffAnalysis(project_name=request.project_name, changed_functions=[])
        kg.two_hop_call_graph = lambda project_name, functions, depth=2: CallGraph(project_name=project_name, depth=depth)
        ai = FakeAIClient()
        analyzer = ImpactAnalyzer(
            knowledge_graph=kg,
            ai_client=ai,
            profile_loader=ProjectProfileLoader(Path("/missing")),
            token_budget=TokenBudget(max_input_tokens=4_096, max_output_tokens=256, reserved_output_tokens=80),
        )

        result = analyzer.analyze(
            ImpactAnalysisRequest(
                git_url="https://github.com/acme/payments.git",
                branch="main",
                before_commit="abc123",
                after_commit="def456",
                project_name="payments",
                provider_id="openai",
            )
        )

        self.assertEqual(ai.prompts, [])
        self.assertEqual(result.changed_functions, [])
        self.assertEqual(result.structured_review_findings, [])
        self.assertEqual(result.structured_test_cases, [])
        self.assertIn("未检测到改动函数", result.impact_summary)

    def test_analyzer_caps_configured_token_budget_at_provider_limits(self):
        kg = FakeKnowledgeGraph()
        ai = FakeAIClient()
        analyzer = ImpactAnalyzer(
            knowledge_graph=kg,
            ai_client=ai,
            profile_loader=ProjectProfileLoader(Path("/missing")),
            token_budget=TokenBudget(
                max_input_tokens=1_000_000,
                max_output_tokens=100_000,
                reserved_output_tokens=80_000,
            ),
        )

        result = analyzer.analyze(
            ImpactAnalysisRequest(
                git_url="https://github.com/acme/payments.git",
                branch="main",
                before_commit="abc123",
                after_commit="def456",
                project_name="payments",
                provider_id="deepseek",
            )
        )

        self.assertEqual(result.token_usage["max_input_tokens"], 64_000)
        self.assertEqual(result.token_usage["max_output_tokens"], 8_192)
        self.assertEqual(result.token_usage["reserved_output_tokens"], 8_192)
        self.assertEqual(ai.prompts[0][2], 8_192)

    def test_analyzer_chunks_prompt_to_fit_provider_input_budget(self):
        kg = FakeKnowledgeGraph()
        kg.two_hop_call_graph = lambda project_name, functions, depth=2: CallGraph(
            project_name=project_name,
            depth=depth,
            inbound={"payments.service.refund": [f"caller_{index}" for index in range(100)]},
            outbound={"payments.service.refund": [f"callee_{index}" for index in range(100)]},
        )
        ai = FakeAIClient()
        analyzer = ImpactAnalyzer(
            knowledge_graph=kg,
            ai_client=ai,
            profile_loader=ProjectProfileLoader(Path("/missing")),
            token_budget=TokenBudget(max_input_tokens=950, max_output_tokens=32, reserved_output_tokens=40),
        )

        result = analyzer.analyze(
            ImpactAnalysisRequest(
                git_url="https://github.com/acme/payments.git",
                branch="main",
                before_commit="abc123",
                after_commit="def456",
                project_name="payments",
                provider_id="openai",
            )
        )

        self.assertGreater(len(ai.prompts), 1)
        self.assertEqual(len(ai.prompts), result.token_usage["prompt_chunks"])
        self.assertEqual(result.token_usage["max_input_tokens"], 950)
        self.assertEqual(result.token_usage["max_output_tokens"], 32)
        self.assertEqual(result.token_usage["reserved_output_tokens"], 40)
        self.assertEqual(len(result.token_usage["chunk_input_tokens"]), len(ai.prompts))
        for prompt, _provider_id, _max_output in ai.prompts:
            self.assertLessEqual(analyzer.token_budget.estimate_tokens(prompt), 910)
            payload = json.loads(prompt)
            self.assertEqual(payload["task"], "分析代码改动影响面，生成中文代码评审发现和中文测试用例。")
            self.assertIn("json", payload["response_instruction"])
            self.assertIn("中文", payload["response_instruction"])
            self.assertIn("business_feature", json.dumps(payload["output_contract"], ensure_ascii=False))
            self.assertIn("business_subfeature", json.dumps(payload["output_contract"], ensure_ascii=False))
            self.assertIn("impact_level", json.dumps(payload["output_contract"], ensure_ascii=False))
            self.assertIn("affected_business_flows", json.dumps(payload["output_contract"], ensure_ascii=False))
            self.assertIn("changed_symbols", json.dumps(payload["output_contract"], ensure_ascii=False))
            self.assertIn("affected_files", json.dumps(payload["output_contract"], ensure_ascii=False))
            self.assertIn("call_graph_summary", payload)
            self.assertIn("不得写无下游", payload["call_graph_consistency_requirement"])
            self.assertIn("inbound_count", json.dumps(payload["output_contract"], ensure_ascii=False))
            self.assertEqual(payload["project"]["name"], "payments")
            self.assertIn("output_contract", payload)
            self.assertIn("context_chunk", payload)
            self.assertIn("chunk_index", payload["context_chunk"])
            self.assertIn("total_chunks", payload["context_chunk"])
            self.assertIn("text", payload["context_chunk"])

    def test_analyzer_splits_single_oversized_context_token_to_fit_input_budget(self):
        kg = FakeKnowledgeGraph()
        kg.changed_functions = lambda request: DiffAnalysis(
            project_name=request.project_name,
            changed_functions=[
                ChangedFunction(
                    qualified_name="payments.service.refund",
                    language="python",
                    file_path="payments/service.py",
                    signature="def refund(order_id)",
                    diff_hunk="+" + ("A" * 5_000),
                )
            ],
        )
        kg.two_hop_call_graph = lambda project_name, functions, depth=2: CallGraph(
            project_name=project_name,
            depth=depth,
            inbound={},
            outbound={},
        )
        ai = FakeAIClient()
        analyzer = ImpactAnalyzer(
            knowledge_graph=kg,
            ai_client=ai,
            profile_loader=ProjectProfileLoader(Path("/missing")),
            token_budget=TokenBudget(max_input_tokens=900, max_output_tokens=32, reserved_output_tokens=80),
        )

        result = analyzer.analyze(
            ImpactAnalysisRequest(
                git_url="https://github.com/acme/payments.git",
                branch="main",
                before_commit="abc123",
                after_commit="def456",
                project_name="payments",
                provider_id="openai",
            )
        )

        self.assertGreater(result.prompt_chunks, 1)
        for prompt, _provider_id, _max_output in ai.prompts:
            self.assertLessEqual(analyzer.token_budget.estimate_tokens(prompt), 820)


if __name__ == "__main__":
    unittest.main()
