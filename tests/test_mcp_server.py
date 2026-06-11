from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, Union
import http.client
import io
import json
import threading
import unittest

from impact_ai.analysis import ImpactAnalysisResult
from impact_ai.http_server import create_server
from impact_ai.knowledge_graph import CallGraph
from impact_ai.mcp_server import handle_mcp_message, run_stdio_loop


class McpHttpServerTests(unittest.TestCase):
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

    def rpc(self, method, params=None, request_id=1):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        body = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            body["params"] = params
        connection.request(
            "POST",
            "/mcp",
            body=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        content_type = response.getheader("Content-Type")
        connection.close()
        return response.status, content_type, payload

    def test_initialize_returns_mcp_capabilities(self):
        status, content_type, payload = self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(payload["jsonrpc"], "2.0")
        self.assertEqual(payload["id"], 1)
        self.assertEqual(payload["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(payload["result"]["serverInfo"]["name"], "impact-codebase")
        self.assertIn("tools", payload["result"]["capabilities"])

    def test_tools_list_exposes_analysis_tools(self):
        status, _content_type, payload = self.rpc("tools/list")

        self.assertEqual(status, 200)
        tools = {tool["name"]: tool for tool in payload["result"]["tools"]}
        self.assertIn("analyze_code_impact", tools)
        self.assertIn("list_analysis_jobs", tools)
        self.assertIn("get_analysis_job", tools)
        self.assertIn("list_ai_providers", tools)
        self.assertEqual(
            tools["analyze_code_impact"]["inputSchema"]["required"],
            ["git_url", "branch", "before_commit", "after_commit", "provider_id"],
        )

    def test_tools_call_analyze_code_impact_runs_analyzer(self):
        status, _content_type, payload = self.rpc(
            "tools/call",
            {
                "name": "analyze_code_impact",
                "arguments": {
                    "git_url": "https://example.test/repo.git",
                    "branch": "main",
                    "before_commit": "abc123",
                    "after_commit": "def456",
                    "project_name": "repo",
                    "provider_id": "deepseek",
                    "call_graph_depth": 3,
                },
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.analyzer.requests[0].project_name, "repo")
        self.assertEqual(self.analyzer.requests[0].call_graph_depth, 3)
        result = payload["result"]
        self.assertFalse(result["isError"])
        self.assertIn("Fake impact summary", result["content"][0]["text"])
        self.assertEqual(result["structuredContent"]["project_name"], "repo")
        self.assertEqual(result["structuredContent"]["call_graph"]["depth"], 3)

    def test_tools_call_returns_json_rpc_error_for_unknown_tool(self):
        status, _content_type, payload = self.rpc(
            "tools/call",
            {"name": "missing_tool", "arguments": {}},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["error"]["code"], -32602)
        self.assertIn("unknown MCP tool", payload["error"]["message"])


class McpStdioServerTests(unittest.TestCase):
    def test_handle_mcp_message_supports_tools_list_without_http(self):
        response = handle_mcp_message(
            {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"},
            analyzer=FakeAnalyzer(),
        )

        self.assertEqual(response["id"], "tools")
        self.assertIn("analyze_code_impact", {tool["name"] for tool in response["result"]["tools"]})

    def test_run_stdio_loop_reads_json_lines_and_writes_json_lines(self):
        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            + "\n"
        )
        stdout = io.StringIO()

        run_stdio_loop(stdin, stdout, analyzer=FakeAnalyzer())

        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([response["id"] for response in responses], [1, 2])
        self.assertIn("serverInfo", responses[0]["result"])
        self.assertIn("tools", responses[1]["result"])

    def test_run_stdio_loop_can_write_content_length_frames(self):
        stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n")
        stdout = io.StringIO()

        run_stdio_loop(stdin, stdout, analyzer=FakeAnalyzer(), use_content_length=True)

        raw = stdout.getvalue()
        header, body = raw.split("\r\n\r\n", 1)
        self.assertTrue(header.startswith("Content-Length: "))
        self.assertEqual(int(header.split(":", 1)[1].strip()), len(body.encode("utf-8")))
        self.assertEqual(json.loads(body)["result"], {})


class FakeAnalyzer:
    def __init__(self):
        self.requests = []

    def analyze(self, request, progress=None):
        self.requests.append(request)
        if progress:
            progress("changed_functions")
            progress("completed")
        return ImpactAnalysisResult(
            project_name=request.project_name,
            changed_functions=[],
            call_graph=CallGraph(
                project_name=request.project_name,
                depth=request.call_graph_depth,
                inbound={},
                outbound={},
            ),
            impact_summary="Fake impact summary",
            review_findings=["Finding"],
            test_cases=["Test case"],
            structured_review_findings=[],
            structured_test_cases=[],
            prompt_chunks=1,
            token_usage={"prompt_chunks": 1},
        )


if __name__ == "__main__":
    unittest.main()
