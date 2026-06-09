import re
import subprocess
from pathlib import Path
from typing import Callable, Protocol

from impact_ai.git_diff import GitDiffFunctionExtractor
from impact_ai.knowledge_graph import CallGraph, ChangedFunction, DiffAnalysis
from impact_ai.models import ImpactAnalysisRequest


class CodebaseMemoryClient(Protocol):
    def index_repository(self, repo_path: Path, project_name: str) -> str:
        raise NotImplementedError

    def trace_two_hop(self, project_id: str, function_name: str, direction: str, depth: int = 2) -> list[str]:
        raise NotImplementedError


class CodebaseMemoryKnowledgeGraph:
    def __init__(self, workspace_root: Path, client: CodebaseMemoryClient):
        self.workspace_root = workspace_root
        self.client = client
        self._repo_paths: dict[str, Path] = {}
        self._project_ids: dict[str, str] = {}
        self._before_project_ids: dict[str, str] = {}
        self._index_errors: dict[str, str] = {}

    def changed_functions(
        self,
        request: ImpactAnalysisRequest,
        progress: Callable[[str], None] | None = None,
    ) -> DiffAnalysis:
        _report(progress, "checkout_repository")
        repo_path = self._checkout(request)
        _report(progress, "index_repository")
        self._repo_paths[request.project_name] = repo_path
        try:
            project_id = self.client.index_repository(repo_path, request.project_name)
            self._project_ids[request.project_name] = project_id
            self._index_errors.pop(request.project_name, None)
        except Exception as error:
            self._project_ids.pop(request.project_name, None)
            self._index_errors[request.project_name] = str(error)
        _report(progress, "extract_changed_functions")
        functions = GitDiffFunctionExtractor(repo_path).extract_changed_functions(
            request.before_commit,
            request.after_commit,
        )
        if any(function.change_type == "deleted" for function in functions):
            self._git(repo_path, "checkout", "--detach", request.before_commit)
            try:
                before_project_id = self.client.index_repository(repo_path, f"{request.project_name}@before")
                self._before_project_ids[request.project_name] = before_project_id
            except Exception as error:
                self._before_project_ids.pop(request.project_name, None)
                self._index_errors[f"{request.project_name}@before"] = str(error)
            self._git(repo_path, "checkout", "--detach", request.after_commit)
        return DiffAnalysis(project_name=request.project_name, changed_functions=functions)

    def two_hop_call_graph(
        self,
        project_name: str,
        functions: list[ChangedFunction],
        depth: int = 2,
        progress: Callable[[str], None] | None = None,
    ) -> CallGraph:
        _report(progress, "trace_call_graph")
        inbound: dict[str, list[str]] = {}
        outbound: dict[str, list[str]] = {}
        trace_status: dict[str, str] = {}
        trace_errors: dict[str, str] = {}

        for function in functions:
            inbound_calls, inbound_status, inbound_error = self._trace_function_with_status(
                project_name,
                function,
                "inbound",
                depth,
            )
            outbound_calls, outbound_status, outbound_error = self._trace_function_with_status(
                project_name,
                function,
                "outbound",
                depth,
            )
            inbound[function.qualified_name] = inbound_calls
            outbound[function.qualified_name] = outbound_calls
            trace_status[function.qualified_name] = _combine_trace_status(inbound_status, outbound_status)
            errors = [error for error in (inbound_error, outbound_error) if error]
            if errors:
                trace_errors[function.qualified_name] = "；".join(errors)

        return CallGraph(
            project_name=project_name,
            depth=depth,
            inbound=inbound,
            outbound=outbound,
            trace_status=trace_status,
            trace_errors=trace_errors,
        )

    def _trace_function_with_status(
        self,
        project_name: str,
        function: ChangedFunction,
        direction: str,
        depth: int,
    ) -> tuple[list[str], str, str]:
        project_id = self._project_ids.get(project_name)
        index_error = self._index_errors.get(project_name)
        if function.change_type == "deleted":
            project_id = self._before_project_ids.get(project_name, project_id)
            index_error = self._index_errors.get(f"{project_name}@before", index_error)

        if not project_id:
            fallback_calls = self._fallback_trace(project_name, function, direction)
            status = "fallback_success" if fallback_calls else "index_failed"
            return fallback_calls, status, index_error or "codebase-memory-mcp index did not produce a usable project id"

        calls, status, error = self._trace_two_hop_with_status(project_id, function.qualified_name, direction, depth)
        if status == "not_found":
            fallback_calls = self._fallback_trace(project_name, function, direction)
            if fallback_calls:
                return fallback_calls, "fallback_success", error
        if status == "success" and function.language.lower() == "lua":
            fallback_calls = self._fallback_trace(project_name, function, direction)
            merged_calls = _merge_calls(calls, fallback_calls)
            if len(merged_calls) > len(calls):
                return merged_calls, "augmented_success", error
        return calls, status, error

    def _trace_two_hop_with_status(self, project_id: str, function_name: str, direction: str, depth: int) -> tuple[list[str], str, str]:
        try:
            return self.client.trace_two_hop(project_id, function_name, direction, depth), "success", ""
        except Exception as error:
            if _is_function_not_found_error(error):
                return [], "not_found", str(error)
            raise

    def _fallback_trace(self, project_name: str, function: ChangedFunction, direction: str) -> list[str]:
        repo_path = self._repo_paths.get(project_name)
        if repo_path is None:
            return []
        if direction == "outbound":
            return _fallback_outbound_calls(repo_path, function)
        return _fallback_inbound_callers(repo_path, function)

    def _checkout(self, request: ImpactAnalysisRequest) -> Path:
        workspace_root = self.workspace_root.resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        repo_path = workspace_root / _safe_project_dir(request.project_name)
        if not (repo_path / ".git").exists():
            self._git(workspace_root, "clone", request.git_url, str(repo_path))
        else:
            self._ensure_origin_url(repo_path, request.git_url)
        self._git(repo_path, "fetch", "--all")
        branch_ref = self._resolve_branch_ref(repo_path, request.branch)
        self._ensure_commit_exists(repo_path, request.before_commit, "before_commit")
        self._ensure_commit_exists(repo_path, request.after_commit, "after_commit")
        self._ensure_ancestor(repo_path, request.after_commit, branch_ref, "after_commit", request.branch)
        self._ensure_ancestor(repo_path, request.before_commit, request.after_commit, "before_commit", "after_commit")
        self._git(repo_path, "checkout", "--detach", request.after_commit)
        return repo_path

    def _resolve_branch_ref(self, repo_path: Path, branch: str) -> str:
        for candidate in (f"origin/{branch}", branch):
            if self._git_ok(repo_path, "rev-parse", "--verify", f"{candidate}^{{commit}}"):
                return candidate
        raise ValueError(f"branch not found after fetch: {branch}")

    def _ensure_commit_exists(self, repo_path: Path, commit: str, field_name: str) -> None:
        if not self._git_ok(repo_path, "rev-parse", "--verify", f"{commit}^{{commit}}"):
            raise ValueError(f"{field_name} is not a valid commit: {commit}")

    def _ensure_ancestor(self, repo_path: Path, ancestor: str, descendant: str, ancestor_name: str, descendant_name: str) -> None:
        if not self._git_ok(repo_path, "merge-base", "--is-ancestor", ancestor, descendant):
            raise ValueError(f"{ancestor_name} is not reachable from {descendant_name}")

    def _ensure_origin_url(self, repo_path: Path, git_url: str) -> None:
        current_origin = self._git(repo_path, "config", "--get", "remote.origin.url").strip()
        if current_origin != git_url:
            self._git(repo_path, "remote", "set-url", "origin", git_url)

    def _git(self, cwd: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            capture_output=True,
        )
        return completed.stdout

    def _git_ok(self, cwd: Path, *args: str) -> bool:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
        )
        return completed.returncode == 0


