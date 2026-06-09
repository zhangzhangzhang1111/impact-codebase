from dataclasses import dataclass, field
from typing import Protocol

from impact_ai.models import ImpactAnalysisRequest


@dataclass(frozen=True)
class ChangedFunction:
    qualified_name: str
    language: str
    file_path: str
    signature: str
    diff_hunk: str
    change_type: str = "modified"


@dataclass(frozen=True)
class DiffAnalysis:
    project_name: str
    changed_functions: list[ChangedFunction]


@dataclass(frozen=True)
class CallGraph:
    project_name: str
    depth: int
    inbound: dict[str, list[str]] = field(default_factory=dict)
    outbound: dict[str, list[str]] = field(default_factory=dict)
    trace_status: dict[str, str] = field(default_factory=dict)
    trace_errors: dict[str, str] = field(default_factory=dict)


class KnowledgeGraph(Protocol):
    def changed_functions(self, request: ImpactAnalysisRequest) -> DiffAnalysis:
        raise NotImplementedError

    def two_hop_call_graph(self, project_name: str, functions: list[ChangedFunction], depth: int = 2) -> CallGraph:
        raise NotImplementedError
