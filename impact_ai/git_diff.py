import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from impact_ai.knowledge_graph import ChangedFunction


@dataclass(frozen=True)
class DiffHunk:
    file_path: str
    old_file_path: str | None
    new_file_path: str | None
    start_line: int
    changed_lines: set[int]
    old_changed_lines: set[int]
    text: str


@dataclass(frozen=True)
class PythonFunctionSpan:
    qualified_name: str
    signature: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class SourceLanguage:
    name: str
    diff_glob: str


class GitDiffFunctionExtractor:
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    def extract_changed_functions(self, before_commit: str, after_commit: str) -> list[ChangedFunction]:
        changed_functions: dict[str, ChangedFunction] = {}

        for language in _supported_languages():
            diff_output = self._git("diff", "--unified=80", before_commit, after_commit, "--", language.diff_glob)
            hunks = _parse_diff_hunks(diff_output)
            for hunk in hunks:
                if hunk.new_file_path and hunk.changed_lines:
                    source = self._git("show", f"{after_commit}:{hunk.new_file_path}")
                    spans = _function_spans(source, hunk.new_file_path)
                    for span in spans:
                        if not hunk.changed_lines.intersection(range(span.start_line, span.end_line + 1)):
                            continue
                        changed_functions[span.qualified_name] = ChangedFunction(
                            qualified_name=span.qualified_name,
                            language=_language_for_path(hunk.new_file_path),
                            file_path=hunk.new_file_path,
                            signature=span.signature,
                            diff_hunk=hunk.text,
                            change_type=_change_type_for_new_side(hunk),
                        )
                if hunk.old_file_path and hunk.old_changed_lines:
                    old_source = self._git("show", f"{before_commit}:{hunk.old_file_path}")
                    old_spans = _function_spans(old_source, hunk.old_file_path)
                    new_span_names = set()
                    if hunk.new_file_path:
                        try:
                            new_source = self._git("show", f"{after_commit}:{hunk.new_file_path}")
                            new_span_names = {new_span.qualified_name for new_span in _function_spans(new_source, hunk.new_file_path)}
                        except subprocess.CalledProcessError:
                            new_span_names = set()
                    for span in old_spans:
                        if span.qualified_name in changed_functions:
                            continue
                        if not hunk.old_changed_lines.intersection(range(span.start_line, span.end_line + 1)):
                            continue
                        change_type = "modified" if span.qualified_name in new_span_names else "deleted"
                        changed_functions[span.qualified_name] = ChangedFunction(
                            qualified_name=span.qualified_name,
                            language=_language_for_path(hunk.old_file_path),
                            file_path=hunk.old_file_path,
                            signature=span.signature,
                            diff_hunk=hunk.text,
                            change_type=change_type,
                        )

        return list(changed_functions.values())

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            check=True,
            text=True,
            capture_output=True,
        )
        return completed.stdout


