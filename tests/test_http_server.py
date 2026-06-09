import http.client
import json
import tempfile
import time
import threading
import unittest
from pathlib import Path

from impact_ai.analysis import ImpactAnalysisResult
from impact_ai.http_server import create_server
from impact_ai.job_store import InMemoryJobStore
from impact_ai.knowledge_graph import CallGraph
from impact_ai.model_config import InMemoryModelConfigStore
from impact_ai.project_profiles import ProjectProfileLoader


class HttpServerTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server(("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        headers = {}
        if body is not None:
            body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        connection.close()
        return response.status, response.getheader("Content-Type"), response_body

    def test_health_endpoint(self):
        status, content_type, body = self.request("GET", "/health")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_provider_catalog_endpoint(self):
        status, content_type, body = self.request("GET", "/api/providers")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        payload = json.loads(body)
        self.assertIn("providers", payload)
        providers = {provider["id"]: provider for provider in payload["providers"]}
        self.assertIn("deepseek", providers)
        self.assertIn("openai", providers)
        self.assertEqual(providers["deepseek"]["model_env"], "DEEPSEEK_MODEL")
        self.assertEqual(providers["openai"]["api_key_env"], "OPENAI_API_KEY")

    def test_review_standard_endpoint(self):
        status, content_type, body = self.request("GET", "/api/review-standards/python")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        payload = json.loads(body)
        self.assertEqual(payload["language"], "python")
        self.assertIn("正确性", payload["sections"])
        self.assertIn("语言专项", payload["sections"])

    def test_review_standards_catalog_endpoint(self):
        status, content_type, body = self.request("GET", "/api/review-standards")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        payload = json.loads(body)
        standards = {standard["language"]: standard for standard in payload["standards"]}
        self.assertIn("python", standards)
        self.assertIn("java", standards)
        self.assertIn("kotlin", standards)
        self.assertIn("lua", standards)
        self.assertIn("正确性", standards["python"]["sections"])
        self.assertIn("语言专项", standards["kotlin"]["sections"])

    def test_dashboard_entrypoint(self):
        status, content_type, body = self.request("GET", "/")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/html; charset=utf-8")
        html = body.decode("utf-8")
        self.assertIn("Impact Analysis AI", html)
        self.assertIn("代码改动影响面分析", html)
        self.assertIn("模型配置", html)
        self.assertIn("默认分析模型", html)
        self.assertIn("default-provider-select", html)
        self.assertIn("设为默认分析模型", html)
        self.assertIn("使用供应商默认模型", html)
        self.assertIn("default_provider_id", html)
        self.assertIn("is_default", html)
        self.assertIn("保存模型配置", html)
        self.assertIn("测试模型", html)
        self.assertIn("/api/model-configs/default", html)
        self.assertIn("/api/model-configs/", html)
        self.assertIn("/test", html)
        self.assertIn("/api/model-configs", html)
        self.assertIn("/api/providers", html)
        self.assertIn("/api/analyses", html)
        self.assertIn("模型环境变量", html)
        self.assertIn("密钥环境变量", html)
        self.assertIn("Base URL 环境变量", html)
        self.assertIn("/api/review-standards", html)
        self.assertIn("评审规范", html)
        self.assertIn("保存评审规范", html)
        self.assertIn("review-standard-form", html)
        self.assertIn("review-standards-table", html)
        self.assertIn("结构化评审发现", html)
        self.assertIn("结构化测试用例", html)
        self.assertIn("structured_review_findings", html)
        self.assertIn("structured_test_cases", html)
        self.assertIn("影响摘要", html)
        self.assertIn("测试用例", html)
        self.assertIn("测试标签", html)
        self.assertIn("受影响功能点", html)
        self.assertIn("回归测试清单", html)
        self.assertIn("变更符号", html)
        self.assertIn("涉及文件", html)
        self.assertIn("test-tag-card", html)
        self.assertIn("test-chip", html)
        self.assertIn("affected_business_flows", html)
        self.assertIn("changed_symbols", html)
        self.assertIn("affected_files", html)
        self.assertIn("进度", html)
        self.assertIn("阶段进度", html)
        self.assertIn("调用链深度", html)
        self.assertIn('name="call_graph_depth"', html)
        self.assertIn('max="5"', html)
        self.assertIn("按配置深度分析调用链路", html)
        self.assertIn("<progress", html)
        self.assertIn("progressPercent", html)
        self.assertIn("Token 用量", html)
        self.assertIn("错误", html)
        self.assertIn("分析日志", html)
        self.assertIn("renderAnalysisLogs", html)
        self.assertIn("log-entry", html)
        self.assertIn("log-detail", html)
        self.assertIn("点击每条日志展开详情", html)
        self.assertIn("失败原因", html)
        self.assertIn("分析详情", html)
        self.assertIn("analysis-detail", html)
        self.assertIn("renderAnalysisDetail", html)
        self.assertIn("调用链路", html)
        self.assertIn("改动函数与调用链路", html)
        self.assertIn("上游调用方", html)
        self.assertIn("下游被调方", html)
        self.assertIn("call-tree", html)
        self.assertIn("call-function-details", html)
        self.assertIn("call-file-details", html)
        self.assertIn("renderCallGraphTree", html)
        self.assertIn("groupCallsByFile", html)
        self.assertIn("call_graph", html)
        self.assertIn("change_type", html)
        self.assertIn("查看", html)
        self.assertIn("join(' / ')", html)
        self.assertIn("业务说明", html)
        self.assertIn("profile-panel", html)
        self.assertIn('data-tab-target="profile-panel"', html)
        self.assertIn("defaultProjectNameFromGitUrl", html)
        self.assertIn("dataset.userEdited", html)
        self.assertIn("markProjectNameEdited", html)
        self.assertIn("!userEdited", html)
        self.assertIn("Kong-kong", html)
        self.assertIn("/api/projects/", html)
        self.assertIn("/business-context", html)
        self.assertIn("首页", html)
        self.assertIn("模型与 API 配置", html)
        self.assertIn("API 配置说明", html)
        self.assertNotIn('data-tab-target="api-panel"', html)
        self.assertNotIn("知识图谱 UI", html)
        self.assertNotIn('data-tab-target="graph-ui-panel"', html)
        self.assertNotIn("codebase-memory-ui-frame", html)
        self.assertNotIn("http://localhost:9749", html)
        self.assertNotIn("refresh-codebase-memory-ui", html)
        self.assertIn("分析历史", html)
        self.assertIn("分析详情", html)
        self.assertIn("历史搜索", html)
        self.assertIn("history-search", html)
        self.assertIn("data-tab-target", html)
        self.assertIn("showTab", html)
        self.assertIn("报告结果将以中文生成", html)
        self.assertIn("analysis-list", html)
        self.assertIn("shortCommit", html)
        self.assertIn("完整 Commit", html)
        self.assertIn("commit-range-compact", html)
        self.assertIn("下载 Markdown", html)
        self.assertIn("downloadMarkdownReport", html)
        self.assertIn("reportMarkdown", html)
        self.assertIn("按业务功能分组", html)
        self.assertIn("影响等级", html)
        self.assertIn("business_feature", html)
        self.assertIn("business_subfeature", html)
        self.assertIn("impact_level", html)
        self.assertIn("report-section", html)
        self.assertIn("业务大类", html)
        self.assertIn("业务小功能", html)
        self.assertIn("Git Diff", html)
        self.assertIn("diff_hunk", html)
        self.assertIn("<details", html)
        self.assertIn("diff-pre", html)
        self.assertIn("DeusData/codebase-memory-mcp", html)
        self.assertIn("调用链状态", html)
        self.assertIn("trace_status", html)
        self.assertIn("图谱成功，源码补充", html)
        self.assertIn("源码降级成功", html)
        self.assertIn("索引失败", html)

    def test_model_config_endpoints_persist_provider_overrides(self):
        store = InMemoryModelConfigStore()
        server = create_server(("127.0.0.1", 0), model_config_store=store)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            connection = http.client.HTTPConnection(host, port, timeout=2)
            body = json.dumps(
                {
                    "model": "deepseek-reasoner",
                    "api_key": "sk-test",
                    "base_url": "https://example.test/v1",
                }
            ).encode("utf-8")
            connection.request("PUT", "/api/model-configs/deepseek", body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()

            list_connection = http.client.HTTPConnection(host, port, timeout=2)
            list_connection.request("GET", "/api/model-configs")
            list_response = list_connection.getresponse()
            list_payload = json.loads(list_response.read())
            list_connection.close()

            default_connection = http.client.HTTPConnection(host, port, timeout=2)
            default_body = json.dumps({"provider_id": "deepseek"}).encode("utf-8")
            default_connection.request(
                "POST",
                "/api/model-configs/default",
                body=default_body,
                headers={"Content-Type": "application/json"},
            )
            default_response = default_connection.getresponse()
            default_payload = json.loads(default_response.read())
            default_connection.close()

            updated_connection = http.client.HTTPConnection(host, port, timeout=2)
            updated_connection.request("GET", "/api/model-configs")
            updated_response = updated_connection.getresponse()
            updated_payload = json.loads(updated_response.read())
            updated_connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["provider_id"], "deepseek")
        self.assertEqual(payload["model"], "deepseek-reasoner")
        self.assertEqual(payload["base_url"], "https://example.test/v1")
        self.assertEqual(payload["api_key"], "sk-test")
        self.assertTrue(payload["api_key_configured"])
        configs = {item["provider_id"]: item for item in list_payload["configs"]}
        self.assertEqual(configs["deepseek"]["model"], "deepseek-reasoner")
        self.assertEqual(configs["deepseek"]["api_key"], "sk-test")
        self.assertTrue(configs["deepseek"]["api_key_configured"])
        self.assertEqual(default_response.status, 200)
        self.assertEqual(default_payload["default_provider_id"], "deepseek")
        self.assertEqual(updated_payload["default_provider_id"], "deepseek")
        updated_configs = {item["provider_id"]: item for item in updated_payload["configs"]}
        self.assertTrue(updated_configs["deepseek"]["is_default"])
        self.assertFalse(updated_configs["openai"]["is_default"])

    def test_review_standard_endpoint_supports_editing_custom_chinese_standards(self):
        put_status, _put_type, put_body = self.request(
            "PUT",
            "/api/review-standards/lua",
            {
                "sections": {
                    "正确性": ["检查 Lua 模块返回值和调用方契约。"],
                    "语言专项": ["检查协程、元表、闭包 upvalue 和 require 缓存。"],
                }
            },
        )
        get_status, _get_type, get_body = self.request("GET", "/api/review-standards/lua")
        catalog_status, _catalog_type, catalog_body = self.request("GET", "/api/review-standards")

        put_payload = json.loads(put_body)
        get_payload = json.loads(get_body)
        catalog = {item["language"]: item for item in json.loads(catalog_body)["standards"]}

        self.assertEqual(put_status, 200)
        self.assertEqual(put_payload["language"], "lua")
        self.assertIn("协程", put_payload["sections"]["语言专项"][0])
        self.assertEqual(get_status, 200)
        self.assertEqual(get_payload, put_payload)
        self.assertEqual(catalog_status, 200)
        self.assertEqual(catalog["lua"], put_payload)

    def test_model_config_test_endpoint_verifies_provider_with_current_ai_client(self):
        analyzer = ModelProbeAnalyzer()
        server = create_server(("127.0.0.1", 0), analyzer=analyzer)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            connection = http.client.HTTPConnection(host, port, timeout=2)
            connection.request("POST", "/api/model-configs/deepseek/test")
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider_id"], "deepseek")
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertEqual(analyzer.ai_client.calls[0]["provider_id"], "deepseek")
        self.assertIn("Return JSON", analyzer.ai_client.calls[0]["prompt"])

    def test_analysis_submission_and_history_endpoints(self):
        request_payload = {
            "git_url": "https://github.com/acme/payments.git",
            "branch": "feature/refund-audit",
            "before_commit": "abc123",
            "after_commit": "def456",
            "project_name": "payments",
            "provider_id": "deepseek",
            "call_graph_depth": "4",
        }

        submit_status, _submit_type, submit_body = self.request("POST", "/api/analyses", request_payload)
        submit_payload = json.loads(submit_body)
        job_id = submit_payload["job"]["id"]

        list_status, _list_type, list_body = self.request("GET", "/api/analyses")
        detail_status, _detail_type, detail_body = self.request("GET", f"/api/analyses/{job_id}")

        self.assertEqual(submit_status, 202)
        self.assertEqual(submit_payload["job"]["status"], "queued")
        self.assertEqual(submit_payload["job"]["request"]["project_name"], "payments")
        self.assertEqual(submit_payload["job"]["request"]["call_graph_depth"], 4)
        self.assertIn("logs", submit_payload["job"])
        self.assertEqual(submit_payload["job"]["logs"][0]["stage"], "queued")
        self.assertEqual(list_status, 200)
        self.assertEqual(json.loads(list_body)["jobs"][0]["id"], job_id)
        self.assertEqual(detail_status, 200)
        self.assertEqual(json.loads(detail_body)["job"]["request"]["after_commit"], "def456")
        self.assertEqual(json.loads(detail_body)["job"]["request"]["call_graph_depth"], 4)
        self.assertEqual(json.loads(detail_body)["job"]["logs"][0]["message"], "任务已创建并进入队列")

    def test_analysis_submission_clamps_call_graph_depth(self):
        request_payload = {
            "git_url": "https://github.com/acme/payments.git",
            "branch": "main",
            "before_commit": "abc123",
            "after_commit": "def456",
            "project_name": "payments",
            "provider_id": "deepseek",
            "call_graph_depth": "99",
        }

        submit_status, _submit_type, submit_body = self.request("POST", "/api/analyses", request_payload)
        submit_payload = json.loads(submit_body)

        self.assertEqual(submit_status, 202)
        self.assertEqual(submit_payload["job"]["request"]["call_graph_depth"], 5)

    def test_analysis_submission_defaults_project_name_from_git_owner_and_repo(self):
        request_payload = {
            "git_url": "https://github.com/Kong/kong.git",
            "branch": "master",
            "before_commit": "abc123",
            "after_commit": "def456",
            "project_name": "",
            "provider_id": "deepseek",
        }

        submit_status, _submit_type, submit_body = self.request("POST", "/api/analyses", request_payload)
        submit_payload = json.loads(submit_body)

        self.assertEqual(submit_status, 202)
        self.assertEqual(submit_payload["job"]["request"]["project_name"], "Kong-kong")

    def test_analysis_submission_rejects_empty_required_fields(self):
        status, _content_type, body = self.request(
            "POST",
            "/api/analyses",
            {
                "git_url": "",
                "branch": "main",
                "before_commit": "abc123",
                "after_commit": "def456",
                "project_name": "payments",
                "provider_id": "deepseek",
            },
        )
        payload = json.loads(body)

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_request")
        self.assertIn("git_url", payload["fields"])
        self.assertEqual(self.server.job_store.list(), [])

    def test_analysis_submission_rejects_unknown_provider(self):
        status, _content_type, body = self.request(
            "POST",
            "/api/analyses",
            {
                "git_url": "https://github.com/acme/payments.git",
                "branch": "main",
                "before_commit": "abc123",
                "after_commit": "def456",
                "project_name": "payments",
                "provider_id": "unknown-ai",
            },
        )
        payload = json.loads(body)

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "unsupported_provider")
        self.assertEqual(payload["provider_id"], "unknown-ai")
        self.assertIn("deepseek", payload["supported_providers"])
        self.assertEqual(self.server.job_store.list(), [])

    def test_create_server_accepts_preconfigured_job_store(self):
        store = InMemoryJobStore()

        server = create_server(("127.0.0.1", 0), job_store=store)
        try:
            self.assertIs(server.job_store, store)
        finally:
            server.server_close()


class ProjectProfileHttpServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.profile_loader = ProjectProfileLoader(Path(self.temp_dir.name))
        self.server = create_server(("127.0.0.1", 0), profile_loader=self.profile_loader)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.temp_dir.cleanup()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        headers = {}
        if body is not None:
            body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        connection.close()
        return response.status, response.getheader("Content-Type"), response_body

    def test_project_business_context_endpoint_returns_empty_profile_when_missing(self):
        status, content_type, body = self.request("GET", "/api/projects/payments/business-context")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(
            json.loads(body),
            {"project_name": "payments", "business_context": "", "source_path": None},
        )

    def test_project_business_context_endpoint_persists_markdown(self):
        status, content_type, body = self.request(
            "PUT",
            "/api/projects/payments/business-context",
            {"business_context": "# Payments\n\nRefunds require audit logs."},
        )
        payload = json.loads(body)
        get_status, _get_type, get_body = self.request("GET", "/api/projects/payments/business-context")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(payload["project_name"], "payments")
        self.assertIn("Refunds require audit logs", payload["business_context"])
        self.assertTrue(payload["source_path"].endswith("payments/business.md"))
        self.assertEqual(get_status, 200)
        self.assertEqual(json.loads(get_body), payload)

    def test_project_business_context_endpoint_sanitizes_project_path_before_persisting(self):
        status, _content_type, body = self.request(
            "PUT",
            "/api/projects/team%2Fpayments/business-context",
            {"business_context": "# Payments\n\nNested project names stay contained."},
        )
        payload = json.loads(body)
        saved_path = Path(payload["source_path"])

        self.assertEqual(status, 200)
        self.assertEqual(payload["project_name"], "team/payments")
        self.assertEqual(saved_path, Path(self.temp_dir.name) / "team-payments" / "business.md")
        self.assertFalse((Path(self.temp_dir.name) / "team" / "payments" / "business.md").exists())


class ExecutingHttpServerTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = FakeAnalyzer()
        self.server = create_server(("127.0.0.1", 0), analyzer=self.analyzer)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        headers = {}
        if body is not None:
            body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        connection.close()
        return response.status, response.getheader("Content-Type"), response_body

    def test_submission_executes_analysis_when_analyzer_is_configured(self):
        status, _content_type, body = self.request(
            "POST",
            "/api/analyses",
            {
                "git_url": "https://github.com/acme/payments.git",
                "branch": "feature/refund-audit",
                "before_commit": "abc123",
                "after_commit": "def456",
                "project_name": "payments",
                "provider_id": "deepseek",
            },
        )
        payload = json.loads(body)
        job_id = payload["job"]["id"]
        detail_status, _detail_type, detail_body = self.request("GET", f"/api/analyses/{job_id}")
        detail_payload = json.loads(detail_body)

        self.assertEqual(status, 200)
        self.assertEqual(payload["job"]["status"], "completed")
        self.assertEqual(payload["job"]["result"]["impact_summary"], "Refund path is impacted.")
        self.assertEqual(payload["job"]["result"]["token_usage"]["prompt_chunks"], 1)
        self.assertEqual(payload["job"]["result"]["token_usage"]["max_output_tokens"], 64)
        self.assertEqual(payload["job"]["logs"][0]["stage"], "queued")
        self.assertEqual(payload["job"]["logs"][-1]["stage"], "completed")
        self.assertEqual(detail_status, 200)
        self.assertEqual(detail_payload["job"]["result"]["test_cases"], ["Cover refund audit failure."])
        self.assertEqual(
            detail_payload["job"]["result"]["structured_review_findings"][0]["standard"],
            "Correctness",
        )
        self.assertEqual(
            detail_payload["job"]["result"]["structured_test_cases"][0]["type"],
            "integration",
        )
        self.assertEqual(
            detail_payload["job"]["result"]["structured_test_cases"][0]["test_id"],
            "TC001",
        )
        self.assertEqual(
            detail_payload["job"]["result"]["structured_test_cases"][0]["affected_business_flows"],
            ["退款审计 (Refund Audit)"],
        )
        self.assertEqual(detail_payload["job"]["result"]["token_usage"]["chunk_input_tokens"], [42])
        self.assertEqual(self.analyzer.requests[0].project_name, "payments")

    def test_failed_submission_records_error_log_detail(self):
        self.server.analyzer = FailingAnalyzer()
        status, _content_type, body = self.request(
            "POST",
            "/api/analyses",
            {
                "git_url": "https://github.com/acme/payments.git",
                "branch": "feature/refund-audit",
                "before_commit": "abc123",
                "after_commit": "def456",
                "project_name": "payments",
                "provider_id": "deepseek",
            },
        )
        payload = json.loads(body)

        self.assertEqual(status, 500)
        self.assertEqual(payload["job"]["status"], "failed")
        self.assertIn("before_commit is not a valid commit", payload["job"]["error"])
        self.assertEqual(payload["job"]["logs"][-1]["stage"], "failed")
        self.assertEqual(payload["job"]["logs"][-1]["level"], "error")
        self.assertIn("abc123", payload["job"]["logs"][-1]["detail"])


class AsyncExecutingHttpServerTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = BlockingFakeAnalyzer()
        self.server = create_server(("127.0.0.1", 0), analyzer=self.analyzer, execute_async=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self):
        self.analyzer.release()
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        headers = {}
        if body is not None:
            body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        connection.close()
        return response.status, response.getheader("Content-Type"), response_body

    def test_async_submission_reports_running_then_completed_with_progress(self):
        status, _content_type, body = self.request(
            "POST",
            "/api/analyses",
            {
                "git_url": "https://github.com/acme/payments.git",
                "branch": "feature/refund-audit",
                "before_commit": "abc123",
                "after_commit": "def456",
                "project_name": "payments",
                "provider_id": "deepseek",
            },
        )
        payload = json.loads(body)
        job_id = payload["job"]["id"]

        self.assertEqual(status, 202)
        self.assertEqual(payload["job"]["status"], "running")
        self.assertEqual(payload["job"]["progress"], ["queued", "running"])
        self.assertEqual([entry["stage"] for entry in payload["job"]["logs"]], ["queued", "running"])

        self.analyzer.release()
        completed = self.wait_for_completed(job_id)

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            completed["progress"],
            ["queued", "running", "changed_functions", "two_hop_call_graph", "ai_request", "completed"],
        )
        self.assertEqual(completed["result"]["impact_summary"], "Refund path is impacted.")
        self.assertEqual(completed["logs"][-1]["stage"], "completed")

    def wait_for_completed(self, job_id):
        for _ in range(30):
            _status, _content_type, body = self.request("GET", f"/api/analyses/{job_id}")
            job = json.loads(body)["job"]
            if job["status"] == "completed":
                return job
            time.sleep(0.05)
        self.fail("analysis job did not complete")


