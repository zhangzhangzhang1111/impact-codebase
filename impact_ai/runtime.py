import os
import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Mapping, Any, Dict, List, Optional, Set, Tuple, Union

from impact_ai.ai_client import OpenAICompatibleClient
from impact_ai.ai_providers import provider_catalog
from impact_ai.analysis import ImpactAnalyzer
from impact_ai.codebase_memory_cli import CodebaseMemoryCliClient
from impact_ai.codebase_memory_graph import CodebaseMemoryClient, CodebaseMemoryKnowledgeGraph
from impact_ai.http_server import _apply_model_config_to_analyzer, create_server
from impact_ai.job_store import JsonFileJobStore
from impact_ai.model_config import JsonFileModelConfigStore
from impact_ai.project_profiles import ProjectProfileLoader
from impact_ai.review_standards import JsonFileReviewStandardStore
from impact_ai.token_budget import TokenBudget


ProcessLauncher = Callable[..., subprocess.Popen]


class ManagedCodebaseMemoryProcess:
    def __init__(self, process: Optional[subprocess.Popen], processes: Optional[List[subprocess.Popen]] = None):
        self.process = process
        self.processes = processes if processes is not None else ([process] if process is not None else [])

    def close(self) -> None:
        for process in self.processes:
            if process.poll() is not None:
                continue
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def build_analyzer_from_env(
    env: Optional[Mapping[str, str]] = None,
    codebase_memory_client: Optional[CodebaseMemoryClient] = None,
) -> ImpactAnalyzer:
    env = with_claude_network_env(env or os.environ)
    _apply_network_env_to_process(env)
    workspace_root = Path(env.get("IMPACT_AI_WORKSPACE_ROOT", ".impact-ai/repos"))
    default_codebase_memory_cache = workspace_root.parent / "codebase-memory-cache"
    profile_root = Path(env.get("IMPACT_AI_PROFILE_ROOT", "profiles"))
    review_standard_store = JsonFileReviewStandardStore(Path(env.get("IMPACT_AI_REVIEW_STANDARDS_PATH", ".impact-ai/review_standards.json")))
    client = codebase_memory_client or CodebaseMemoryCliClient(
        binary=env.get("CODEBASE_MEMORY_MCP_BIN", "codebase-memory-mcp"),
        index_mode=env.get("CODEBASE_MEMORY_INDEX_MODE", "fast"),
        cache_dir=Path(env.get("CODEBASE_MEMORY_CACHE_DIR", str(default_codebase_memory_cache))),
    )

    token_budget = _token_budget_from_env(env)
    return ImpactAnalyzer(
        knowledge_graph=CodebaseMemoryKnowledgeGraph(workspace_root, client),
        ai_client=OpenAICompatibleClient(
            api_keys=_configured_values(env, "api_key_env"),
            base_urls=_configured_values(env, "base_url_env"),
            models=_configured_models(env),
        ),
        profile_loader=ProjectProfileLoader(profile_root),
        token_budget=token_budget,
        review_standard_store=review_standard_store,
    )


def create_configured_server(
    address: Tuple[str, int],
    env: Optional[Mapping[str, str]] = None,
    codebase_memory_client: Optional[CodebaseMemoryClient] = None,
    codebase_memory_launcher: Optional[ProcessLauncher] = None,
):
    analyzer = build_analyzer_from_env(env=env, codebase_memory_client=codebase_memory_client)
    env = with_claude_network_env(env or os.environ)
    _apply_network_env_to_process(env)
    job_store = JsonFileJobStore(Path(env.get("IMPACT_AI_HISTORY_PATH", ".impact-ai/history.json")))
    profile_loader = ProjectProfileLoader(Path(env.get("IMPACT_AI_PROFILE_ROOT", "profiles")))
    model_config_path = Path(env.get("IMPACT_AI_MODEL_CONFIG_PATH", ".impact-ai/model_config.json"))
    _seed_model_config_if_missing(
        model_config_path,
        Path(env.get("IMPACT_AI_DEFAULT_MODEL_CONFIG_PATH", str(_default_model_config_template_path()))),
    )
    model_config_store = JsonFileModelConfigStore(model_config_path)
    review_standard_store = JsonFileReviewStandardStore(Path(env.get("IMPACT_AI_REVIEW_STANDARDS_PATH", ".impact-ai/review_standards.json")))
    analyzer.review_standard_store = review_standard_store
    for config in model_config_store.list():
        _apply_model_config_to_analyzer(analyzer, config)
    server = create_server(
        address,
        analyzer=analyzer,
        execute_async=True,
        job_store=job_store,
        profile_loader=profile_loader,
        model_config_store=model_config_store,
        review_standard_store=review_standard_store,
    )
    if codebase_memory_client is None:
        process = start_managed_codebase_memory_process(env, launcher=codebase_memory_launcher)
        _attach_managed_process(server, process)
    return server