def _safe_project_dir(project_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", project_name).strip("-") or "project"


def _is_function_not_found_error(error: Exception) -> bool:
    message = str(error).lower()
    return "function not found" in message


def _combine_trace_status(inbound_status: str, outbound_status: str) -> str:
    statuses = {inbound_status, outbound_status}
    if statuses == {"success"}:
        return "success"
    if "augmented_success" in statuses:
        return "augmented_success"
    if "fallback_success" in statuses:
        return "fallback_success"
    if "index_failed" in statuses:
        return "index_failed"
    if statuses == {"not_found"}:
        return "not_found"
    return "partial"


def _merge_calls(primary: list[str], supplemental: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for call in [*primary, *supplemental]:
        if call in seen:
            continue
        seen.add(call)
        merged.append(call)
    return merged


def _fallback_inbound_callers(repo_path: Path, function: ChangedFunction) -> list[str]:
    aliases = _call_aliases(function)
    callers: list[str] = []
    seen: set[str] = set()
    definition_path = repo_path / function.file_path
    for path in _iter_source_files(repo_path):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        relative = _relative_path(repo_path, path)
        for index, line in enumerate(lines):
            if path == definition_path and _is_lua_definition_line(line, function):
                continue
            if not _line_calls_alias(line, aliases):
                continue
            caller = _nearest_function_name(lines, index) or f"{relative}:{index + 1}"
            if caller not in seen:
                seen.add(caller)
                callers.append(caller)
    return callers[:50]


def _fallback_outbound_calls(repo_path: Path, function: ChangedFunction) -> list[str]:
    body = _function_body(repo_path, function) or function.diff_hunk
    calls: list[str] = []
    seen: set[str] = set()
    ignored = {
        "assert",
        "error",
        "if",
        "pairs",
        "pcall",
        "return",
        "string.format",
        "tostring",
        "type",
    }
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_.:]*)\s*\(", body):
        name = match.group(1)
        if name in ignored or name == function.qualified_name or name.endswith(f".{_short_name(function)}"):
            continue
        if name not in seen:
            seen.add(name)
            calls.append(name)
    return calls[:50]


def _function_body(repo_path: Path, function: ChangedFunction) -> str:
    path = repo_path / function.file_path
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    start = None
    for index, line in enumerate(lines):
        if _is_lua_definition_line(line, function):
            start = index
            break
    if start is None:
        return ""
    end = min(len(lines), start + 400)
    for index in range(start + 1, len(lines)):
        if re.match(r"\s*end\s*$", lines[index]):
            end = index + 1
            break
    return "\n".join(lines[start:end])


def _iter_source_files(repo_path: Path):
    suffixes = {".lua", ".t", ".rockspec", ".py", ".js", ".ts", ".go", ".java", ".c", ".cc", ".cpp", ".h", ".hpp", ".php", ".swift"}
    ignored_dirs = {".git", ".codebase-memory", "node_modules", "vendor", "build", "dist"}
    for path in repo_path.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in ignored_dirs for part in path.parts):
            continue
        yield path


