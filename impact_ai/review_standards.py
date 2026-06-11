from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, Union
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock


COMMON_SECTIONS = {
    "正确性": [
        "核对改动行为是否满足调用方预期、边界条件和异常路径。",
        "检查错误处理、空值处理、并发一致性、事务回滚和状态恢复。",
    ],
    "安全性": [
        "审查认证、授权、注入风险、密钥处理和不安全反序列化。",
        "确认日志、错误信息和生成报告不会泄露凭据、隐私或业务敏感数据。",
    ],
    "测试": [
        "改动函数需要单元测试覆盖，对外 API 或跨模块行为需要集成测试覆盖。",
        "针对按配置深度追踪到的受影响调用方补充回归测试。",
    ],
    "可维护性": [
        "保持改动小而清晰，遵循项目既有风格、命名和模块边界。",
        "标记不必要的抽象、重复业务规则和难以维护的隐式依赖。",
    ],
}

LANGUAGE_NOTES = {
    "python": ["检查类型标注、异步边界、上下文管理器、依赖注入和异常传播。"],
    "javascript": ["检查 Promise 处理、模块边界、浏览器/运行时兼容性和异步竞态。"],
    "typescript": ["检查类型收窄、公开接口、泛型约束以及编译后的运行时行为。"],
    "java": ["检查事务边界、线程安全、注解配置、异常契约和资源关闭。"],
    "go": ["检查错误传播、goroutine 安全、context 取消、接口粒度和 defer 使用。"],
    "rust": ["检查所有权、生命周期、unsafe 代码块、panic 路径和错误枚举设计。"],
    "c": ["检查指针所有权、内存分配/释放、宏副作用、未定义行为、ABI 兼容性和线程安全。"],
    "cpp": ["检查所有权、RAII、未定义行为、ABI 兼容性、锁和并发访问。"],
    "csharp": ["检查 async/await、可空引用、LINQ 成本、依赖生命周期和释放语义。"],
    "php": ["检查请求校验、框架中间件、魔术方法、SQL 边界和会话状态。"],
    "ruby": ["检查元编程、框架回调、nil 处理、迁移安全和隐式 monkey patch。"],
    "swift": ["检查 actor 隔离、可选值、内存所有权和 UI 线程访问。"],
    "kotlin": ["检查空安全、协程、data class、Java 互操作和挂起函数契约。"],
    "lua": ["检查协程、元表、闭包 upvalue、require 缓存、全局变量污染和 C API 边界。"],
}

DEFAULT_REVIEW_STANDARD_LANGUAGES = tuple(LANGUAGE_NOTES.keys())


@dataclass(frozen=True)
class ReviewStandard:
    language: str
    sections: Dict[str, List[str]]


def standard_for_language(language: str) -> ReviewStandard:
    normalized = language.lower().strip()
    selected_language = normalized if normalized in LANGUAGE_NOTES else "generic"
    sections = {section: list(items) for section, items in COMMON_SECTIONS.items()}

    if selected_language != "generic":
        sections["语言专项"] = LANGUAGE_NOTES[selected_language]

    return ReviewStandard(language=selected_language, sections=sections)


class InMemoryReviewStandardStore:
    def __init__(self):
        self._standards: Dict[str, ReviewStandard] = {}
        self._lock = RLock()

    def list(self) -> List[ReviewStandard]:
        with self._lock:
            standards = {language: standard_for_language(language) for language in DEFAULT_REVIEW_STANDARD_LANGUAGES}
            standards.update(self._standards)
            return [standards[language] for language in sorted(standards)]

    def get(self, language: str) -> ReviewStandard:
        normalized = language.lower().strip()
        with self._lock:
            return self._standards.get(normalized) or standard_for_language(normalized)

    def save(self, language: str, sections: Dict[str, List[str]]) -> ReviewStandard:
        normalized = language.lower().strip() or "generic"
        standard = ReviewStandard(language=normalized, sections={key: list(value) for key, value in sections.items()})
        with self._lock:
            self._standards[normalized] = standard
            self._persist()
            return standard

    def _persist(self) -> None:
        return


class JsonFileReviewStandardStore(InMemoryReviewStandardStore):
    def __init__(self, path: Path):
        self.path = path
        super().__init__()
        self._standards = self._load()

    def _load(self) -> Dict[str, ReviewStandard]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        standards = payload.get("standards", [])
        return {
            item["language"]: ReviewStandard(
                language=item["language"],
                sections={key: list(value) for key, value in item.get("sections", {}).items()},
            )
            for item in standards
            if isinstance(item, dict) and item.get("language") and isinstance(item.get("sections"), dict)
        }

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"standards": [asdict(standard) for standard in self._standards.values()]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
