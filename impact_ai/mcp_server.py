from typing import Any, Dict, Iterable, List, Mapping, Optional, TextIO
import json
import sys
from dataclasses import asdict

from impact_ai.ai_providers import provider_catalog
from impact_ai.knowledge_graph import CallGraph
from impact_ai.models import ImpactAnalysisRequest


SERVER_NAME = "impact-codebase"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2025-03-26"


class McpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def handle_mcp_message(message: Mapping[str, Any], analyzer=None, job_store=None) -> Optional[Dict[str, Any]]:
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None and method in {"notifications/initialized", "initialized"}:
        return None

    try:
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            raise McpError(-32600, "invalid JSON-RPC request")
        result = _dispatch_mcp_method(method, message.get("params") or {}, analyzer=analyzer, job_store=job_store)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except McpError as error:
        return _error_response(request_id, error.code, error.message)
    except Exception as error:
        return _error_response(request_id, -32000, str(error))


def run_stdio_loop(stdin: TextIO = None, stdout: TextIO = None, analyzer=None, job_store=None, use_content_length: bool = False) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for message in _read_stdio_messages(stdin):
        response = handle_mcp_message(message, analyzer=analyzer, job_store=job_store)
        if response is None:
            continue
        _write_stdio_message(stdout, response, use_content_length=use_content_length)


def main() -> None:
    from impact_ai.runtime import build_analyzer_from_env

    run_stdio_loop(analyzer=build_analyzer_from_env(), use_content_length=True)


def _dispatch_mcp_method(method: str, params: Mapping[str, Any], analyzer=None, job_store=None) -> Dict[str, Any]:
    if method == "initialize":
        return _initialize_result(params)
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": _tools()}
    if method == "tools/call":
        return _call_tool(params, analyzer=analyzer, job_store=job_store)
    raise McpError(-32601, "method not found: {}".format(method))


