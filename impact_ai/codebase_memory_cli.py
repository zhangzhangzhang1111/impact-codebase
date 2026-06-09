import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable


Runner = Callable[[list[str], str], subprocess.CompletedProcess]


class CodebaseMemoryCliError(RuntimeError):
    pass


class CodebaseMemoryCliClient:
    def __init__(
        self,
        binary: str = "codebase-memory-mcp",
        runner: Runner | None = None,
        index_mode: str = "fast",
        cache_dir: Path | None = None,
    ):
        self.binary = binary
        self.runner = runner
        self.index_mode = index_mode
        self.cache_dir = cache_dir

    def index_repository(self, repo_path: Path, project_name: str) -> str:
        payload = {"repo_path": str(repo_path.resolve()), "project_name": project_name, "mode": self.index_mode}
        response = self._call("index_repository", payload)
        return response.get("project") or response.get("name") or project_name

    def trace_two_hop(self, project_id: str, function_name: str, direction: str, depth: int = 2) -> list[str]:
        try:
            resolved_name = function_name
            response = self._trace_path(project_id, resolved_name, direction, depth)
        except CodebaseMemoryCliError as error:
            if not _is_function_not_found_error(error):
                raise
            resolved_name = self._resolve_function_name(project_id, function_name)
            if not resolved_name or resolved_name == function_name:
                raise
            response = self._trace_path(project_id, resolved_name, direction, depth)
        return _extract_trace_names(response, exclude={function_name, resolved_name})

    def _trace_path(self, project_id: str, function_name: str, direction: str, depth: int) -> dict:
        return self._call(
            "trace_path",
            {
                "project": project_id,
                "function_name": function_name,
                "direction": direction,
                "depth": depth,
            },
        )

    def _resolve_function_name(self, project_id: str, function_name: str) -> str:
        short_name = function_name.rsplit(".", 1)[-1].rsplit(":", 1)[-1]
        response = self._call(
            "search_graph",
            {
                "project": project_id,
                "name_pattern": f".*{re.escape(short_name)}.*",
                "limit": 20,
            },
        )
        return _best_function_match(function_name, response.get("results", []))

    def _call(self, tool: str, payload: dict) -> dict:
        encoded_payload = json.dumps(payload, ensure_ascii=False)
        args = [self.binary, "cli", tool]
        completed = self.runner(args, encoded_payload) if self.runner else _run_cli(args, encoded_payload, self.cache_dir)
        if completed.returncode != 0:
            raise CodebaseMemoryCliError(_format_cli_error(tool, completed.stdout, completed.stderr))
        return _parse_json_from_stdout(completed.stdout)


def _run_cli(args: list[str], payload: str, cache_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        env["CBM_CACHE_DIR"] = str(cache_dir)
    return subprocess.run(
        [*args, payload],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )


def _parse_json_from_stdout(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        return json.loads(stripped)
    raise CodebaseMemoryCliError("codebase-memory-mcp did not return a JSON object.")


def _format_cli_error(tool: str, stdout: str, stderr: str) -> str:
    details = [f"{tool} failed"]
    try:
        payload = _parse_json_from_stdout(stdout)
    except CodebaseMemoryCliError:
        payload = {}

    if payload.get("error"):
        details.append(str(payload["error"]))
    if payload.get("hint"):
        details.append(str(payload["hint"]))
    if stderr.strip():
        details.append(stderr.strip())
    if not payload and stdout.strip():
        details.append(stdout.strip())
    return ": ".join(details)


def _extract_trace_names(response: dict, exclude: str | set[str] | None = None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    excluded = {exclude} if isinstance(exclude, str) else (exclude or set())

    def add(value: object) -> None:
        if not isinstance(value, str) or value in excluded or value in seen:
            return
        seen.add(value)
        names.append(value)

    for item in response.get("paths", []):
        if isinstance(item, dict):
            for node in item.get("nodes", []):
                if isinstance(node, dict):
                    add(node.get("qualified_name") or node.get("name"))
                else:
                    add(node)
            for node in item.get("path", []):
                if isinstance(node, dict):
                    add(node.get("qualified_name") or node.get("name"))
                else:
                    add(node)
        elif isinstance(item, list):
            for node in item:
                if isinstance(node, dict):
                    add(node.get("qualified_name") or node.get("name"))
                else:
                    add(node)

    for key in ("nodes", "callers", "callees", "results"):
        for item in response.get(key, []):
            if isinstance(item, dict):
                add(item.get("qualified_name") or item.get("name"))
            else:
                add(item)

    return names


def _best_function_match(function_name: str, results: list[object]) -> str:
    candidates = [item for item in results if isinstance(item, dict)]
    short_name = function_name.rsplit(".", 1)[-1].rsplit(":", 1)[-1]

    def qualified_name(item: dict) -> str:
        value = item.get("qualified_name") or item.get("name") or ""
        return value if isinstance(value, str) else ""

    for item in candidates:
        if qualified_name(item) == function_name:
            return qualified_name(item)
    for item in candidates:
        qn = qualified_name(item)
        if qn.endswith(f".{function_name}") or qn.endswith(f":{function_name}"):
            return qn
    for item in candidates:
        qn = qualified_name(item)
        if qn.endswith(f".{short_name}") or qn.endswith(f":{short_name}"):
            return qn
    return qualified_name(candidates[0]) if candidates else ""


def _is_function_not_found_error(error: Exception) -> bool:
    return "function not found" in str(error).lower()
