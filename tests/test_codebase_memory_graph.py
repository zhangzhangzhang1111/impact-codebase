import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from impact_ai.codebase_memory_graph import CodebaseMemoryKnowledgeGraph
from impact_ai.knowledge_graph import ChangedFunction
from impact_ai.models import ImpactAnalysisRequest


class FakeCodebaseMemoryClient:
    def __init__(self):
        self.indexed_paths = []
        self.indexed_heads = []
        self.trace_calls = []

    def index_repository(self, repo_path: Path, project_name: str) -> str:
        self.indexed_paths.append((repo_path, project_name))
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        self.indexed_heads.append(head)
        if project_name.endswith("@before"):
            return f"indexed-{project_name.removesuffix('@before')}-before"
        return f"indexed-{project_name}"

    def trace_two_hop(self, project_id: str, function_name: str, direction: str, depth: int = 2) -> list[str]:
        self.trace_calls.append((project_id, function_name, direction, depth))
        if direction == "inbound":
            return ["api.refunds.post_refund", "jobs.refunds.retry_refund"]
        return ["payments.audit.audit_refund"]


class MissingFunctionTraceClient(FakeCodebaseMemoryClient):
    def trace_two_hop(self, project_id: str, function_name: str, direction: str, depth: int = 2) -> list[str]:
        self.trace_calls.append((project_id, function_name, direction, depth))
        if function_name == "lapi.lua_settop":
            raise RuntimeError('trace_path failed: {"error":"function not found","function_name":"lapi.lua_settop"}')
        return super().trace_two_hop(project_id, function_name, direction, depth)


class FailingIndexClient(FakeCodebaseMemoryClient):
    def index_repository(self, repo_path: Path, project_name: str) -> str:
        raise RuntimeError("index_repository failed: pipeline.err phase=dump")


class EmptyTraceClient(FakeCodebaseMemoryClient):
    def trace_two_hop(self, project_id: str, function_name: str, direction: str, depth: int = 2) -> list[str]:
        self.trace_calls.append((project_id, function_name, direction, depth))
        return []