class FakeAnalyzer:
    def __init__(self):
        self.requests = []

    def analyze(self, request):
        self.requests.append(request)
        return ImpactAnalysisResult(
            project_name=request.project_name,
            changed_functions=[],
            call_graph=CallGraph(project_name=request.project_name, depth=2),
            impact_summary="Refund path is impacted.",
            review_findings=["Audit errors need review."],
            test_cases=["Cover refund audit failure."],
            structured_review_findings=[
                {
                    "function": "payments.service.refund",
                    "standard": "Correctness",
                    "severity": "high",
                    "finding": "Audit errors need review.",
                }
            ],
            structured_test_cases=[
                {
                    "test_id": "TC001",
                    "name": "Refund audit failure integration",
                    "business_feature": "退款服务",
                    "business_feature_en": "Refund Service",
                    "business_subfeature": "退款审计",
                    "business_subfeature_en": "Refund Audit",
                    "type": "integration",
                    "target": "api.refunds.post_refund",
                    "verification_goal": "verify audit failure is handled",
                    "affected_business_flows": ["退款审计 (Refund Audit)"],
                    "changed_symbols": ["payments.service.refund"],
                    "affected_files": ["payments/service.py"],
                    "test_tags": ["refund-audit", "regression"],
                }
            ],
            prompt_chunks=1,
            token_usage={
                "max_input_tokens": 4096,
                "max_output_tokens": 64,
                "reserved_output_tokens": 40,
                "prompt_chunks": 1,
                "chunk_input_tokens": [42],
            },
        )


class BlockingFakeAnalyzer(FakeAnalyzer):
    def __init__(self):
        super().__init__()
        self.event = threading.Event()

    def release(self):
        self.event.set()

    def analyze(self, request, progress=None):
        self.event.wait(timeout=2)
        if progress:
            progress("changed_functions")
            progress("two_hop_call_graph")
            progress("ai_request")
        return super().analyze(request)


class FailingAnalyzer:
    def analyze(self, request):
        raise RuntimeError("before_commit is not a valid commit: abc123")


class ModelProbeAnalyzer:
    def __init__(self):
        self.ai_client = FakeProbeAiClient()

    def analyze(self, request):
        raise AssertionError("model probe should not run analysis")


class FakeProbeAiClient:
    def __init__(self):
        self.calls = []

    def complete(self, prompt, provider, max_output_tokens):
        self.calls.append(
            {
                "prompt": prompt,
                "provider_id": provider.id,
                "max_output_tokens": max_output_tokens,
            }
        )
        return {"ok": True, "message": "model is reachable"}


if __name__ == "__main__":
    unittest.main()
