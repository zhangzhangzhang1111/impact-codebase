import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock


@dataclass(frozen=True)
class ModelConfig:
    provider_id: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""


class InMemoryModelConfigStore:
    def __init__(self, default_provider_id: str = ""):
        self._configs: dict[str, ModelConfig] = {}
        self._default_provider_id = default_provider_id
        self._lock = RLock()

    def list(self) -> list[ModelConfig]:
        with self._lock:
            return list(self._configs.values())

    def get(self, provider_id: str) -> ModelConfig | None:
        with self._lock:
            return self._configs.get(provider_id)

    def default_provider_id(self) -> str:
        with self._lock:
            return self._default_provider_id

    def set_default_provider_id(self, provider_id: str) -> str:
        with self._lock:
            self._default_provider_id = provider_id.strip()
            self._persist()
            return self._default_provider_id

    def save(
        self,
        provider_id: str,
        model: str = "",
        base_url: str = "",
        api_key: str | None = None,
    ) -> ModelConfig:
        with self._lock:
            existing = self._configs.get(provider_id)
            config = ModelConfig(
                provider_id=provider_id,
                model=model.strip(),
                base_url=base_url.strip(),
                api_key=(existing.api_key if api_key is None and existing else (api_key or "").strip()),
            )
            self._configs[provider_id] = config
            self._persist()
            return config

    def _persist(self) -> None:
        return


class JsonFileModelConfigStore(InMemoryModelConfigStore):
    def __init__(self, path: Path):
        self.path = path
        super().__init__()
        self._configs = self._load()

    def _load(self) -> dict[str, ModelConfig]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self._default_provider_id = payload.get("default_provider_id", "")
        configs = payload.get("configs", [])
        return {
            item["provider_id"]: ModelConfig(
                provider_id=item["provider_id"],
                model=item.get("model", ""),
                base_url=item.get("base_url", ""),
                api_key=item.get("api_key", ""),
            )
            for item in configs
            if isinstance(item, dict) and item.get("provider_id")
        }

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "default_provider_id": self._default_provider_id,
            "configs": [asdict(config) for config in self._configs.values()],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
