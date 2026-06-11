import json
import inspect
from dataclasses import dataclass, field
from typing import Callable, Any, Dict, List, Mapping, Optional, Set, Tuple, Union

from impact_ai.ai_providers import AIProvider, provider_catalog
from impact_ai.knowledge_graph import CallGraph, ChangedFunction, KnowledgeGraph
from impact_ai.models import ImpactAnalysisRequest
from impact_ai.project_profiles import ProjectProfileLoader
from impact_ai.review_standards import InMemoryReviewStandardStore, standard_for_language
from impact_ai.token_budget import TokenBudget


class AIClient:
    def complete(self, prompt: str, provider: AIProvider, max_output_tokens: int) -> dict:
        raise NotImplementedError


@dataclass(frozen=True)
class ImpactAnalysisResult:
    project_name: str
    changed_functions: List[ChangedFunction]
    call_graph: CallGraph
    impact_summary: str
    review_findings: List[str] = field(default_factory=list)
    test_cases: List[str] = field(default_factory=list)
    structured_review_findings: List[dict] = field(default_factory=list)
    structured_test_cases: List[dict] = field(default_factory=list)
    prompt_chunks: int = 0
    token_usage: Dict[str, object] = field(default_factory=dict)


class ImpactAnalyzer:
    def __init__(
        self,
        knowledge_graph: KnowledgeGraph,
        ai_client: AIClient,
        profile_loader: ProjectProfileLoader,
        token_budget: Optional[TokenBudget] = None,
        review_standard_store: Optional[InMemoryReviewStandardStore] = None,
    ):
        self.knowledge_graph = knowledge_graph
        self.ai_client = ai_client
        self.profile_loader = profile_loader
        self.token_budget = token_budget
        self.review_standard_store = review_standard_store
        self.providers = {provider.id: provider for provider in provider_catalog()}

    def analyze(
        self,
        request: ImpactAnalysisRequest,
        progress: Optional[Callable[[str], None]] = None,
    ) -> ImpactAnalysisResult:
        provider = self.providers[request.provider_id]
        budget = _effective_budget(provider, self.token_budget)

        _report(progress, "changed_functions")
        diff = _call_with_optional_progress(self.knowledge_graph.changed_functions, request, progress=progress)
        _report(progress, "two_hop_call_graph")
        call_graph = _call_with_optional_progress(
            self.knowledge_graph.two_hop_call_graph,
            request.project_name,
            diff.changed_functions,
            request.call_graph_depth,
            progress=progress,
        )
        if not diff.changed_functions:
            return ImpactAnalysisResult(
                project_name=request.project_name,
                changed_functions=[],
                call_graph=call_graph,
                impact_summary="未检测到改动函数，未生成代码评审发现和测试用例。",
                review_findings=[],
                test_cases=[],
                structured_review_findings=[],
                structured_test_cases=[],
                prompt_chunks=0,
                token_usage={
                    "max_input_tokens": budget.max_input_tokens,
                    "max_output_tokens": budget.max_output_tokens,
                    "reserved_output_tokens": budget.reserved_output_tokens,
                    "prompt_chunks": 0,
                    "chunk_input_tokens": [],
                },
            )
        _report(progress, "prompt_build")
        prompt_payload = self._build_prompt_payload(request, diff.changed_functions, call_graph)
        prompt_chunks = self._build_prompt_chunks(prompt_payload, budget)
        chunk_input_tokens = [budget.estimate_tokens(chunk) for chunk in prompt_chunks]

        ai_outputs = []
        for chunk in prompt_chunks:
            _report(progress, "ai_request")
            ai_outputs.append(self.ai_client.complete(chunk, provider=provider, max_output_tokens=budget.max_output_tokens))
            _report(progress, "ai_response")

        return ImpactAnalysisResult(
            project_name=request.project_name,
            changed_functions=diff.changed_functions,
            call_graph=call_graph,
            impact_summary="\n".join(output.get("impact_summary", "") for output in ai_outputs).strip(),
            review_findings=_flatten_unique_text(output.get("review_findings", []) for output in ai_outputs),
            test_cases=_flatten_unique_text(output.get("test_cases", []) for output in ai_outputs),
            structured_review_findings=_flatten_unique_dicts(output.get("review_findings", []) for output in ai_outputs),
            structured_test_cases=_flatten_unique_dicts(output.get("test_cases", []) for output in ai_outputs),
            prompt_chunks=len(prompt_chunks),
            token_usage={
                "max_input_tokens": budget.max_input_tokens,
                "max_output_tokens": budget.max_output_tokens,
                "reserved_output_tokens": budget.reserved_output_tokens,
                "prompt_chunks": len(prompt_chunks),
                "chunk_input_tokens": chunk_input_tokens,
            },
        )

    def _build_prompt_payload(
        self,
        request: ImpactAnalysisRequest,
        changed_functions: List[ChangedFunction],
        call_graph: CallGraph,
    ) -> dict:
        profile = self.profile_loader.load(request.project_name)
        languages = sorted({function.language.lower() for function in changed_functions}) or ["generic"]
        standards = {language: self._review_standard_for_language(language).sections for language in languages}
        call_graph_summary = _call_graph_summary(changed_functions, call_graph)

        return {
            "task": "分析代码改动影响面，生成中文代码评审发现和中文测试用例。",
            "language_requirement": "所有报告结果必须使用中文输出，包括 impact_summary、review_findings.finding、test_cases.name 和 test_cases.covers；评审发现和测试用例必须包含业务大类 business_feature、业务小功能 business_subfeature 和影响等级 impact_level。测试用例需要生成测试标签信息：test_id、affected_business_flows、changed_symbols、affected_files；业务标签可包含英文括注，格式类似 股票行情查询 (Stock HQ Query)。",
            "call_graph_consistency_requirement": "生成 impact_summary、review_findings 和 test_cases 时必须与 call_graph_summary 一致；如果某个函数 outbound_count > 0，不得写无下游、无被调方或无依赖；如果 inbound_count > 0，不得写无上游、无调用方或无影响入口。",
            "project": {
                "name": request.project_name,
                "git_url": request.git_url,
                "branch": request.branch,
                "before_commit": request.before_commit,
                "after_commit": request.after_commit,
            },
            "business_context_markdown": profile.business_context,
            "changed_functions": [
                {
                    "qualified_name": function.qualified_name,
                    "language": function.language,
                    "file_path": function.file_path,
                    "signature": function.signature,
                    "change_type": function.change_type,
                    "diff_hunk": function.diff_hunk,
                }
                for function in changed_functions
            ],
            "call_graph_summary": call_graph_summary,
            "two_hop_call_graph": {
                "depth": call_graph.depth,
                "inbound": call_graph.inbound,
                "outbound": call_graph.outbound,
                "trace_status": call_graph.trace_status,
                "trace_errors": call_graph.trace_errors,
            },
            "review_standards": standards,
            "output_contract": {
                "impact_summary": "中文影响面分析，按改动函数、上游调用方和下游被调方说明风险；必须引用 call_graph_summary，不得与 inbound_count/outbound_count 计数矛盾。",
                "review_findings": [
                    {
                        "function": "改动函数 qualified name。",
                        "business_feature": "中文业务大类，例如鉴权、限流、计费、订单、配置、网关路由；无法判断时用模块名归类。",
                        "business_subfeature": "中文业务小功能，例如响应头读取、限流上下文校验、退款审计写入；避免只填写函数名。",
                        "impact_level": "影响等级，取值为 critical|high|medium|low。",
                        "standard": "评审规范分组，例如正确性、安全性、测试、可维护性或语言专项。",
                        "severity": "low|medium|high|critical.",
                        "finding": "中文可执行评审意见。",
                        "impacted_callers": ["按配置深度追踪到的受影响调用方或被调方名称。"],
                    }
                ],
                "test_cases": [
                    {
                        "test_id": "测试编号，格式 TC001、TC002，按影响面顺序递增。",
                        "name": "中文测试用例名称。",
                        "business_feature": "中文业务大类，必须与相关评审发现或改动函数保持一致。",
                        "business_feature_en": "业务大类英文名，可为空；例如 Trading Service。",
                        "business_subfeature": "中文业务小功能，用业务语言描述要验证的细分能力，避免只填写函数名。",
                        "business_subfeature_en": "业务小功能英文名，可为空；例如 Stock HQ Query。",
                        "impact_level": "影响等级，取值为 critical|high|medium|low。",
                        "type": "unit|integration|regression|contract|e2e.",
                        "target": "待测试的函数、调用方、接口、任务或组件。",
                        "verification_goal": "英文或中英混合校验目标，例如 verify no crash on stock HQ query。",
                        "affected_business_flows": ["受影响业务功能标签，格式为 中文名称 (English name)，例如 股票行情查询 (Stock HQ Query)。"],
                        "changed_symbols": ["与测试相关的变更符号、结构体、函数、接口或配置名，例如 @AzizetStockCodePatternInfo。"],
                        "affected_files": ["与测试相关的文件路径，例如 include/WtService/Define.h。"],
                        "test_tags": ["可用于筛选的测试标签，例如 risk-match、market-buy、trade-limit。"],
                        "covers": ["中文说明覆盖的改动行为、受影响调用方、业务规则或回归风险。"],
                    }
                ],
            },
        }

    def _review_standard_for_language(self, language: str):
        if self.review_standard_store is not None:
            return self.review_standard_store.get(language)
        return standard_for_language(language)

    def _build_prompt_chunks(self, payload: dict, budget: TokenBudget) -> List[str]:
        context_payload = {
            "changed_functions": payload["changed_functions"],
            "call_graph_summary": payload["call_graph_summary"],
            "two_hop_call_graph": payload["two_hop_call_graph"],
            "review_standards": payload["review_standards"],
        }
        context_text = json.dumps(context_payload, ensure_ascii=False, indent=2)
        available_prompt_tokens = budget.max_input_tokens - budget.reserved_output_tokens
        if budget.estimate_tokens(_prompt_chunk_json(payload, "", 1, 1)) > available_prompt_tokens:
            raise ValueError("Prompt envelope exceeds available input token budget.")

        context_chunks = _chunk_context_for_prompt(payload, context_text, budget, max(1, available_prompt_tokens - 8))
        total_chunks = len(context_chunks)

        prompt_chunks = []
        for index, context_chunk in enumerate(context_chunks, start=1):
            prompt_chunks.append(_prompt_chunk_json(payload, context_chunk, index, total_chunks))

        return prompt_chunks


