import tempfile
import threading
import time
import unittest
from pathlib import Path

from impact_ai.job_store import JsonFileJobStore
from impact_ai.models import ImpactAnalysisRequest


class JsonFileJobStoreTests(unittest.TestCase):
    def test_persists_jobs_across_store_instances(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "history.json"
            request = ImpactAnalysisRequest(
                git_url="https://github.com/acme/payments.git",
                branch="main",
                before_commit="abc123",
                after_commit="def456",
                project_name="payments",
                provider_id="openai",
            )
            store = JsonFileJobStore(history_path)
            job = store.create(request)
            store.start(job.id)
            store.add_progress(job.id, "changed_functions")
            store.complete(job.id, {"impact_summary": "Refunds changed.", "test_cases": ["test refund"]})

            reloaded = JsonFileJobStore(history_path)
            jobs = reloaded.list()
            detail = reloaded.get(job.id)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].id, job.id)
        self.assertEqual(detail.status, "completed")
        self.assertEqual(detail.request.project_name, "payments")
        self.assertEqual(detail.progress, ["queued", "running", "changed_functions", "completed"])
        self.assertEqual([entry["stage"] for entry in detail.logs], ["queued", "running", "changed_functions", "completed"])
        self.assertEqual(detail.logs[-1]["message"], "分析完成")
        self.assertEqual(detail.result["impact_summary"], "Refunds changed.")

    def test_failure_persists_error_log_detail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "history.json"
            request = ImpactAnalysisRequest(
                git_url="https://github.com/acme/payments.git",
                branch="main",
                before_commit="abc123",
                after_commit="def456",
                project_name="payments",
                provider_id="openai",
            )
            store = JsonFileJobStore(history_path)
            job = store.create(request)
            store.start(job.id)
            store.add_progress(job.id, "checkout_repository")
            store.fail(job.id, "before_commit is not a valid commit: abc123")

            reloaded = JsonFileJobStore(history_path)
            detail = reloaded.get(job.id)

        self.assertEqual(detail.status, "failed")
        self.assertEqual(detail.logs[-1]["stage"], "failed")
        self.assertEqual(detail.logs[-1]["level"], "error")
        self.assertIn("abc123", detail.logs[-1]["detail"])

    def test_progress_updates_are_thread_safe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "history.json"
            store = LockObservingProgressStore(history_path)
            request = ImpactAnalysisRequest(
                git_url="https://github.com/acme/payments.git",
                branch="main",
                before_commit="abc123",
                after_commit="def456",
                project_name="payments",
                provider_id="openai",
            )
            job = store.create(request)
            store.start(job.id)
            threads = [
                threading.Thread(target=store.add_progress, args=(job.id, "changed_functions")),
                threading.Thread(target=store.add_progress, args=(job.id, "trace_call_graph")),
            ]

            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

            reloaded = JsonFileJobStore(history_path)
            detail = reloaded.get(job.id)

        self.assertIn("changed_functions", detail.progress)
        self.assertIn("trace_call_graph", detail.progress)
        self.assertIn("trace_call_graph", [entry["stage"] for entry in detail.logs])
        self.assertEqual(detail.progress.count("changed_functions"), 1)
        self.assertEqual(detail.progress.count("trace_call_graph"), 1)
        self.assertEqual(store.max_active_writers, 1)


class LockObservingProgressStore(JsonFileJobStore):
    def __init__(self, path: Path):
        self.active_writers = 0
        self.max_active_writers = 0
        super().__init__(path)

    def add_progress(self, job_id: str, stage: str):
        with self._lock:
            self.active_writers += 1
            self.max_active_writers = max(self.max_active_writers, self.active_writers)
            try:
                time.sleep(0.01)
                return super().add_progress(job_id, stage)
            finally:
                self.active_writers -= 1


if __name__ == "__main__":
    unittest.main()
