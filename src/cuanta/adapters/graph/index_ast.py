from __future__ import annotations

import ast
import json
import posixpath
import unicodedata
from collections.abc import Iterator
from dataclasses import replace
from pathlib import PurePosixPath

import tree_sitter_go
import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from cuanta.domain.code_index import IndexedFile, IndexRow, IndexStructure

_SUFFIXES = ("", ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".d.ts")
_FUNCTIONS = frozenset({"function_declaration", "method_declaration", "method_definition"})
_TYPES = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "type_spec",
        "type_alias",
    }
)


def _nodes(node: Node) -> Iterator[Node]:
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.named_children))


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", errors="replace") if node and node.text else ""


def _literal(node: Node | None) -> str:
    value = _text(node)
    return value[1:-1] if len(value) >= 2 and value[0] in "\"'`" else ""


def _row(
    file: IndexedFile,
    name: str,
    line: int,
    end_line: int,
    relation: str,
    target: str = "",
    *,
    edge: bool = False,
) -> IndexRow:
    identity = (
        f"{file.path}:{line}:{relation}:{target}:{name}" if edge else f"{file.path}:{line}:{name}"
    )
    return IndexRow(
        identity,
        file.path,
        file.content_hash,
        "ast",
        text=name,
        line=line,
        end_line=end_line,
        target=target,
        relation=relation,
    )


def _unique(rows: list[IndexRow]) -> tuple[IndexRow, ...]:
    found: dict[str, IndexRow] = {}
    for row in rows:
        found.setdefault(row.id, row)
    return tuple(found.values())


def _resolve(base: str, paths: tuple[str, ...]) -> str:
    normalized = posixpath.normpath(base)
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        return ""
    known = frozenset(paths)
    for suffix in _SUFFIXES:
        candidate = normalized + suffix
        if candidate in known:
            return candidate
    for filename in ("index.ts", "index.tsx", "index.js", "index.jsx", "__init__.py"):
        candidate = normalized + "/" + filename
        if candidate in known:
            return candidate
    return ""


def _import_target(file: IndexedFile, module: str, paths: tuple[str, ...]) -> str:
    directory = posixpath.dirname(file.path)
    if module.startswith("."):
        found = _resolve(posixpath.join(directory, module), paths)
    elif module.startswith("@/"):
        found = _resolve("src/" + module[2:], paths) or _resolve(module[2:], paths)
    else:
        found = ""
    return found or "module:" + module


def resolve_import(path: str, module: str, paths: tuple[str, ...]) -> str:
    file = IndexedFile(path, "", "", 0)
    if path.endswith(".py"):
        level = len(module) - len(module.lstrip("."))
        name = module.lstrip(".")
        target = _python_target(file, name, level, paths)
        if target.startswith("module:") and "." in name:
            target = _python_target(file, name.rsplit(".", 1)[0], level, paths)
        return target if not target.startswith("module:") else "module:" + module
    target = _import_target(file, module, paths)
    if path.endswith(".go") and target.startswith("module:"):
        return _go_target(module, paths)
    return target


def _import_symbol(
    file: IndexedFile, module: str, line: int, end: int, relation: str = "import"
) -> IndexRow:
    return replace(
        _row(file, module, line, end, relation, "module:" + module),
        id=f"{file.path}:{line}:{relation}:{module}",
    )


def _python_target(file: IndexedFile, module: str, level: int, paths: tuple[str, ...]) -> str:
    name = module.replace(".", "/")
    if level:
        directory = posixpath.dirname(file.path)
        if level > len(PurePosixPath(directory).parts) + 1:
            return "module:" + "." * level + module
        for _ in range(level - 1):
            directory = posixpath.dirname(directory)
        found = _resolve(posixpath.join(directory, name), paths)
    else:
        found = _resolve("src/" + name, paths) or _resolve(name, paths)
    return found or "module:" + "." * level + module


