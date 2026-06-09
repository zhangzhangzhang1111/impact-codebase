import http.client
import json
import subprocess
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path

from impact_ai.analysis import ImpactAnalyzer
from impact_ai.codebase_memory_graph import CodebaseMemoryKnowledgeGraph
from impact_ai.http_server import create_server
from impact_ai.project_profiles import ProjectProfileLoader
from impact_ai.token_budget import TokenBudget


class EndToEndAnalysisTests(unittest.TestCase):
    def test_http_post_runs_git_diff_graph_ai_and_persists_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_repo = root / "source"
            source_repo.mkdir()
            before_commit, after_commit = self.create_repo_with_refund_change(source_repo)
            profiles = root / "profiles"
            (profiles / "payments").mkdir(parents=True)
            (profiles / "payments" / "business.md").write_text(
                "Refunds require immutable audit logs.",
                encoding="utf-8",
            )
            codebase_client = FakeCodebaseMemoryClient()
            ai_client = CapturingAIClient()
            analyzer = ImpactAnalyzer(
                knowledge_graph=CodebaseMemoryKnowledgeGraph(root / "workspace", codebase_client),
                ai_client=ai_client,
                profile_loader=ProjectProfileLoader(profiles),
                token_budget=TokenBudget(max_input_tokens=4_096, max_output_tokens=512, reserved_output_tokens=256),
            )
            server = create_server(("127.0.0.1", 0), analyzer=analyzer)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, body = self.post_json(
                    server.server_address,
                    "/api/analyses",
                    {
                        "git_url": str(source_repo),
                        "branch": "main",
                        "before_commit": before_commit,
                        "after_commit": after_commit,
                        "project_name": "payments",
                        "provider_id": "openai",
                    },
                )
                payload = json.loads(body)
                job_id = payload["job"]["id"]
                detail_status, detail_body = self.get(server.server_address, f"/api/analyses/{job_id}")
                detail_payload = json.loads(detail_body)
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

        self.assertEqual(status, 200)
        self.assertEqual(detail_status, 200)
        result = detail_payload["job"]["result"]
        self.assertEqual(detail_payload["job"]["status"], "completed")
        self.assertEqual(result["changed_functions"][0]["qualified_name"], "service.refund")
        self.assertEqual(result["call_graph"]["inbound"]["service.refund"], ["api.refunds.post_refund"])
        self.assertEqual(result["call_graph"]["outbound"]["service.refund"], ["audit.write_refund_event"])
        self.assertIn("Refund audit behavior affects API callers.", result["impact_summary"])
        self.assertEqual(result["test_cases"], ["Add API refund audit regression test."])
        self.assertEqual(codebase_client.indexed[0][1], "payments")
        self.assertIn("Refunds require immutable audit logs", ai_client.prompts[0])
        self.assertIn("service.refund", ai_client.prompts[0])

    def create_repo_with_refund_change(self, repo: Path) -> tuple[str, str]:
        self.run_git(repo, "init")
        self.run_git(repo, "checkout", "-b", "main")
        self.run_git(repo, "config", "user.email", "tester@example.com")
        self.run_git(repo, "config", "user.name", "Test User")
        (repo / "service.py").write_text(
            textwrap.dedent(
                """
                def refund(order_id):
                    return {"status": "ok", "order_id": order_id}
                """
            ).lstrip(),
            encoding="utf-8",
        )
        self.run_git(repo, "add", "service.py")
        self.run_git(repo, "commit", "-m", "initial")
        before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

        (repo / "service.py").write_text(
            textwrap.dedent(
                """
                def refund(order_id):
                    write_refund_event(order_id)
                    return {"status": "ok", "order_id": order_id}
                """
            ).lstrip(),
            encoding="utf-8",
        )
        self.run_git(repo, "add", "service.py")
        self.run_git(repo, "commit", "-m", "audit refund")
        after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()
        return before_commit, after_commit

    def run_git(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True)

    def post_json(self, address, path, payload):
        connection = http.client.HTTPConnection(address[0], address[1], timeout=5)
        connection.request(
            "POST",
            path,
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()
        return response.status, body

    def get(self, address, path):
        connection = http.client.HTTPConnection(address[0], address[1], timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()
        return response.status, body


class FakeCodebaseMemoryClient:
    def __init__(self):
        self.indexed = []

    def index_repository(self, repo_path: Path, project_name: str) -> str:
        self.indexed.append((repo_path, project_name))
        return "indexed-payments"

    def trace_two_hop(self, project_id: str, function_name: str, direction: str, depth: int = 2) -> list[str]:
        if direction == "inbound":
            return ["api.refunds.post_refund"]
        return ["audit.write_refund_event"]


class CapturingAIClient:
    def __init__(self):
        self.prompts = []

    def complete(self, prompt, provider, max_output_tokens):
        self.prompts.append(prompt)
        return {
            "impact_summary": "Refund audit behavior affects API callers.",
            "review_findings": ["Check audit write failure handling."],
            "test_cases": ["Add API refund audit regression test."],
        }


if __name__ == "__main__":
    unittest.main()