def _initialize_result(params: Mapping[str, Any]) -> Dict[str, Any]:
    requested_version = params.get("protocolVersion")
    protocol_version = requested_version if isinstance(requested_version, str) and requested_version else DEFAULT_PROTOCOL_VERSION
    return {
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def _tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "analyze_code_impact",
            "description": "Analyze a Git commit range, trace impacted calls, and return Chinese impact review output.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "git_url": {"type": "string", "description": "Git repository URL."},
                    "branch": {"type": "string", "description": "Branch that contains after_commit."},
                    "before_commit": {"type": "string", "description": "Base commit SHA."},
                    "after_commit": {"type": "string", "description": "Head commit SHA."},
                    "project_name": {"type": "string", "description": "Optional display/cache project name."},
                    "provider_id": {"type": "string", "description": "AI provider id, such as deepseek or openai."},
                    "call_graph_depth": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
                },
                "required": ["git_url", "branch", "before_commit", "after_commit", "provider_id"],
            },
        },
        {
            "name": "list_analysis_jobs",
            "description": "List persisted impact-analysis jobs known to this service.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_analysis_job",
            "description": "Fetch a persisted impact-analysis job by id.",
            "inputSchema": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
        {
            "name": "list_ai_providers",
            "description": "List supported AI provider ids and default model metadata.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def _call_tool(params: Mapping[str, Any], analyzer=None, job_store=None) -> Dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise McpError(-32602, "tool arguments must be an object")
    if name == "analyze_code_impact":
        return _call_analyze_code_impact(arguments, analyzer)
    if name == "list_analysis_jobs":
        jobs = [job.to_dict() for job in job_store.list()] if job_store is not None else []
        return _tool_result({"jobs": jobs}, "Found {} analysis job(s).".format(len(jobs)))
    if name == "get_analysis_job":
        job_id = _required_string(arguments, "job_id")
        job = job_store.get(job_id) if job_store is not None else None
        if job is None:
            raise McpError(-32602, "analysis job not found: {}".format(job_id))
        return _tool_result({"job": job.to_dict()}, "Analysis job {} loaded.".format(job_id))
    if name == "list_ai_providers":
        providers = [asdict(provider) for provider in provider_catalog()]
        return _tool_result({"providers": providers}, "Found {} AI provider(s).".format(len(providers)))
    raise McpError(-32602, "unknown MCP tool: {}".format(name))


def _call_analyze_code_impact(arguments: Mapping[str, Any], analyzer) -> Dict[str, Any]:
    if analyzer is None:
        raise McpError(-32000, "analyzer is not configured")
    request = ImpactAnalysisRequest(
        git_url=_required_string(arguments, "git_url"),
        branch=_required_string(arguments, "branch"),
        before_commit=_required_string(arguments, "before_commit"),
        after_commit=_required_string(arguments, "after_commit"),
        project_name=_project_name(arguments),
        provider_id=_required_string(arguments, "provider_id"),
        call_graph_depth=_call_graph_depth(arguments),
    )
    progress = []
    result = analyzer.analyze(request, lambda stage: progress.append(stage))
    payload = _analysis_result_to_dict(result)
    payload["progress"] = progress
    return _tool_result(payload, payload.get("impact_summary") or "Impact analysis completed.")


def _tool_result(structured_content: Dict[str, Any], text: str) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured_content,
        "isError": False,
    }


def _analysis_result_to_dict(result) -> Dict[str, Any]:
    call_graph = result.call_graph
    return {
        "project_name": result.project_name,
        "changed_functions": [asdict(function) for function in result.changed_functions],
        "call_graph": _call_graph_to_dict(call_graph),
        "impact_summary": result.impact_summary,
        "review_findings": result.review_findings,
        "test_cases": result.test_cases,
        "structured_review_findings": result.structured_review_findings,
        "structured_test_cases": result.structured_test_cases,
        "prompt_chunks": result.prompt_chunks,
        "token_usage": result.token_usage,
    }


def _call_graph_to_dict(call_graph: CallGraph) -> Dict[str, Any]:
    return {
        "project_name": call_graph.project_name,
        "depth": call_graph.depth,
        "inbound": call_graph.inbound,
        "outbound": call_graph.outbound,
        "trace_status": call_graph.trace_status,
        "trace_errors": call_graph.trace_errors,
    }


def _required_string(arguments: Mapping[str, Any], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        raise McpError(-32602, "missing required tool argument: {}".format(field))
    return value.strip()


def _project_name(arguments: Mapping[str, Any]) -> str:
    value = arguments.get("project_name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    git_url = str(arguments.get("git_url", ""))
    name = git_url.rstrip("/").rsplit("/", 1)[-1]
    return name[:-4] if name.endswith(".git") else (name or "project")


def _call_graph_depth(arguments: Mapping[str, Any]) -> int:
    try:
        depth = int(arguments.get("call_graph_depth", 2))
    except (TypeError, ValueError):
        return 2
    return max(1, min(5, depth))


def _error_response(request_id, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _read_stdio_messages(stdin: TextIO) -> Iterable[Dict[str, Any]]:
    while True:
        line = stdin.readline()
        if line == "":
            return
        if not line.strip():
            continue
        if line.lower().startswith("content-length:"):
            yield _read_content_length_message(stdin, line)
            continue
        yield json.loads(line)


def _read_content_length_message(stdin: TextIO, first_header: str) -> Dict[str, Any]:
    length = int(first_header.split(":", 1)[1].strip())
    while True:
        header = stdin.readline()
        if header in ("\r\n", "\n", ""):
            break
        if header.lower().startswith("content-length:"):
            length = int(header.split(":", 1)[1].strip())
    return json.loads(stdin.read(length))


def _write_stdio_message(stdout: TextIO, message: Mapping[str, Any], use_content_length: bool) -> None:
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    if use_content_length:
        stdout.write("Content-Length: {}\r\n\r\n{}".format(len(encoded.encode("utf-8")), encoded))
    else:
        stdout.write(encoded + "\n")
    stdout.flush()


if __name__ == "__main__":
    main()