def _parse_diff_hunks(diff_output: str) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    current_file: str | None = None
    old_file: str | None = None
    current_lines: list[str] = []
    changed_lines: set[int] = set()
    old_changed_lines: set[int] = set()
    new_line = 0
    old_line = 0
    start_line = 0

    for line in diff_output.splitlines():
        if line.startswith("--- "):
            old_file = _diff_path(line.removeprefix("--- "))
            continue

        if line.startswith("+++ "):
            current_file = _diff_path(line.removeprefix("+++ "))
            continue

        header = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if header:
            if (current_file or old_file) and current_lines:
                hunks.append(
                    DiffHunk(
                        file_path=current_file or old_file or "",
                        old_file_path=old_file,
                        new_file_path=current_file,
                        start_line=start_line,
                        changed_lines=changed_lines,
                        old_changed_lines=old_changed_lines,
                        text="\n".join(current_lines),
                    )
                )
            current_lines = [line]
            changed_lines = set()
            old_changed_lines = set()
            old_line = int(header.group(1))
            start_line = int(header.group(2))
            new_line = start_line
            continue

        if not current_lines:
            continue

        current_lines.append(line)
        if line.startswith("+") and not line.startswith("+++"):
            changed_lines.add(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            old_changed_lines.add(old_line)
            old_line += 1
            continue
        else:
            old_line += 1
            new_line += 1

    if (current_file or old_file) and current_lines:
        hunks.append(
            DiffHunk(
                file_path=current_file or old_file or "",
                old_file_path=old_file,
                new_file_path=current_file,
                start_line=start_line,
                changed_lines=changed_lines,
                old_changed_lines=old_changed_lines,
                text="\n".join(current_lines),
            )
        )

    return hunks


def _diff_path(raw_path: str) -> str | None:
    if raw_path == "/dev/null":
        return None
    if raw_path.startswith("a/") or raw_path.startswith("b/"):
        return raw_path[2:]
    return raw_path


def _change_type_for_new_side(hunk: DiffHunk) -> str:
    if hunk.old_file_path is None:
        return "added"
    return "modified"


def _supported_languages() -> list[SourceLanguage]:
    return [
        SourceLanguage("python", "*.py"),
        SourceLanguage("javascript", "*.js"),
        SourceLanguage("javascript", "*.jsx"),
        SourceLanguage("typescript", "*.ts"),
        SourceLanguage("typescript", "*.tsx"),
        SourceLanguage("java", "*.java"),
        SourceLanguage("go", "*.go"),
        SourceLanguage("rust", "*.rs"),
        SourceLanguage("php", "*.php"),
        SourceLanguage("csharp", "*.cs"),
        SourceLanguage("kotlin", "*.kt"),
        SourceLanguage("kotlin", "*.kts"),
        SourceLanguage("lua", "*.lua"),
        SourceLanguage("c", "*.c"),
        SourceLanguage("cpp", "*.cpp"),
        SourceLanguage("cpp", "*.cc"),
        SourceLanguage("cpp", "*.cxx"),
        SourceLanguage("cpp", "*.hpp"),
        SourceLanguage("cpp", "*.h"),
        SourceLanguage("ruby", "*.rb"),
        SourceLanguage("swift", "*.swift"),
    ]


def _language_for_path(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    if suffix in {".js", ".jsx"}:
        return "javascript"
    if suffix == ".java":
        return "java"
    if suffix == ".go":
        return "go"
    if suffix == ".rs":
        return "rust"
    if suffix == ".php":
        return "php"
    if suffix == ".cs":
        return "csharp"
    if suffix in {".kt", ".kts"}:
        return "kotlin"
    if suffix == ".lua":
        return "lua"
    if suffix == ".c":
        return "c"
    if suffix in {".cpp", ".cc", ".cxx", ".hpp", ".h"}:
        return "cpp"
    if suffix == ".rb":
        return "ruby"
    if suffix == ".swift":
        return "swift"
    return "unknown"


def _function_spans(source: str, file_path: str) -> list[PythonFunctionSpan]:
    language = _language_for_path(file_path)
    module_name = Path(file_path).stem
    if language == "python":
        return _python_function_spans(source, module_name)
    if language in {"javascript", "typescript"}:
        return _javascript_function_spans(source, module_name)
    if language == "java":
        return _java_method_spans(source, module_name)
    if language == "go":
        return _go_function_spans(source, module_name)
    if language == "rust":
        return _rust_function_spans(source, module_name)
    if language == "php":
        return _php_function_spans(source, module_name)
    if language == "csharp":
        return _csharp_method_spans(source, module_name)
    if language == "kotlin":
        return _kotlin_function_spans(source, module_name)
    if language == "lua":
        return _lua_function_spans(source, module_name)
    if language in {"c", "cpp"}:
        return _cpp_function_spans(source, module_name)
    if language == "ruby":
        return _ruby_function_spans(source, module_name)
    if language == "swift":
        return _swift_function_spans(source, module_name)
    return []


def _python_function_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    tree = ast.parse(source)
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parent_class = _enclosing_class(tree, node)
        name_parts = [module_name]
        if parent_class:
            name_parts.append(parent_class.name)
        name_parts.append(node.name)
        spans.append(
            PythonFunctionSpan(
                qualified_name=".".join(name_parts),
                signature=_signature_from_line(lines[node.lineno - 1]),
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
            )
        )

    return spans


def _enclosing_class(tree: ast.AST, target: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for child in ast.walk(node):
            if child is target:
                return node
    return None


def _signature_from_line(line: str) -> str:
    return line.strip().rstrip(":")


def _javascript_function_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []
    patterns = [
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[^=]+)?=>\s*\{"),
    ]

    for index, line in enumerate(lines, start=1):
        match = next((pattern.match(line) for pattern in patterns if pattern.match(line)), None)
        if not match:
            continue
        spans.append(
            PythonFunctionSpan(
                qualified_name=f"{module_name}.{match.group(1)}",
                signature=_javascript_signature(line),
                start_line=index,
                end_line=_brace_span_end(lines, index),
            )
        )

    return spans


def _javascript_signature(line: str) -> str:
    stripped = line.strip()
    if "{" in stripped:
        stripped = stripped[: stripped.index("{")]
    return stripped.strip()


def _java_method_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []
    class_name = module_name
    class_pattern = re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+)?class\s+([A-Za-z_$][\w$]*)\b")
    method_pattern = re.compile(
        r"^\s*(?:(?:public|private|protected|static|final|synchronized|native|abstract)\s+)*"
        r"(?:<[^>]+>\s*)?"
        r"[\w$<>\[\], ?]+\s+"
        r"([A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?:throws\s+[^{]+)?\{"
    )

    for index, line in enumerate(lines, start=1):
        class_match = class_pattern.match(line)
        if class_match:
            class_name = class_match.group(1)
            continue

        method_match = method_pattern.match(line)
        if not method_match:
            continue
        method_name = method_match.group(1)
        if method_name in {"if", "for", "while", "switch", "catch", "return", "new"}:
            continue
        spans.append(
            PythonFunctionSpan(
                qualified_name=f"{class_name}.{method_name}",
                signature=_javascript_signature(line),
                start_line=index,
                end_line=_brace_span_end(lines, index),
            )
        )

    return spans


def _go_function_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []
    function_pattern = re.compile(
        r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)\s*\([^)]*\)\s*(?:[A-Za-z_*][\w*]*(?:\[[^\]]+\])?|\([^)]*\))?\s*\{"
    )

    for index, line in enumerate(lines, start=1):
        function_match = function_pattern.match(line)
        if not function_match:
            continue
        spans.append(
            PythonFunctionSpan(
                qualified_name=f"{module_name}.{function_match.group(1)}",
                signature=_javascript_signature(line),
                start_line=index,
                end_line=_brace_span_end(lines, index),
            )
        )

    return spans


