from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, Union
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from impact_ai.git_diff import GitDiffFunctionExtractor


class GitDiffFunctionExtractorTests(unittest.TestCase):
    def test_extracts_changed_python_function_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "service.py"
            service.write_text(
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

            service.write_text(
                textwrap.dedent(
                    """
                    def refund(order_id):
                        audit_refund(order_id)
                        return {"status": "ok", "order_id": order_id}


                    def capture(order_id):
                        return {"status": "captured", "order_id": order_id}
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "service.py")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "service.refund")
        self.assertEqual(functions[0].language, "python")
        self.assertEqual(functions[0].file_path, "service.py")
        self.assertEqual(functions[0].signature, "def refund(order_id)")
        self.assertEqual(functions[0].change_type, "modified")
        self.assertIn("+    audit_refund(order_id)", functions[0].diff_hunk)

    def test_extracts_deleted_python_function_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "service.py"
            service.write_text(
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

            service.write_text(
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

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "service.refund")
        self.assertEqual(functions[0].language, "python")
        self.assertEqual(functions[0].file_path, "service.py")
        self.assertEqual(functions[0].signature, "def refund(order_id)")
        self.assertEqual(functions[0].change_type, "deleted")
        self.assertIn("-def refund(order_id):", functions[0].diff_hunk)

    def test_extracts_deletion_only_python_function_change_as_modified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "service.py"
            service.write_text(
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
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    def refund(order_id):
                        return {"status": "ok", "order_id": order_id}
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "service.py")
            self.run_git(repo, "commit", "-m", "remove audit")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "service.refund")
        self.assertEqual(functions[0].change_type, "modified")
        self.assertIn("-    audit_refund(order_id)", functions[0].diff_hunk)

    def test_extracts_added_python_function_from_new_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            (repo / "README.md").write_text("# service\n", encoding="utf-8")
            self.run_git(repo, "add", "README.md")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service = repo / "service.py"
            service.write_text(
                textwrap.dedent(
                    """
                    def refund(order_id):
                        return {"status": "ok", "order_id": order_id}
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "service.py")
            self.run_git(repo, "commit", "-m", "add refund service")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "service.refund")
        self.assertEqual(functions[0].file_path, "service.py")
        self.assertEqual(functions[0].change_type, "added")
        self.assertIn("+def refund(order_id):", functions[0].diff_hunk)

    def test_extracts_changed_typescript_function_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "refundService.ts"
            service.write_text(
                textwrap.dedent(
                    """
                    export function refund(orderId: string): RefundResult {
                      return { status: "ok", orderId };
                    }

                    export const capture = (orderId: string): CaptureResult => {
                      return { status: "captured", orderId };
                    };
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "refundService.ts")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    export function refund(orderId: string): RefundResult {
                      auditRefund(orderId);
                      return { status: "ok", orderId };
                    }

                    export const capture = (orderId: string): CaptureResult => {
                      return { status: "captured", orderId };
                    };
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "refundService.ts")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "refundService.refund")
        self.assertEqual(functions[0].language, "typescript")
        self.assertEqual(functions[0].file_path, "refundService.ts")
        self.assertEqual(functions[0].signature, "export function refund(orderId: string): RefundResult")
        self.assertIn("+  auditRefund(orderId);", functions[0].diff_hunk)

    def test_extracts_changed_javascript_arrow_function_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "refundService.js"
            service.write_text(
                textwrap.dedent(
                    """
                    export const refund = (orderId) => {
                      return { status: "ok", orderId };
                    };
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "refundService.js")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    export const refund = (orderId) => {
                      auditRefund(orderId);
                      return { status: "ok", orderId };
                    };
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "refundService.js")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "refundService.refund")
        self.assertEqual(functions[0].language, "javascript")
        self.assertEqual(functions[0].signature, "export const refund = (orderId) =>")

    def test_extracts_changed_java_method_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "RefundService.java"
            service.write_text(
                textwrap.dedent(
                    """
                    public class RefundService {
                        public RefundResult refund(String orderId) {
                            return new RefundResult("ok", orderId);
                        }

                        public CaptureResult capture(String orderId) {
                            return new CaptureResult("captured", orderId);
                        }
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "RefundService.java")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    public class RefundService {
                        public RefundResult refund(String orderId) {
                            auditRefund(orderId);
                            return new RefundResult("ok", orderId);
                        }

                        public CaptureResult capture(String orderId) {
                            return new CaptureResult("captured", orderId);
                        }
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "RefundService.java")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "RefundService.refund")
        self.assertEqual(functions[0].language, "java")
        self.assertEqual(functions[0].file_path, "RefundService.java")
        self.assertEqual(functions[0].signature, "public RefundResult refund(String orderId)")
        self.assertIn("+        auditRefund(orderId);", functions[0].diff_hunk)

    def test_extracts_changed_go_function_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "service.go"
            service.write_text(
                textwrap.dedent(
                    """
                    package service

                    func Refund(orderID string) RefundResult {
                        return RefundResult{Status: "ok", OrderID: orderID}
                    }

                    func Capture(orderID string) CaptureResult {
                        return CaptureResult{Status: "captured", OrderID: orderID}
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "service.go")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    package service

                    func Refund(orderID string) RefundResult {
                        auditRefund(orderID)
                        return RefundResult{Status: "ok", OrderID: orderID}
                    }

                    func Capture(orderID string) CaptureResult {
                        return CaptureResult{Status: "captured", OrderID: orderID}
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "service.go")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "service.Refund")
        self.assertEqual(functions[0].language, "go")
        self.assertEqual(functions[0].file_path, "service.go")
        self.assertEqual(functions[0].signature, "func Refund(orderID string) RefundResult")
        self.assertIn("+    auditRefund(orderID)", functions[0].diff_hunk)

    def test_extracts_changed_rust_function_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "service.rs"
            service.write_text(
                textwrap.dedent(
                    """
                    pub fn refund(order_id: &str) -> RefundResult {
                        RefundResult::ok(order_id)
                    }

                    pub fn capture(order_id: &str) -> CaptureResult {
                        CaptureResult::captured(order_id)
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "service.rs")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    pub fn refund(order_id: &str) -> RefundResult {
                        audit_refund(order_id);
                        RefundResult::ok(order_id)
                    }

                    pub fn capture(order_id: &str) -> CaptureResult {
                        CaptureResult::captured(order_id)
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "service.rs")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "service.refund")
        self.assertEqual(functions[0].language, "rust")
        self.assertEqual(functions[0].file_path, "service.rs")
        self.assertEqual(functions[0].signature, "pub fn refund(order_id: &str) -> RefundResult")
        self.assertIn("+    audit_refund(order_id);", functions[0].diff_hunk)

    def test_extracts_changed_php_method_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "RefundService.php"
            service.write_text(
                textwrap.dedent(
                    """
                    <?php

                    class RefundService {
                        public function refund(string $orderId): RefundResult {
                            return RefundResult::ok($orderId);
                        }

                        public function capture(string $orderId): CaptureResult {
                            return CaptureResult::captured($orderId);
                        }
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "RefundService.php")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    <?php

                    class RefundService {
                        public function refund(string $orderId): RefundResult {
                            auditRefund($orderId);
                            return RefundResult::ok($orderId);
                        }

                        public function capture(string $orderId): CaptureResult {
                            return CaptureResult::captured($orderId);
                        }
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "RefundService.php")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "RefundService.refund")
        self.assertEqual(functions[0].language, "php")
        self.assertEqual(functions[0].file_path, "RefundService.php")
        self.assertEqual(functions[0].signature, "public function refund(string $orderId): RefundResult")
        self.assertIn("+        auditRefund($orderId);", functions[0].diff_hunk)

    def test_extracts_changed_csharp_method_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "RefundService.cs"
            service.write_text(
                textwrap.dedent(
                    """
                    public class RefundService
                    {
                        public RefundResult Refund(string orderId)
                        {
                            return RefundResult.Ok(orderId);
                        }

                        public CaptureResult Capture(string orderId)
                        {
                            return CaptureResult.Captured(orderId);
                        }
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "RefundService.cs")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    public class RefundService
                    {
                        public RefundResult Refund(string orderId)
                        {
                            AuditRefund(orderId);
                            return RefundResult.Ok(orderId);
                        }

                        public CaptureResult Capture(string orderId)
                        {
                            return CaptureResult.Captured(orderId);
                        }
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "RefundService.cs")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "RefundService.Refund")
        self.assertEqual(functions[0].language, "csharp")
        self.assertEqual(functions[0].file_path, "RefundService.cs")
        self.assertEqual(functions[0].signature, "public RefundResult Refund(string orderId)")
        self.assertIn("+        AuditRefund(orderId);", functions[0].diff_hunk)

    def test_extracts_changed_kotlin_function_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "RefundService.kt"
            service.write_text(
                textwrap.dedent(
                    """
                    fun refund(orderId: String): RefundResult {
                        return RefundResult.ok(orderId)
                    }

                    fun capture(orderId: String): CaptureResult {
                        return CaptureResult.captured(orderId)
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "RefundService.kt")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    fun refund(orderId: String): RefundResult {
                        auditRefund(orderId)
                        return RefundResult.ok(orderId)
                    }

                    fun capture(orderId: String): CaptureResult {
                        return CaptureResult.captured(orderId)
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "RefundService.kt")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "RefundService.refund")
        self.assertEqual(functions[0].language, "kotlin")
        self.assertEqual(functions[0].file_path, "RefundService.kt")
        self.assertEqual(functions[0].signature, "fun refund(orderId: String): RefundResult")
        self.assertIn("+    auditRefund(orderId)", functions[0].diff_hunk)

    def test_extracts_changed_cpp_method_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "refund_service.cpp"
            service.write_text(
                textwrap.dedent(
                    """
                    RefundResult RefundService::refund(const std::string& orderId) {
                        return RefundResult::ok(orderId);
                    }

                    CaptureResult RefundService::capture(const std::string& orderId) {
                        return CaptureResult::captured(orderId);
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "refund_service.cpp")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    RefundResult RefundService::refund(const std::string& orderId) {
                        auditRefund(orderId);
                        return RefundResult::ok(orderId);
                    }

                    CaptureResult RefundService::capture(const std::string& orderId) {
                        return CaptureResult::captured(orderId);
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "refund_service.cpp")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "RefundService.refund")
        self.assertEqual(functions[0].language, "cpp")
        self.assertEqual(functions[0].file_path, "refund_service.cpp")
        self.assertEqual(functions[0].signature, "RefundResult RefundService::refund(const std::string& orderId)")
        self.assertIn("+    auditRefund(orderId);", functions[0].diff_hunk)

    def test_extracts_changed_c_function_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "lapi.c"
            service.write_text(
                textwrap.dedent(
                    """
                    LUA_API void lua_pushnil (lua_State *L) {
                      lua_lock(L);
                      setnilvalue(s2v(L->top.p));
                      api_incr_top(L);
                      lua_unlock(L);
                    }

                    static int aux_capture (lua_State *L) {
                      return 1;
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "lapi.c")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    LUA_API void lua_pushnil (lua_State *L) {
                      lua_lock(L);
                      setnilvalue(s2v(L->top.p));
                      api_check(L, L->top.p <= L->ci->top.p, "stack overflow");
                      api_incr_top(L);
                      lua_unlock(L);
                    }

                    static int aux_capture (lua_State *L) {
                      return 1;
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "lapi.c")
            self.run_git(repo, "commit", "-m", "check push nil")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "lapi.lua_pushnil")
        self.assertEqual(functions[0].language, "c")
        self.assertEqual(functions[0].file_path, "lapi.c")
        self.assertEqual(functions[0].signature, "LUA_API void lua_pushnil (lua_State *L)")
        self.assertIn("+  api_check(L, L->top.p <= L->ci->top.p, \"stack overflow\");", functions[0].diff_hunk)

    def test_extracts_changed_lua_module_function_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "rate_limiting.lua"
            service.write_text(
                textwrap.dedent(
                    """
                    local _M = {}

                    local function _has_rl_ctx(ngx_ctx)
                      return ngx_ctx.__rate_limiting_context__ ~= nil
                    end

                    function _M.get_stored_response_header(ngx_ctx, key)
                      if not _has_rl_ctx(ngx_ctx) then
                        return nil
                      end

                      if not _has_rl_ctx(ngx_ctx) then
                        return nil
                      end

                      return ngx_ctx.__rate_limiting_context__[key]
                    end

                    return _M
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "rate_limiting.lua")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    local _M = {}

                    local function _has_rl_ctx(ngx_ctx)
                      return ngx_ctx.__rate_limiting_context__ ~= nil
                    end

                    function _M.get_stored_response_header(ngx_ctx, key)
                      if not _has_rl_ctx(ngx_ctx) then
                        return nil
                      end

                      return ngx_ctx.__rate_limiting_context__[key]
                    end

                    return _M
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "rate_limiting.lua")
            self.run_git(repo, "commit", "-m", "remove duplicate check")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "rate_limiting.get_stored_response_header")
        self.assertEqual(functions[0].language, "lua")
        self.assertEqual(functions[0].file_path, "rate_limiting.lua")
        self.assertEqual(functions[0].signature, "function _M.get_stored_response_header(ngx_ctx, key)")
        self.assertEqual(functions[0].change_type, "modified")
        self.assertIn("-  if not _has_rl_ctx(ngx_ctx) then", functions[0].diff_hunk)

    def test_extracts_changed_ruby_method_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "refund_service.rb"
            service.write_text(
                textwrap.dedent(
                    """
                    class RefundService
                      def refund(order_id)
                        RefundResult.ok(order_id)
                      end

                      def capture(order_id)
                        CaptureResult.captured(order_id)
                      end
                    end
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "refund_service.rb")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    class RefundService
                      def refund(order_id)
                        audit_refund(order_id)
                        RefundResult.ok(order_id)
                      end

                      def capture(order_id)
                        CaptureResult.captured(order_id)
                      end
                    end
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "refund_service.rb")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "RefundService.refund")
        self.assertEqual(functions[0].language, "ruby")
        self.assertEqual(functions[0].file_path, "refund_service.rb")
        self.assertEqual(functions[0].signature, "def refund(order_id)")
        self.assertIn("+    audit_refund(order_id)", functions[0].diff_hunk)

    def test_extracts_changed_swift_function_from_commit_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "tester@example.com")
            self.run_git(repo, "config", "user.name", "Test User")
            service = repo / "RefundService.swift"
            service.write_text(
                textwrap.dedent(
                    """
                    func refund(orderId: String) -> RefundResult {
                        return RefundResult.ok(orderId)
                    }

                    func capture(orderId: String) -> CaptureResult {
                        return CaptureResult.captured(orderId)
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "RefundService.swift")
            self.run_git(repo, "commit", "-m", "initial")
            before_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            service.write_text(
                textwrap.dedent(
                    """
                    func refund(orderId: String) -> RefundResult {
                        auditRefund(orderId)
                        return RefundResult.ok(orderId)
                    }

                    func capture(orderId: String) -> CaptureResult {
                        return CaptureResult.captured(orderId)
                    }
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self.run_git(repo, "add", "RefundService.swift")
            self.run_git(repo, "commit", "-m", "audit refunds")
            after_commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()

            functions = GitDiffFunctionExtractor(repo).extract_changed_functions(before_commit, after_commit)

        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0].qualified_name, "RefundService.refund")
        self.assertEqual(functions[0].language, "swift")
        self.assertEqual(functions[0].file_path, "RefundService.swift")
        self.assertEqual(functions[0].signature, "func refund(orderId: String) -> RefundResult")
        self.assertIn("+    auditRefund(orderId)", functions[0].diff_hunk)

    def run_git(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


if __name__ == "__main__":
    unittest.main()