def _chunk_context_for_prompt(payload: dict, context_text: str, budget: TokenBudget, max_tokens: int) -> List[str]:
    chunks: List[str] = []
    current = ""

    for word in context_text.split():
        candidate = f"{current} {word}" if current else word
        candidate_prompt = _prompt_chunk_json(payload, candidate, 1, 1)
        if budget.estimate_tokens(candidate_prompt) <= max_tokens:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        word_prompt = _prompt_chunk_json(payload, word, 1, 1)
        if budget.estimate_tokens(word_prompt) <= max_tokens:
            current = word
            continue

        word_chunks = _split_oversized_context_token(payload, word, budget, max_tokens)
        chunks.extend(word_chunks[:-1])
        current = word_chunks[-1]

    if current:
        chunks.append(current)

    return chunks or [""]


def _call_graph_summary(changed_functions: List[ChangedFunction], call_graph: CallGraph) -> dict:
    summary = {}
    for function in changed_functions:
        name = function.qualified_name
        inbound = call_graph.inbound.get(name, [])
        outbound = call_graph.outbound.get(name, [])
        summary[name] = {
            "inbound_count": len(inbound),
            "outbound_count": len(outbound),
            "has_inbound": bool(inbound),
            "has_outbound": bool(outbound),
            "trace_status": call_graph.trace_status.get(name, "unknown"),
        }
    return summary


