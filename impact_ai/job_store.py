import json
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from impact_ai.models import ImpactAnalysisRequest


@dataclass(frozen=True)
class AnalysisJob:
    id: str
    status: str
    request: ImpactAnalysisRequest
    created_at: str
    updated_at: str
    result: dict | None = None
    error: str | None = None
    progress: list[str] | None = None
    logs: list[dict] | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "request": asdict(self.request),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
            "progress": self.progress or [],
            "logs": self.logs or [],
        }


class InMemoryJobStore:
    def __init__(self):
        self._jobs: dict[str, AnalysisJob] = {}
        self._lock = threading.RLock()

    def create(self, request: ImpactAnalysisRequest) -> AnalysisJob:
        with self._lock:
            now = datetime.now(UTC).isoformat()
            job = AnalysisJob(
                id=uuid4().hex,
                status="queued",
                request=request,
                created_at=now,
                updated_at=now,
                progress=["queued"],
                logs=[_log_entry("queued", "任务已创建并进入队列")],
            )
            self._jobs[job.id] = job
            return job

    def list(self) -> list[AnalysisJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def get(self, job_id: str) -> AnalysisJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start(self, job_id: str) -> AnalysisJob:
        with self._lock:
            job = self._jobs[job_id]
            updated = AnalysisJob(
                id=job.id,
                status="running",
                request=job.request,
                created_at=job.created_at,
                updated_at=datetime.now(UTC).isoformat(),
                result=job.result,
                error=job.error,
                progress=[*(job.progress or []), "running"],
                logs=[*(job.logs or []), _log_entry("running", "任务开始运行")],
            )
            self._jobs[job_id] = updated
            return updated

    def add_progress(self, job_id: str, stage: str) -> AnalysisJob:
        with self._lock:
            job = self._jobs[job_id]
            updated = AnalysisJob(
                id=job.id,
                status=job.status,
                request=job.request,
                created_at=job.created_at,
                updated_at=datetime.now(UTC).isoformat(),
                result=job.result,
                error=job.error,
                progress=[*(job.progress or []), stage],
                logs=[*(job.logs or []), _log_entry(stage, _stage_message(stage))],
            )
            self._jobs[job_id] = updated
            return updated

    def complete(self, job_id: str, result: dict) -> AnalysisJob:
        with self._lock:
            job = self._jobs[job_id]
            updated = AnalysisJob(
                id=job.id,
                status="completed",
                request=job.request,
                created_at=job.created_at,
                updated_at=datetime.now(UTC).isoformat(),
                result=result,
                error=None,
                progress=[*(job.progress or []), "completed"],
                logs=[*(job.logs or []), _log_entry("completed", "分析完成")],
            )
            self._jobs[job_id] = updated
            return updated

    def fail(self, job_id: str, error: str) -> AnalysisJob:
        with self._lock:
            job = self._jobs[job_id]
            updated = AnalysisJob(
                id=job.id,
                status="failed",
                request=job.request,
                created_at=job.created_at,
                updated_at=datetime.now(UTC).isoformat(),
                result=None,
                error=error,
                progress=[*(job.progress or []), "failed"],
                logs=[*(job.logs or []), _log_entry("failed", "分析失败", level="error", detail=error)],
            )
            self._jobs[job_id] = updated
            return updated


class JsonFileJobStore(InMemoryJobStore):
    def __init__(self, path: Path):
        self.path = path
        super().__init__()
        self._load()

    def create(self, request: ImpactAnalysisRequest) -> AnalysisJob:
        with self._lock:
            job = super().create(request)
            self._save()
            return job

    def start(self, job_id: str) -> AnalysisJob:
        with self._lock:
            job = super().start(job_id)
            self._save()
            return job

    def add_progress(self, job_id: str, stage: str) -> AnalysisJob:
        with self._lock:
            job = super().add_progress(job_id, stage)
            self._save()
            return job

    def complete(self, job_id: str, result: dict) -> AnalysisJob:
        with self._lock:
            job = super().complete(job_id, result)
            self._save()
            return job

    def fail(self, job_id: str, error: str) -> AnalysisJob:
        with self._lock:
            job = super().fail(job_id, error)
            self._save()
            return job

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            for item in payload.get("jobs", []):
                request_payload = item["request"]
                job = AnalysisJob(
                    id=item["id"],
                    status=item["status"],
                    request=ImpactAnalysisRequest(**request_payload),
                    created_at=item["created_at"],
                    updated_at=item["updated_at"],
                    result=item.get("result"),
                    error=item.get("error"),
                    progress=item.get("progress", []),
                    logs=item.get("logs", []),
                )
                self._jobs[job.id] = job

    def _save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"jobs": [job.to_dict() for job in self.list()]}
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _log_entry(stage: str, message: str, level: str = "info", detail: str = "") -> dict:
    return {
        "time": datetime.now(UTC).isoformat(),
        "stage": stage,
        "level": level,
        "message": message,
        "detail": detail,
    }


def _stage_message(stage: str) -> str:
    return {
        "queued": "任务已创建并进入队列",
        "running": "任务开始运行",
        "checkout_repository": "拉取或更新 Git 仓库",
        "index_repository": "构建 codebase-memory-mcp 知识图谱索引",
        "extract_changed_functions": "从 Git Diff 中提取改动函数",
        "changed_functions": "分析改动函数",
        "trace_call_graph": "追踪调用链路",
        "two_hop_call_graph": "整理调用链路结果",
        "prompt_build": "构建模型提示词",
        "ai_request": "调用 AI 模型生成影响面分析",
        "ai_response": "AI 模型已返回结果",
        "completed": "分析完成",
        "failed": "分析失败",
    }.get(stage, stage)