def _rust_function_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []
    function_pattern = re.compile(
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)\s*(?:<[^>]+>)?\s*\([^)]*\)\s*(?:->\s*[^{]+)?\{"
    )

    for index, line in enumerate(lines, start=1):
        function_match = function_pattern.match(line)
        if not function_match:
            continue
        spans.append(
            PythonFunctionSpan(
                qualified_name=f"{module_name}.{function_match.group(1)}",
                signature=_javascript_signature(line),
                start_line=index,
                end_line=_brace_span_end(lines, index),
            )
        )

    return spans


def _php_function_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []
    class_name = module_name
    class_pattern = re.compile(r"^\s*(?:final\s+|abstract\s+)?class\s+([A-Za-z_]\w*)\b")
    function_pattern = re.compile(
        r"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*function\s+([A-Za-z_]\w*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\{"
    )

    for index, line in enumerate(lines, start=1):
        class_match = class_pattern.match(line)
        if class_match:
            class_name = class_match.group(1)
            continue

        function_match = function_pattern.match(line)
        if not function_match:
            continue
        spans.append(
            PythonFunctionSpan(
                qualified_name=f"{class_name}.{function_match.group(1)}",
                signature=_javascript_signature(line),
                start_line=index,
                end_line=_brace_span_end(lines, index),
            )
        )

    return spans


def _csharp_method_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []
    class_name = module_name
    class_pattern = re.compile(r"^\s*(?:public\s+|internal\s+|private\s+|protected\s+|abstract\s+|sealed\s+|static\s+)*class\s+([A-Za-z_]\w*)\b")
    method_pattern = re.compile(
        r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|async|sealed|abstract|partial)\s+)*"
        r"[\w<>\[\], ?]+\s+"
        r"([A-Za-z_]\w*)\s*\([^;]*\)\s*$"
    )

    for index, line in enumerate(lines, start=1):
        class_match = class_pattern.match(line)
        if class_match:
            class_name = class_match.group(1)
            continue

        method_match = method_pattern.match(line)
        if not method_match:
            continue
        method_name = method_match.group(1)
        if method_name in {"if", "for", "while", "switch", "catch", "using", "lock", "return", "new"}:
            continue
        spans.append(
            PythonFunctionSpan(
                qualified_name=f"{class_name}.{method_name}",
                signature=line.strip(),
                start_line=index,
                end_line=_brace_span_end(lines, index),
            )
        )

    return spans