def _symbol_kind(name: str, node: Node, file: IndexedFile, creators: frozenset[str]) -> str:
    value = node.child_by_field_name("value")
    if value and value.type == "call_expression":
        called = value.child_by_field_name("function")
        while called and called.type == "call_expression":
            called = called.child_by_field_name("function")
        if _text(called) in creators:
            return "store"
    if name.startswith("use") and len(name) > 3 and name[3].isupper():
        return "hook"
    if name and name[0].isupper() and any(child.type.startswith("jsx_") for child in _nodes(node)):
        return "component"
    if "services" in PurePosixPath(file.path).parts or name.endswith("Service"):
        return "service"
    if node.type in _TYPES:
        return "class" if node.type == "class_declaration" else "type"
    if node.type in _FUNCTIONS or (
        value and value.type in {"arrow_function", "function_expression"}
    ):
        return "function"
    return "variable"


def _creators(root: Node) -> frozenset[str]:
    names: set[str] = set()
    for node in _nodes(root):
        if node.type != "import_statement":
            continue
        if _literal(node.child_by_field_name("source")) not in {"zustand", "zustand/vanilla"}:
            continue
        for imported in _nodes(node):
            if imported.type == "import_specifier":
                name = _text(imported.child_by_field_name("name"))
                if name in {"create", "createStore"}:
                    names.add(_text(imported.child_by_field_name("alias")) or name)
            elif imported.type == "import_clause":
                names.update(
                    _text(child) for child in imported.named_children if child.type == "identifier"
                )
    return frozenset(names)


def _route(file: IndexedFile) -> IndexRow | None:
    path = PurePosixPath(file.path)
    if "app" not in path.parts or path.stem not in {"page", "layout", "route"}:
        return None
    start = path.parts.index("app") + 1
    segments = tuple(part for part in path.parts[start:-1] if not part.startswith(("(", "@")))
    route = "/" + "/".join(segments)
    return replace(
        _row(file, path.stem, 1, 1, "route", route), id=f"{file.path}:1:route:{path.stem}"
    )


def _tree_structure(
    file: IndexedFile, text: str, paths: tuple[str, ...], parser: Parser
) -> IndexStructure:
    parse_source = text + "\n" if file.language == "go" and not text.endswith("\n") else text
    root = parser.parse(parse_source.encode("utf-8")).root_node
    if root.has_error:
        return IndexStructure(coverage="reduced")
    symbols: list[IndexRow] = []
    edges: list[IndexRow] = []
    creators = _creators(root)
    for node in _nodes(root):
        line, end = node.start_point[0] + 1, node.end_point[0] + 1
        if node.type in _FUNCTIONS | _TYPES | {"variable_declarator"}:
            name = _text(node.child_by_field_name("name"))
            if name and "\n" not in name and len(name) <= 120:
                symbols.append(
                    _row(file, name, line, end, _symbol_kind(name, node, file, creators))
                )
        if node.type in {"import_statement", "export_statement", "import_spec"}:
            source = node.child_by_field_name("source") or node.child_by_field_name("path")
            module = _literal(source)
            if module:
                target = resolve_import(file.path, module, paths)
                relation = "exports" if node.type == "export_statement" else "imports"
                edges.append(_row(file, module, line, end, relation, target, edge=True))
                declaration_kind = "reexport" if relation == "exports" else "import"
                symbols.append(_import_symbol(file, module, line, end, declaration_kind))
        if node.type == "export_statement":
            declaration = node.child_by_field_name("declaration")
            if declaration:
                for exported in _nodes(declaration):
                    if exported.type not in _FUNCTIONS | _TYPES | {"variable_declarator"}:
                        continue
                    if declaration not in (exported, exported.parent):
                        continue
                    name = _text(exported.child_by_field_name("name"))
                    if name:
                        identity = f"{file.path}:{exported.start_point[0] + 1}:{name}"
                        edges.append(_row(file, name, line, end, "exports", identity, edge=True))
            for exported in _nodes(node):
                if exported.type == "export_specifier":
                    name = _text(exported.child_by_field_name("name"))
                    edges.append(
                        _row(file, name, line, end, "exports", "symbol:" + name, edge=True)
                    )
        if node.type == "member_expression":
            owner = node.child_by_field_name("object")
            if (
                owner
                and owner.type == "member_expression"
                and _text(owner.child_by_field_name("object")) == "process"
                and _text(owner.child_by_field_name("property")) == "env"
            ):
                name = _text(node.child_by_field_name("property"))
                if name:
                    edges.append(_row(file, name, line, end, "env", "env:" + name, edge=True))
    route = _route(file) if file.language in {"typescript", "javascript", "tsx", "jsx"} else None
    if route:
        symbols.append(route)
    if file.language == "go":
        go_symbols, go_edges = _go_exports(file, root)
        symbols.extend(go_symbols)
        edges.extend(go_edges)
    return IndexStructure(_unique(symbols), _unique(edges), "ast")


