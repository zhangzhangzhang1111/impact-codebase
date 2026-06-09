from dataclasses import dataclass


@dataclass(frozen=True)
class ImpactAnalysisRequest:
    git_url: str
    branch: str
    before_commit: str
    after_commit: str
    project_name: str
    provider_id: str
    call_graph_depth: int = 2