def start_managed_codebase_memory_process(
    env: Mapping[str, str],
    launcher: Optional[ProcessLauncher] = None,
) -> ManagedCodebaseMemoryProcess:
    env = with_claude_network_env(env)
    if _env_flag(env, "IMPACT_AI_MANAGE_CODEBASE_MEMORY", default=True) is False:
        return ManagedCodebaseMemoryProcess(None)

    binary = env.get("CODEBASE_MEMORY_MCP_BIN", "codebase-memory-mcp")
    ui_port = env.get("CODEBASE_MEMORY_UI_PORT", "9749")
    cache_dir = Path(env.get("CODEBASE_MEMORY_CACHE_DIR", ".impact-ai/codebase-memory-cache"))
    process_env = os.environ.copy()
    process_env.update(dict(env))
    process_env["CBM_CACHE_DIR"] = str(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    run = launcher or subprocess.Popen
    processes = []

    if _env_flag(env, "CODEBASE_MEMORY_ENABLE_UI", default=True):
        ui_process = run(
            [binary, "--ui=true", f"--port={ui_port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=process_env,
            universal_newlines=True,
        )
        processes.append(ui_process)

    process = run(
        [binary],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=process_env,
        universal_newlines=True,
    )
    processes.append(process)
    return ManagedCodebaseMemoryProcess(process, processes)


def _attach_managed_process(server, process: ManagedCodebaseMemoryProcess) -> None:
    server.codebase_memory_process = process
    original_server_close = server.server_close

    def server_close_with_codebase_memory():
        try:
            process.close()
        finally:
            original_server_close()

    server.server_close = server_close_with_codebase_memory


def _env_flag(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = env.get(key)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def with_claude_network_env(env: Mapping[str, str]) -> Dict[str, str]:
    merged = dict(env)
    claude_env = _claude_settings_env()
    for key in ("NO_PROXY", "no_proxy"):
        value = claude_env.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        merged[key] = _merge_csv_values(os.environ.get(key, ""), merged.get(key, ""), value)
    return merged


def _apply_network_env_to_process(env: Mapping[str, str]) -> None:
    for key in ("NO_PROXY", "no_proxy"):
        value = env.get(key)
        if value:
            os.environ[key] = value


def _claude_settings_env() -> Dict[str, str]:
    settings_path = Path(os.environ.get("HOME", str(Path.home()))) / ".claude" / "settings.json"
    if not settings_path.exists():
        return {}
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    env = payload.get("env")
    return env if isinstance(env, dict) else {}


def _merge_csv_values(*values: str) -> str:
    merged = []
    seen = set()
    for value in values:
        for item in value.split(","):
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return ",".join(merged)


def _default_model_config_template_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "model_config.default.json"


def _seed_model_config_if_missing(target_path: Path, template_path: Path) -> None:
    if target_path.exists() or not template_path.exists():
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(template_path), str(target_path))


def _token_budget_from_env(env: Mapping[str, str]) -> Optional[TokenBudget]:
    required_keys = (
        "IMPACT_AI_MAX_INPUT_TOKENS",
        "IMPACT_AI_MAX_OUTPUT_TOKENS",
        "IMPACT_AI_RESERVED_OUTPUT_TOKENS",
    )
    if not all(key in env for key in required_keys):
        return None
    return TokenBudget(
        max_input_tokens=int(env["IMPACT_AI_MAX_INPUT_TOKENS"]),
        max_output_tokens=int(env["IMPACT_AI_MAX_OUTPUT_TOKENS"]),
        reserved_output_tokens=int(env["IMPACT_AI_RESERVED_OUTPUT_TOKENS"]),
    )


def _configured_values(env: Mapping[str, str], provider_field: str) -> Dict[str, str]:
    values = {}
    for provider in provider_catalog():
        env_key = getattr(provider, provider_field)
        if env_key in env:
            values[env_key] = env[env_key]
    return values


def _configured_models(env: Mapping[str, str]) -> Dict[str, str]:
    models = {}
    for provider in provider_catalog():
        if provider.model_env in env:
            models[provider.id] = env[provider.model_env]
    return models
