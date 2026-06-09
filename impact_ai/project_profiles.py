from dataclasses import dataclass
from pathlib import Path
import re


def _safe_project_dir(project_name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", project_name).strip("-")
    return safe_name or "project"


@dataclass(frozen=True)
class ProjectProfile:
    project_name: str
    business_context: str
    source_path: Path | None


class ProjectProfileLoader:
    def __init__(self, root: Path):
        self.root = root

    def _source_path(self, project_name: str) -> Path:
        return self.root / _safe_project_dir(project_name) / "business.md"

    def load(self, project_name: str) -> ProjectProfile:
        source_path = self._source_path(project_name)
        if not source_path.exists():
            return ProjectProfile(project_name=project_name, business_context="", source_path=None)

        return ProjectProfile(
            project_name=project_name,
            business_context=source_path.read_text(encoding="utf-8"),
            source_path=source_path,
        )

    def save(self, project_name: str, business_context: str) -> ProjectProfile:
        source_path = self._source_path(project_name)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(business_context, encoding="utf-8")
        return ProjectProfile(project_name=project_name, business_context=business_context, source_path=source_path)