def _kotlin_function_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []
    class_name: str | None = None
    class_pattern = re.compile(r"^\s*(?:data\s+|sealed\s+|open\s+|abstract\s+)?class\s+([A-Za-z_]\w*)\b")
    function_pattern = re.compile(
        r"^\s*(?:(?:public|private|protected|internal|open|override|suspend|inline|tailrec|operator)\s+)*fun\s+(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)\s*(?:<[^>]+>)?\s*\([^)]*\)\s*(?::\s*[^{=]+)?\{"
    )

    for index, line in enumerate(lines, start=1):
        class_match = class_pattern.match(line)
        if class_match:
            class_name = class_match.group(1)
            continue

        function_match = function_pattern.match(line)
        if not function_match:
            continue
        owner = class_name or module_name
        spans.append(
            PythonFunctionSpan(
                qualified_name=f"{owner}.{function_match.group(1)}",
                signature=_javascript_signature(line),
                start_line=index,
                end_line=_brace_span_end(lines, index),
            )
        )

    return spans


def _lua_function_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []
    patterns = [
        re.compile(r"^\s*local\s+function\s+([A-Za-z_]\w*)\s*\("),
        re.compile(r"^\s*function\s+([A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)*)\s*\("),
        re.compile(r"^\s*(?:local\s+)?([A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)*)\s*=\s*function\s*\("),
    ]

    for index, line in enumerate(lines, start=1):
        function_match = next((pattern.match(line) for pattern in patterns if pattern.match(line)), None)
        if not function_match:
            continue
        function_name = _lua_qualified_function_name(module_name, function_match.group(1))
        spans.append(
            PythonFunctionSpan(
                qualified_name=function_name,
                signature=line.strip(),
                start_line=index,
                end_line=_lua_span_end(lines, index),
            )
        )

    return spans


def _lua_qualified_function_name(module_name: str, raw_name: str) -> str:
    normalized = raw_name.replace(":", ".")
    if normalized.startswith("_M."):
        normalized = normalized.removeprefix("_M.")
    if "." in normalized:
        return f"{module_name}.{normalized.split('.')[-1]}"
    return f"{module_name}.{normalized}"


