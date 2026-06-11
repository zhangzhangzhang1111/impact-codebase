from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, Union

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
    changed_functions: List[ChangedFunction]


@dataclass(frozen=True)
class CallGraph:
    project_name: str
    depth: int
    inbound: Dict[str, List[str]] = field(default_factory=dict)
    outbound: Dict[str, List[str]] = field(default_factory=dict)
    trace_status: Dict[str, str] = field(default_factory=dict)
    trace_errors: Dict[str, str] = field(default_factory=dict)


class KnowledgeGraph:
    def changed_functions(self, request: ImpactAnalysisRequest) -> DiffAnalysis:
        raise NotImplementedError

    def two_hop_call_graph(self, project_name: str, functions: List[ChangedFunction], depth: int = 2) -> CallGraph:
        raise NotImplementedError