class CodebaseMemoryKnowledgeGraphTests(unittest.TestCase):
    def test_clone_destination_is_absolute_when_workspace_root_is_relative(self):
        graph = CodebaseMemoryKnowledgeGraph(Path(".impact-ai/repos"), FakeCodebaseMemoryClient())
        calls = []

        def fake_git(cwd, *args):
            calls.append((cwd, args))
            if args[0] == "clone":
                return ""
            if args[:3] == ("rev-parse", "--verify", "origin/main^{commit}"):
                return "origin/main\n"
            if args[0] in {"fetch", "checkout"}:
                return ""
            return ""

        graph._git = fake_git
        graph._git_ok = lambda cwd, *args: True

        graph._checkout(
            ImpactAnalysisRequest(
                git_url="https://github.com/LuaLS/lua-language-server.git",
                branch="main",
                before_commit="abc123",
                after_commit="def456",
                project_name="relative-workspace-clone-test",
                provider_id="openai",
            )
        )

        clone_call = next(args for _cwd, args in calls if args[0] == "clone")
        self.assertEqual(Path(clone_call[2]), Path(".impact-ai/repos/relative-workspace-clone-test").resolve())

    def test_clones_indexes_extracts_changed_functions_and_fetches_two_hop_graph(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_repo = root / "source"
            source_repo.mkdir()
            before_commit, after_commit = self.create_repo_with_python_change(source_repo)
            workspace = root / "workspace"
            client = FakeCodebaseMemoryClient()
            graph = CodebaseMemoryKnowledgeGraph(workspace, client)
            request = ImpactAnalysisRequest(
                git_url=str(source_repo),
                branch="main",
                before_commit=before_commit,
                after_commit=after_commit,
                project_name="payments",
                provider_id="openai",
            )

            progress = []
            diff = graph.changed_functions(request, progress.append)
            call_graph = graph.two_hop_call_graph(request.project_name, diff.changed_functions, 3, progress.append)

            self.assertEqual(diff.project_name, "payments")
            self.assertEqual(diff.changed_functions[0].qualified_name, "service.refund")
            self.assertEqual(client.indexed_paths[0][1], "payments")
            self.assertTrue((client.indexed_paths[0][0] / ".git").exists())
            self.assertEqual(call_graph.depth, 3)
            self.assertEqual(call_graph.inbound["service.refund"], ["api.refunds.post_refund", "jobs.refunds.retry_refund"])
            self.assertEqual(call_graph.outbound["service.refund"], ["payments.audit.audit_refund"])
            self.assertEqual(call_graph.trace_status["service.refund"], "success")
            self.assertEqual(call_graph.trace_errors, {})
            self.assertIn(("indexed-payments", "service.refund", "inbound", 3), client.trace_calls)
            self.assertIn(("indexed-payments", "service.refund", "outbound", 3), client.trace_calls)
            self.assertEqual(
                progress,
                [
                    "checkout_repository",
                    "index_repository",
                    "extract_changed_functions",
                    "trace_call_graph",
                ],
            )

    def test_indexes_exact_after_commit_even_when_branch_has_newer_commits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_repo = root / "source"
            source_repo.mkdir()
            before_commit, after_commit, branch_tip = self.create_repo_with_newer_branch_tip(source_repo)
            workspace = root / "workspace"
            client = FakeCodebaseMemoryClient()
            graph = CodebaseMemoryKnowledgeGraph(workspace, client)
            request = ImpactAnalysisRequest(
                git_url=str(source_repo),
                branch="feature/refund-audit",
                before_commit=before_commit,
                after_commit=after_commit,
                project_name="payments",
                provider_id="openai",
            )

            diff = graph.changed_functions(request)

            self.assertNotEqual(after_commit, branch_tip)
            self.assertEqual(client.indexed_heads, [after_commit])
            self.assertEqual(diff.changed_functions[0].qualified_name, "service.refund")

    def test_reuses_project_directory_with_updated_git_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_repo = root / "first-source"
            second_repo = root / "second-source"
            first_repo.mkdir()
            second_repo.mkdir()
            first_before, first_after = self.create_repo_with_named_python_change(first_repo, "refund")
            second_before, second_after = self.create_repo_with_named_python_change(second_repo, "charge")
            workspace = root / "workspace"
            client = FakeCodebaseMemoryClient()
            graph = CodebaseMemoryKnowledgeGraph(workspace, client)

            graph.changed_functions(
                ImpactAnalysisRequest(
                    git_url=str(first_repo),
                    branch="main",
                    before_commit=first_before,
                    after_commit=first_after,
                    project_name="payments",
                    provider_id="openai",
                )
            )
            diff = graph.changed_functions(
                ImpactAnalysisRequest(
                    git_url=str(second_repo),
                    branch="main",
                    before_commit=second_before,
                    after_commit=second_after,
                    project_name="payments",
                    provider_id="openai",
                )
            )

            origin_url = self.run_git(client.indexed_paths[-1][0], "config", "--get", "remote.origin.url").stdout.strip()
            self.assertEqual(origin_url, str(second_repo))
            self.assertEqual(client.indexed_heads[-1], second_after)
            self.assertEqual(diff.changed_functions[0].qualified_name, "service.charge")

    def test_traces_deleted_functions_against_before_commit_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_repo = root / "source"
            source_repo.mkdir()
            before_commit, after_commit = self.create_repo_with_deleted_python_function(source_repo)
            workspace = root / "workspace"
            client = FakeCodebaseMemoryClient()
            graph = CodebaseMemoryKnowledgeGraph(workspace, client)
            request = ImpactAnalysisRequest(
                git_url=str(source_repo),
                branch="main",
                before_commit=before_commit,
                after_commit=after_commit,
                project_name="payments",
                provider_id="openai",
            )

            diff = graph.changed_functions(request)
            graph.two_hop_call_graph(request.project_name, diff.changed_functions)

            self.assertEqual(diff.changed_functions[0].qualified_name, "service.refund")
            self.assertEqual(diff.changed_functions[0].change_type, "deleted")
            self.assertEqual(client.indexed_heads, [after_commit, before_commit])
            self.assertIn(("indexed-payments-before", "service.refund", "inbound", 2), client.trace_calls)
            self.assertIn(("indexed-payments-before", "service.refund", "outbound", 2), client.trace_calls)

    def test_missing_trace_function_does_not_fail_entire_call_graph(self):
        client = MissingFunctionTraceClient()
        graph = CodebaseMemoryKnowledgeGraph(Path("unused"), client)
        graph._project_ids["lua"] = "indexed-lua"
        functions = [
            ChangedFunction(
                qualified_name="lapi.lua_settop",
                language="c",
                file_path="lapi.c",
                signature="LUA_API void lua_settop (lua_State *L, int idx)",
                diff_hunk="@@ -1 +1 @@",
            ),
            ChangedFunction(
                qualified_name="service.refund",
                language="python",
                file_path="service.py",
                signature="def refund(order_id)",
                diff_hunk="@@ -1 +1 @@",
            ),
        ]

        call_graph = graph.two_hop_call_graph("lua", functions)

        self.assertEqual(call_graph.inbound["lapi.lua_settop"], [])
        self.assertEqual(call_graph.outbound["lapi.lua_settop"], [])
        self.assertEqual(call_graph.trace_status["lapi.lua_settop"], "not_found")
        self.assertIn("function not found", call_graph.trace_errors["lapi.lua_settop"])
        self.assertEqual(call_graph.inbound["service.refund"], ["api.refunds.post_refund", "jobs.refunds.retry_refund"])
        self.assertEqual(call_graph.outbound["service.refund"], ["payments.audit.audit_refund"])
        self.assertEqual(call_graph.trace_status["service.refund"], "success")

    def test_index_failure_uses_source_fallback_for_lua_call_graph(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_repo = root / "source"
            source_repo.mkdir()
            self.run_git(source_repo, "init")
            self.run_git(source_repo, "checkout", "-b", "main")
            self.run_git(source_repo, "config", "user.email", "tester@example.com")
            self.run_git(source_repo, "config", "user.name", "Test User")
            module = source_repo / "kong" / "pdk" / "private"
            module.mkdir(parents=True)
            (module / "rate_limiting.lua").write_text(
                textwrap.dedent(
                    """
                    local _M = {}
                    local function _validate_key(key) return key end
                    local function _has_rl_ctx(ctx) return ctx.__rate_limiting_context__ ~= nil end
                    local function _get_rl_ctx(ctx) return ctx.__rate_limiting_context__ end
                    function _M.get_stored_response_header(ngx_ctx, key)
                      _validate_key(key)
                      if not _has_rl_ctx(ngx_ctx) then
                        return nil
                      end
                      local rl_ctx = _get_rl_ctx(ngx_ctx)
                      return rl_ctx[key]
                    end
                    return _M
                    """
                ).strip()
            )
            test_dir = source_repo / "t"
            test_dir.mkdir()
            (test_dir / "01-pdk.t").write_text(
                textwrap.dedent(
                    """
                    local pdk_rl = require "kong.pdk.private.rate_limiting"
                    local value = pdk_rl.get_stored_response_header(ngx.ctx, "X-1")
                    """
                ).strip()
            )
            self.run_git(source_repo, "add", ".")
            self.run_git(source_repo, "commit", "-m", "before")
            before_commit = self.run_git(source_repo, "rev-parse", "HEAD").stdout.strip()
            (module / "rate_limiting.lua").write_text(
                (module / "rate_limiting.lua").read_text().replace(
                    "local rl_ctx = _get_rl_ctx(ngx_ctx)",
                    "-- removed duplicated guard\n                      local rl_ctx = _get_rl_ctx(ngx_ctx)",
                )
            )
            self.run_git(source_repo, "add", ".")
            self.run_git(source_repo, "commit", "-m", "after")
            after_commit = self.run_git(source_repo, "rev-parse", "HEAD").stdout.strip()

            graph = CodebaseMemoryKnowledgeGraph(root / "workspace", FailingIndexClient())
            request = ImpactAnalysisRequest(
                git_url=str(source_repo),
                branch="main",
                before_commit=before_commit,
                after_commit=after_commit,
                project_name="kong",
                provider_id="openai",
            )

            diff = graph.changed_functions(request)
            call_graph = graph.two_hop_call_graph(request.project_name, diff.changed_functions)

            self.assertEqual(diff.changed_functions[0].qualified_name, "rate_limiting.get_stored_response_header")
            self.assertEqual(call_graph.trace_status["rate_limiting.get_stored_response_header"], "fallback_success")
            self.assertIn("t/01-pdk.t:2", call_graph.inbound["rate_limiting.get_stored_response_header"])
            self.assertIn("_validate_key", call_graph.outbound["rate_limiting.get_stored_response_header"])
            self.assertIn("phase=dump", call_graph.trace_errors["rate_limiting.get_stored_response_header"])

    def test_successful_empty_trace_is_augmented_from_lua_source_aliases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            module = repo / "kong" / "pdk" / "private"
            module.mkdir(parents=True)
            (module / "rate_limiting.lua").write_text(
                textwrap.dedent(
                    """
                    local _M = {}
                    local function _validate_key(key) return key end
                    function _M.get_stored_response_header(ngx_ctx, key)
                      _validate_key(key)
                      return ngx_ctx[key]
                    end
                    return _M
                    """
                ).strip()
            )
            test_dir = repo / "t"
            test_dir.mkdir()
            (test_dir / "01-pdk.t").write_text('local pdk_rl = require "kong.pdk.private.rate_limiting"\npdk_rl.get_stored_response_header(ngx.ctx, "X-1")\n')
            client = EmptyTraceClient()
            graph = CodebaseMemoryKnowledgeGraph(Path("unused"), client)
            graph._repo_paths["kong"] = repo
            graph._project_ids["kong"] = "indexed-kong"
            function = ChangedFunction(
                qualified_name="rate_limiting.get_stored_response_header",
                language="lua",
                file_path="kong/pdk/private/rate_limiting.lua",
                signature="function _M.get_stored_response_header(ngx_ctx, key)",
                diff_hunk="@@ -1 +1 @@",
            )

            call_graph = graph.two_hop_call_graph("kong", [function])

            self.assertEqual(call_graph.trace_status["rate_limiting.get_stored_response_header"], "augmented_success")
            self.assertEqual(call_graph.inbound["rate_limiting.get_stored_response_header"], ["t/01-pdk.t:2"])
            self.assertIn("_validate_key", call_graph.outbound["rate_limiting.get_stored_response_header"])

    def create_repo_with_python_change(self, repo: Path) -> tuple[str, str]:
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
                    audit_refund(order_id)
                    return {"status": "ok", "order_id": order_id}
                """
            ).lstrip(),
            encoding="utf-8",
        )
        self.run_git(repo, "add", "service.py")
        self.run_git(repo, "commit", "-m", "audit refunds")
        after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()
        return before_commit, after_commit

    def create_repo_with_named_python_change(self, repo: Path, function_name: str) -> tuple[str, str]:
        self.run_git(repo, "init")
        self.run_git(repo, "checkout", "-b", "main")
        self.run_git(repo, "config", "user.email", "tester@example.com")
        self.run_git(repo, "config", "user.name", "Test User")
        (repo / "service.py").write_text(
            textwrap.dedent(
                f"""
                def {function_name}(order_id):
                    return {{"status": "ok", "order_id": order_id}}
                """
            ).lstrip(),
            encoding="utf-8",
        )
        self.run_git(repo, "add", "service.py")
        self.run_git(repo, "commit", "-m", "initial")
        before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

        (repo / "service.py").write_text(
            textwrap.dedent(
                f"""
                def {function_name}(order_id):
                    audit_event(order_id)
                    return {{"status": "ok", "order_id": order_id}}
                """
            ).lstrip(),
            encoding="utf-8",
        )
        self.run_git(repo, "add", "service.py")
        self.run_git(repo, "commit", "-m", f"audit {function_name}")
        after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()
        return before_commit, after_commit

    def create_repo_with_newer_branch_tip(self, repo: Path) -> tuple[str, str, str]:
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

        self.run_git(repo, "checkout", "-b", "feature/refund-audit")
        (repo / "service.py").write_text(
            textwrap.dedent(
                """
                def refund(order_id):
                    audit_refund(order_id)
                    return {"status": "ok", "order_id": order_id}
                """
            ).lstrip(),
            encoding="utf-8",
        )
        self.run_git(repo, "add", "service.py")
        self.run_git(repo, "commit", "-m", "audit refunds")
        after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

        (repo / "unrelated.py").write_text("def untouched():\n    return True\n", encoding="utf-8")
        self.run_git(repo, "add", "unrelated.py")
        self.run_git(repo, "commit", "-m", "newer unrelated work")
        branch_tip = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()
        return before_commit, after_commit, branch_tip

    def create_repo_with_deleted_python_function(self, repo: Path) -> tuple[str, str]:
        self.run_git(repo, "init")
        self.run_git(repo, "checkout", "-b", "main")
        self.run_git(repo, "config", "user.email", "tester@example.com")
        self.run_git(repo, "config", "user.name", "Test User")
        (repo / "service.py").write_text(
            textwrap.dedent(
                """
                def refund(order_id):
                    return {"status": "ok", "order_id": order_id}


                def capture(order_id):
                    return {"status": "captured", "order_id": order_id}
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
                def capture(order_id):
                    return {"status": "captured", "order_id": order_id}
                """
            ).lstrip(),
            encoding="utf-8",
        )
        self.run_git(repo, "add", "service.py")
        self.run_git(repo, "commit", "-m", "remove refund")
        after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()
        return before_commit, after_commit

    def run_git(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