def _cpp_function_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []
    function_pattern = re.compile(
        r"^\s*(?:template\s*<[^>]+>\s*)?"
        r"[\w:<>~*&\s]+\s+"
        r"(?:(\w+)::)?([A-Za-z_~]\w*)\s*\([^;]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:->\s*[^{]+)?\{"
    )

    for index, line in enumerate(lines, start=1):
        function_match = function_pattern.match(line)
        if not function_match:
            continue
        owner = function_match.group(1) or module_name
        function_name = function_match.group(2)
        if function_name in {"if", "for", "while", "switch", "catch", "return", "sizeof"}:
            continue
        spans.append(
            PythonFunctionSpan(
                qualified_name=f"{owner}.{function_name}",
                signature=_javascript_signature(line),
                start_line=index,
                end_line=_brace_span_end(lines, index),
            )
        )

    return spans


def _ruby_function_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []
    class_name = module_name
    class_pattern = re.compile(r"^\s*class\s+([A-Za-z_]\w*)\b")
    function_pattern = re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[!?=]?)\s*(?:\([^)]*\)|[^#]*)?")

    for index, line in enumerate(lines, start=1):
        class_match = class_pattern.match(line)
        if class_match:
            class_name = class_match.group(1)
            continue

        function_match = function_pattern.match(line)
        if not function_match:
            continue
        spans.append(
            PythonFunctionSpan(
                qualified_name=f"{class_name}.{function_match.group(1)}",
                signature=line.strip(),
                start_line=index,
                end_line=_ruby_span_end(lines, index),
            )
        )

    return spans


def _swift_function_spans(source: str, module_name: str) -> list[PythonFunctionSpan]:
    lines = source.splitlines()
    spans: list[PythonFunctionSpan] = []
    type_name: str | None = None
    type_pattern = re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|open\s+|final\s+)?(?:class|struct|actor|enum)\s+([A-Za-z_]\w*)\b")
    function_pattern = re.compile(
        r"^\s*(?:(?:public|private|fileprivate|internal|open|static|class|final|override|mutating|nonmutating|async)\s+)*func\s+([A-Za-z_]\w*)\s*(?:<[^>]+>)?\s*\([^)]*\)\s*(?:async\s*)?(?:throws\s*)?(?:->\s*[^{]+)?\{"
    )

    for index, line in enumerate(lines, start=1):
        type_match = type_pattern.match(line)
        if type_match:
            type_name = type_match.group(1)
            continue

        function_match = function_pattern.match(line)
        if not function_match:
            continue
        owner = type_name or module_name
        spans.append(
            PythonFunctionSpan(
                qualified_name=f"{owner}.{function_match.group(1)}",
                signature=_javascript_signature(line),
                start_line=index,
                end_line=_brace_span_end(lines, index),
            )
        )

    return spans


def _brace_span_end(lines: list[str], start_line: int) -> int:
    depth = 0
    seen_opening_brace = False
    for index in range(start_line, len(lines) + 1):
        line = lines[index - 1]
        depth += line.count("{")
        if "{" in line:
            seen_opening_brace = True
        depth -= line.count("}")
        if seen_opening_brace and depth <= 0:
            return index
    return start_line


def _ruby_span_end(lines: list[str], start_line: int) -> int:
    depth = 0
    for index in range(start_line, len(lines) + 1):
        stripped = lines[index - 1].strip()
        if re.match(r"^(def|class|module|if|unless|case|begin|while|until|for)\b", stripped) or stripped.endswith(" do"):
            depth += 1
        if stripped == "end":
            depth -= 1
            if depth <= 0:
                return index
    return start_line


def _lua_span_end(lines: list[str], start_line: int) -> int:
    depth = 0
    for index in range(start_line, len(lines) + 1):
        stripped = _strip_lua_comment(lines[index - 1])
        if not stripped:
            continue
        depth += len(re.findall(r"\bfunction\b", stripped))
        if re.search(r"\b(then|do|repeat)\b", stripped):
            depth += 1
        if re.match(r"^until\b", stripped):
            depth -= 1
        if re.match(r"^end\b", stripped):
            depth -= 1
            if depth <= 0:
                return index
    return start_line


def _strip_lua_comment(line: str) -> str:
    return line.split("--", 1)[0].strip()