def _go_package_node(node: Node) -> bool:
    parent = node.parent
    while parent:
        if parent.type == "source_file":
            return True
        if parent.type not in {"type_declaration", "var_declaration", "const_declaration"}:
            return False
        parent = parent.parent
    return False


def _go_exports(file: IndexedFile, root: Node) -> tuple[list[IndexRow], list[IndexRow]]:
    symbols: list[IndexRow] = []
    edges: list[IndexRow] = []
    for node in _nodes(root):
        if node.type not in {
            "function_declaration",
            "method_declaration",
            "type_spec",
            "type_alias",
            "var_spec",
            "const_spec",
        }:
            continue
        names = tuple(node.children_by_field_name("name"))
        line, end = node.start_point[0] + 1, node.end_point[0] + 1
        for identifier in names:
            name = _text(identifier)
            if node.type in {"var_spec", "const_spec"}:
                symbols.append(_row(file, name, line, end, "variable"))
            if (
                name
                and unicodedata.category(name[0]) == "Lu"
                and (node.type == "method_declaration" or _go_package_node(node))
            ):
                target = f"{file.path}:{line}:{name}"
                edges.append(_row(file, name, line, end, "exports", target, edge=True))
    return symbols, edges


def _go_target(module: str, paths: tuple[str, ...]) -> str:
    directories = {posixpath.dirname(path) for path in paths if path.endswith(".go")}
    matches = tuple(
        directory for directory in sorted(directories) if module.endswith("/" + directory)
    )
    if len(matches) == 1:
        candidates = tuple(
            path for path in paths if posixpath.dirname(path) == matches[0] and path.endswith(".go")
        )
        if len(candidates) == 1:
            return candidates[0]
    return "module:" + module


def _python_structure(file: IndexedFile, text: str, paths: tuple[str, ...]) -> IndexStructure:
    try:
        root = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return IndexStructure(coverage="reduced")
    symbols: list[IndexRow] = []
    edges: list[IndexRow] = []
    for node in ast.walk(root):
        line = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", line) or line
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            if "services" in PurePosixPath(file.path).parts or node.name.endswith("Service"):
                kind = "service"
            symbols.append(_row(file, node.name, line, end, kind))
        if isinstance(node, ast.Import):
            for imported in node.names:
                target = resolve_import(file.path, imported.name, paths)
                edges.append(_row(file, imported.name, line, end, "imports", target, edge=True))
                symbols.append(_import_symbol(file, imported.name, line, end))
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for imported in node.names:
                qualified = module + "." + imported.name if module else imported.name
                reference = "." * node.level + qualified
                target = resolve_import(file.path, reference, paths)
                edges.append(_row(file, reference, line, end, "imports", target, edge=True))
                symbols.append(_import_symbol(file, reference, line, end))
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target_node in targets:
                for binding in ast.walk(target_node):
                    if isinstance(binding, ast.Name) and isinstance(binding.ctx, ast.Store):
                        symbols.append(_row(file, binding.id, line, end, "variable"))
    edges.extend(_python_exports(file, root, symbols))
    return IndexStructure(_unique(symbols), _unique(edges), "ast")


