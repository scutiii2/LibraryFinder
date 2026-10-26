"""AST-only scanning for Python utility libraries."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core.models import Catalog, FileRecord, MethodRecord, ScanError

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Subscript):
        return _name(node.value)
    return ast.unparse(node)


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = (root.name, *relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(tree: ast.Module) -> tuple[str, ...]:
    values: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            values.extend(f"{prefix}.{alias.name}".rstrip(".") for alias in node.names)
    return tuple(values)


def _effects(node: ast.AST, imports: tuple[str, ...]) -> tuple[str, ...]:
    text = " ".join([*imports, *(ast.unparse(item) for item in ast.walk(node) if isinstance(item, ast.Call))]).lower()
    effects: list[str] = []
    for label, terms in {
        "webdriver": ("selenium", "webdriver", "find_element", "webdrivermanager"),
        "filesystem": ("pathlib", "open(", ".write", ".read", "json.dump", "json.load"),
        "network": ("urlopen", "requests", "http"),
        "environment": ("getenv", "load_dotenv", "environ"),
        "report": ("report", "json.dump", "write_report"),
    }.items():
        if any(term in text for term in terms):
            effects.append(label)
    return tuple(effects)


def _keywords(*texts: str | None) -> tuple[str, ...]:
    words = {word.lower() for text in texts if text for word in _WORD.findall(text)}
    return tuple(sorted(words))


def _parameters(arguments: ast.arguments) -> tuple[str, ...]:
    positional = [*arguments.posonlyargs, *arguments.args]
    names = [argument.arg for argument in positional]
    if arguments.vararg:
        names.append(f"*{arguments.vararg.arg}")
    names.extend(argument.arg for argument in arguments.kwonlyargs)
    if arguments.kwarg:
        names.append(f"**{arguments.kwarg.arg}")
    return tuple(names)


def _method_records(
    node: ast.ClassDef | ast.Module,
    module: str,
    relative_path: str,
    imports: tuple[str, ...],
    owner: str | None = None,
    bases: tuple[str, ...] = (),
    content: str = "",
) -> list[MethodRecord]:
    records: list[MethodRecord] = []
    for child in node.body:
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if child.name.startswith("__") and child.name.endswith("__"):
            continue
        method_owner = owner
        name = f"{module}.{method_owner}.{child.name}" if method_owner else f"{module}.{child.name}"
        docstring = ast.get_docstring(child)
        source = ast.get_source_segment(content, child) or ""
        fingerprint = hashlib.sha256(json.dumps({"name": name, "parameters": _parameters(child.args), "return": ast.unparse(child.returns) if child.returns else None, "decorators": tuple(_name(item) for item in child.decorator_list), "docstring": docstring, "imports": imports, "effects": _effects(child, imports), "source": source}, sort_keys=True).encode()).hexdigest()
        records.append(
            MethodRecord(
                qualified_name=name,
                module=module,
                file=relative_path,
                line=child.lineno,
                visibility="internal" if child.name.startswith("_") else "public",
                owner_class=method_owner,
                base_classes=bases,
                parameters=_parameters(child.args),
                return_annotation=ast.unparse(child.returns) if child.returns else None,
                decorators=tuple(_name(item) for item in child.decorator_list),
                docstring=docstring,
                imports=imports,
                keywords=_keywords(name, docstring, *imports),
                effects=_effects(child, imports),
                source=source[:12000],
                fingerprint=fingerprint,
            )
        )
    return records


def scan_library(source_root: Path) -> Catalog:
    """Parse `source_root` without importing or executing any source module."""
    source_root = source_root.resolve()
    files: list[FileRecord] = []
    methods: list[MethodRecord] = []
    errors: list[ScanError] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        try:
            raw_content = path.read_bytes()
            content = raw_content.decode("utf-8")
            tree = ast.parse(content, filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as error:
            errors.append(ScanError(relative, str(error)))
            continue
        files.append(FileRecord(relative, hashlib.sha256(raw_content).hexdigest()))
        module = _module_name(source_root, path)
        imports = _imports(tree)
        methods.extend(_method_records(tree, module, relative, imports, content=content))
        for child in tree.body:
            if isinstance(child, ast.ClassDef):
                bases = tuple(_name(base) for base in child.bases)
                methods.extend(_method_records(child, module, relative, imports, child.name, bases, content))
    return Catalog(
        version=Catalog.CURRENT_VERSION,
        source_root=str(source_root),
        files=tuple(files),
        methods=tuple(methods),
        errors=tuple(errors),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
