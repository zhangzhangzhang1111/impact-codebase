import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from impact_ai.codebase_memory_cli import CodebaseMemoryCliClient


@unittest.skipUnless(
    os.environ.get("RUN_CODEBASE_MEMORY_CLI_INTEGRATION") == "1",
    "set RUN_CODEBASE_MEMORY_CLI_INTEGRATION=1 to run real codebase-memory-mcp CLI smoke",
)
class CodebaseMemoryCliIntegrationTests(unittest.TestCase):
    def test_real_cli_indexes_temporary_git_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
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

            project_id = CodebaseMemoryCliClient(index_mode="fast").index_repository(repo, "temporary-smoke")

        self.assertTrue(project_id)

    def run_git(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