def _effective_budget(provider: AIProvider, configured_budget: Optional[TokenBudget]) -> TokenBudget:
    if configured_budget is None:
        return TokenBudget(
            max_input_tokens=provider.max_input_tokens,
            max_output_tokens=provider.max_output_tokens,
            reserved_output_tokens=min(provider.max_output_tokens, 8_192),
        )

    max_input_tokens = min(configured_budget.max_input_tokens, provider.max_input_tokens)
    max_output_tokens = min(configured_budget.max_output_tokens, provider.max_output_tokens)
    reserved_output_tokens = min(configured_budget.reserved_output_tokens, max_input_tokens, provider.max_output_tokens)
    return TokenBudget(
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        reserved_output_tokens=reserved_output_tokens,
    )


def _split_oversized_context_token(payload: dict, token: str, budget: TokenBudget, max_tokens: int) -> List[str]:
    chunks: List[str] = []
    start = 0
    while start < len(token):
        best_size = 0
        low = 1
        high = len(token) - start
        while low <= high:
            size = (low + high) // 2
            candidate = token[start : start + size]
            prompt = _prompt_chunk_json(payload, candidate, 1, 1)
            if budget.estimate_tokens(prompt) <= max_tokens:
                best_size = size
                low = size + 1
            else:
                high = size - 1

        if best_size == 0:
            raise ValueError("Prompt context token cannot fit within available input token budget.")
        chunks.append(token[start : start + best_size])
        start += best_size
    return chunks


def _prompt_chunk_json(payload: dict, context_chunk: str, chunk_index: int, total_chunks: int) -> str:
    chunk_payload = {
        "task": payload["task"],
        "response_instruction": "Return only a valid json object that matches output_contract. Do not include markdown, commentary, or any text outside the json object. 所有报告结果必须使用中文输出。",
        "project": payload["project"],
        "language_requirement": payload["language_requirement"],
        "call_graph_consistency_requirement": payload["call_graph_consistency_requirement"],
        "business_context_markdown": payload["business_context_markdown"],
        "call_graph_summary": payload["call_graph_summary"],
        "output_contract": payload["output_contract"],
        "context_chunk": {
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "text": context_chunk,
        },
    }
    return json.dumps(chunk_payload, ensure_ascii=False, indent=2)


def _flatten_unique_text(values: object) -> List[str]:
    seen: Set[str] = set()
    flattened: List[str] = []
    for value_list in values:
        if not isinstance(value_list, list):
            continue
        for value in value_list:
            text = _text_from_ai_item(value)
            if text and text not in seen:
                seen.add(text)
                flattened.append(text)
    return flattened


def _flatten_unique_dicts(values: object) -> List[dict]:
    seen: Set[str] = set()
    flattened: List[dict] = []
    for value_list in values:
        if not isinstance(value_list, list):
            continue
        for value in value_list:
            if not isinstance(value, dict):
                continue
            key = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            flattened.append(value)
    return flattened


def _text_from_ai_item(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    for key in ("finding", "name", "summary", "title", "description"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _call_with_optional_progress(method, *args, progress):
    parameters = inspect.signature(method).parameters
    if "progress" in parameters:
        return method(*args, progress)
    return method(*args)


def _report(progress: Optional[Callable[[str], None]], stage: str) -> None:
    if progress:
        progress(stage)