def _bindings(node: ast.stmt) -> tuple[str, ...]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return (node.name,)
    if isinstance(node, ast.Import):
        return tuple(alias.asname or alias.name.split(".")[0] for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return tuple(alias.asname or alias.name for alias in node.names if alias.name != "*")
    if isinstance(node, ast.Assign | ast.AnnAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return tuple(
            binding.id
            for target in targets
            for binding in ast.walk(target)
            if isinstance(binding, ast.Name) and isinstance(binding.ctx, ast.Store)
        )
    return ()


def _literal_all(root: ast.Module) -> tuple[str, ...] | None:
    assigned = False
    names: tuple[str, ...] = ()
    known_stores: set[int] = set()
    for node in root.body:
        if "__all__" not in _bindings(node):
            continue
        assigned = True
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        known_stores.update(
            id(binding)
            for target in targets
            for binding in ast.walk(target)
            if isinstance(binding, ast.Name)
            and binding.id == "__all__"
            and isinstance(binding.ctx, ast.Store)
        )
        value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
        if not isinstance(value, ast.List | ast.Tuple):
            return ()
        if any(
            not isinstance(item, ast.Constant) or not isinstance(item.value, str)
            for item in value.elts
        ):
            return ()
        names = tuple(
            item.value
            for item in value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
    for candidate in ast.walk(root):
        if (
            isinstance(candidate, ast.Name)
            and isinstance(candidate.ctx, ast.Store | ast.Del)
            and candidate.id == "__all__"
            and id(candidate) not in known_stores
        ):
            return ()
        if (
            isinstance(candidate, ast.Subscript)
            and isinstance(candidate.ctx, ast.Store | ast.Del)
            and isinstance(candidate.value, ast.Name)
            and candidate.value.id == "__all__"
        ):
            return ()
        if (
            isinstance(candidate, ast.AugAssign)
            and isinstance(candidate.target, ast.Name)
            and candidate.target.id == "__all__"
        ):
            return ()
        if isinstance(candidate, ast.Call) and isinstance(candidate.func, ast.Attribute):
            owner = candidate.func.value
            if isinstance(owner, ast.Name) and owner.id == "__all__":
                return ()
    return names if assigned else None


def _python_exports(file: IndexedFile, root: ast.Module, symbols: list[IndexRow]) -> list[IndexRow]:
    bindings = {name: node for node in root.body for name in _bindings(node)}
    explicit = _literal_all(root)
    names = (
        explicit
        if explicit is not None
        else tuple(name for name in bindings if not name.startswith("_"))
    )
    edges: list[IndexRow] = []
    for name in names:
        declaration = bindings.get(name)
        line = declaration.lineno if declaration else 1
        end = (declaration.end_lineno or line) if declaration else 1
        target = next(
            (row.id for row in symbols if row.text == name and row.line == line), "symbol:" + name
        )
        edges.append(_row(file, name, line, end, "exports", target, edge=True))
    return edges


def _manifest(file: IndexedFile, text: str) -> IndexStructure:
    try:
        manifest: object = json.loads(text)
    except (ValueError, TypeError):
        return IndexStructure(coverage="reduced")
    if not isinstance(manifest, dict):
        return IndexStructure(coverage="reduced")
    scripts = manifest.get("scripts")
    if not isinstance(scripts, dict):
        return IndexStructure(coverage="manifest")
    symbols: list[IndexRow] = []
    edges: list[IndexRow] = []
    for name, command in sorted(scripts.items()):
        if not isinstance(name, str) or not isinstance(command, str):
            continue
        symbols.append(_row(file, "npm run " + name, 1, 1, "script", command))
        if name.split(":", 1)[0] in {
            "test",
            "lint",
            "build",
            "check",
            "typecheck",
            "type-check",
            "verify",
        }:
            edges.append(_row(file, command, 1, 1, "verifies", "npm run " + name, edge=True))
    return IndexStructure(tuple(symbols), tuple(edges), "manifest")


class AstIndexExtractor:
    def __init__(self) -> None:
        self._parsers = {
            "typescript": Parser(Language(tree_sitter_typescript.language_typescript())),
            "tsx": Parser(Language(tree_sitter_typescript.language_tsx())),
            "javascript": Parser(Language(tree_sitter_javascript.language())),
            "jsx": Parser(Language(tree_sitter_javascript.language())),
            "go": Parser(Language(tree_sitter_go.language())),
        }

    def resolve(self, path: str, module: str, paths: tuple[str, ...]) -> str:
        return resolve_import(path, module, paths)

    def extract(self, file: IndexedFile, text: str, paths: tuple[str, ...]) -> IndexStructure:
        if PurePosixPath(file.path).name == "package.json":
            return _manifest(file, text)
        if file.language == "python":
            return _python_structure(file, text, paths)
        language = file.language
        if PurePosixPath(file.path).suffix == ".tsx":
            language = "tsx"
        elif PurePosixPath(file.path).suffix == ".jsx":
            language = "jsx"
        parser = self._parsers.get(language)
        return _tree_structure(file, text, paths, parser) if parser else IndexStructure()