def _call_aliases(function: ChangedFunction) -> set[str]:
    short = _short_name(function)
    aliases = {function.qualified_name, short}
    if function.language.lower() == "lua":
        aliases.update({f"_M.{short}", f":{short}"})
    return aliases


def _line_calls_alias(line: str, aliases: set[str]) -> bool:
    stripped = line.split("--", 1)[0]
    if any(re.search(rf"(?<![\w.]){re.escape(alias)}\s*\(", stripped) for alias in aliases):
        return True
    short_names = [alias for alias in aliases if "." not in alias and ":" not in alias]
    return any(re.search(rf"\b[A-Za-z_][\w]*\.{re.escape(short)}\s*\(", stripped) for short in short_names)


def _is_lua_definition_line(line: str, function: ChangedFunction) -> bool:
    short = _short_name(function)
    return bool(re.search(rf"\bfunction\s+(?:[A-Za-z_][\w]*[.:])?{re.escape(short)}\s*\(", line))


def _nearest_function_name(lines: list[str], index: int) -> str:
    for cursor in range(index, max(-1, index - 80), -1):
        line = lines[cursor]
        match = re.search(r"\bfunction\s+([A-Za-z_][\w.:]*)\s*\(", line)
        if match:
            return match.group(1)
        match = re.search(r"\blocal\s+function\s+([A-Za-z_][\w]*)\s*\(", line)
        if match:
            return match.group(1)
    return ""


def _short_name(function: ChangedFunction) -> str:
    return function.qualified_name.rsplit(".", 1)[-1].rsplit(":", 1)[-1]


def _relative_path(repo_path: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_path))
    except ValueError:
        return str(path)


def _report(progress: Callable[[str], None] | None, stage: str) -> None:
    if progress:
        progress(stage)
