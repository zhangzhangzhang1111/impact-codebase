from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, Union
import json
import subprocess
import unittest
from pathlib import Path

from impact_ai.codebase_memory_cli import CodebaseMemoryCliClient, CodebaseMemoryCliError


class CodebaseMemoryCliClientTests(unittest.TestCase):
    def test_index_repository_invokes_cli_and_returns_project_name(self):
        runner = FakeRunner(
            {
                "index_repository": 'level=info msg=mem.init\n{"project":"indexed-payments","status":"indexed"}',
            }
        )
        client = CodebaseMemoryCliClient(binary="codebase-memory-mcp", runner=runner.run)

        project_id = client.index_repository(Path("/tmp/payments"), "payments")

        self.assertEqual(project_id, "indexed-payments")
        self.assertEqual(runner.calls[0][0], ["codebase-memory-mcp", "cli", "index_repository"])
        self.assertEqual(json.loads(runner.calls[0][1])["repo_path"], str(Path("/tmp/payments").resolve()))
        self.assertEqual(json.loads(runner.calls[0][1])["mode"], "fast")
        self.assertEqual(json.loads(runner.calls[0][1])["project_name"], "payments")

    def test_index_mode_is_configurable(self):
        runner = FakeRunner(
            {
                "index_repository": '{"project":"indexed-payments","status":"indexed"}',
            }
        )
        client = CodebaseMemoryCliClient(binary="codebase-memory-mcp", runner=runner.run, index_mode="full")

        client.index_repository(Path("/tmp/payments"), "payments")

        self.assertEqual(json.loads(runner.calls[0][1])["mode"], "full")

    def test_trace_two_hop_invokes_trace_path_and_normalizes_multiple_response_shapes(self):
        runner = FakeRunner(
            {
                "trace_path": json.dumps(
                    {
                        "paths": [
                            {"nodes": [{"name": "api.refunds.post_refund"}, {"name": "service.refund"}]},
                            {"path": ["jobs.refunds.retry_refund", "service.refund"]},
                        ]
                    }
                )
            }
        )
        client = CodebaseMemoryCliClient(binary="codebase-memory-mcp", runner=runner.run)

        callers = client.trace_two_hop("indexed-payments", "service.refund", "inbound", depth=4)

        self.assertEqual(callers, ["api.refunds.post_refund", "jobs.refunds.retry_refund"])
        self.assertEqual(runner.calls[0][0], ["codebase-memory-mcp", "cli", "trace_path"])
        payload = json.loads(runner.calls[0][1])
        self.assertEqual(payload["project"], "indexed-payments")
        self.assertEqual(payload["function_name"], "service.refund")
        self.assertEqual(payload["direction"], "inbound")
        self.assertEqual(payload["depth"], 4)

    def test_trace_two_hop_resolves_exact_function_name_when_trace_reports_not_found(self):
        runner = SequencedRunner(
            [
                (
                    "trace_path",
                    1,
                    'level=info msg=mem.init\n{"error":"function not found","function_name":"rate_limiting.get_stored_response_header"}',
                ),
                (
                    "search_graph",
                    0,
                    json.dumps(
                        {
                            "results": [
                                {
                                    "name": "_M.get_stored_response_header",
                                    "qualified_name": "project.rate_limiting._M.get_stored_response_header",
                                }
                            ]
                        }
                    ),
                ),
                (
                    "trace_path",
                    0,
                    json.dumps({"callees": [{"qualified_name": "_validate_key"}, {"qualified_name": "_has_rl_ctx"}]}),
                ),
            ]
        )
        client = CodebaseMemoryCliClient(binary="codebase-memory-mcp", runner=runner.run)

        callees = client.trace_two_hop("indexed-kong", "rate_limiting.get_stored_response_header", "outbound")

        self.assertEqual(callees, ["_validate_key", "_has_rl_ctx"])
        self.assertEqual(json.loads(runner.calls[1][1])["name_pattern"], ".*get_stored_response_header.*")
        self.assertEqual(json.loads(runner.calls[2][1])["function_name"], "project.rate_limiting._M.get_stored_response_header")

    def test_failed_cli_call_reports_json_error_details_from_stdout(self):
        runner = FakeRunner(
            {
                "index_repository": 'level=info msg=mem.init\n{"error":"project not found","hint":"Use list_projects"}',
            },
            returncode=1,
        )
        client = CodebaseMemoryCliClient(binary="codebase-memory-mcp", runner=runner.run)

        with self.assertRaises(CodebaseMemoryCliError) as context:
            client.index_repository(Path("/tmp/payments"), "payments")

        message = str(context.exception)
        self.assertIn("index_repository failed", message)
        self.assertIn("project not found", message)
        self.assertIn("Use list_projects", message)


class FakeRunner:
    def __init__(self, outputs, returncode=0):
        self.outputs = outputs
        self.returncode = returncode
        self.calls = []

    def run(self, args, payload):
        self.calls.append((args, payload))
        tool = args[2]
        return subprocess.CompletedProcess(args=args, returncode=self.returncode, stdout=self.outputs[tool], stderr="")


class SequencedRunner:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def run(self, args, payload):
        self.calls.append((args, payload))
        expected_tool, returncode, stdout = self.outputs[len(self.calls) - 1]
        self.assert_tool(args, expected_tool)
        return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr="")

    def assert_tool(self, args, expected_tool):
        if args[2] != expected_tool:
            raise AssertionError(f"expected {expected_tool}, got {args[2]}")


if __name__ == "__main__":
    unittest.main()
