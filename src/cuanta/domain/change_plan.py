from __future__ import annotations

import fnmatch
import re
from dataclasses import asdict, dataclass, replace

from cuanta.domain.code_index import IndexedFile, IndexRow, index_path
from cuanta.domain.index_search import rank_files, search_terms
from cuanta.domain.intake import READ_ONLY_AT, core_text, extract_mentions, extract_out_of_scope
from cuanta.domain.mandate import INVESTIGATION, MandateRequest

WRITERS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
EXECUTION = ("Bash", "PowerShell", "Computer", "ComputerUse")
READ_TOOLS = ("Read", "Grep", "Glob", "Agent", "Task")
_PATH = re.compile(r"[\w.@*?-]+(?:/[\w.@*?-]+)+|[\w@*?-]+\.[\w*?-]+(?:/\*\*)?")
_READ_ONLY_SCOPE_SUFFIX = re.compile(
    r"\s+(?:else|outside|except|beyond|in|under|inside|within|to|en|fuera|salvo|excepto|m[aá]s)\b",
    re.IGNORECASE,
)
_GENERIC = frozenset(
    [
        "fix",
        "bug",
        "feature",
        "add",
        "change",
        "improve",
        "implement",
        "update",
        "refactor",
        "investigate",
        "audit",
        "explain",
        "the",
        "this",
        "that",
        "with",
        "from",
        "for",
        "and",
        "only",
        "not",
        "sin",
        "no",
        "una",
        "uno",
        "los",
        "las",
        "del",
        "para",
        "con",
        "arreglar",
        "corregir",
        "crear",
        "agregar",
        "cambiar",
        "mejorar",
        "investigar",
        "analizar",
        "why",
        "what",
        "where",
        "should",
        "expected",
        "tests",
        "test",
        "regression",
        "source",
        "file",
        "files",
        "code",
        "codigo",
        "archivo",
        "archivos",
        "please",
        "por",
        "favor",
        "none",
        "stated",
        "unknown",
    ]
)


@dataclass(frozen=True, slots=True)
class EditTarget:
    path: str
    confidence: float
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ChangePlan:
    edit: tuple[EditTarget, ...] = ()
    read: tuple[str, ...] = ()
    guard: tuple[str, ...] = ()
    verify: tuple[str, ...] = ()
    read_only: bool = False
    coverage: float = 0.0


def path_matches(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/").removeprefix("./")
    pattern = pattern.replace("\\", "/").removeprefix("./")
    if "/" not in pattern:
        return fnmatch.fnmatchcase(normalized.rsplit("/", 1)[-1], pattern)
    parts = tuple(normalized.split("/"))
    patterns = tuple(pattern.split("/"))
    if len(patterns) == 2 and patterns[1] == "**":
        patterns = ("**", *patterns)
    pending = [(0, 0)]
    visited: set[tuple[int, int]] = set()
    while pending:
        source, target = pending.pop()
        if (source, target) in visited:
            continue
        visited.add((source, target))
        if target == len(patterns):
            if source == len(parts):
                return True
            continue
        if patterns[target] == "**":
            pending.append((source, target + 1))
            if source < len(parts):
                pending.append((source + 1, target))
        elif source < len(parts) and fnmatch.fnmatchcase(parts[source], patterns[target]):
            pending.append((source + 1, target + 1))
    return False


def guarded(path: str, plan: ChangePlan) -> bool:
    return plan.read_only or any(path_matches(path, pattern) for pattern in plan.guard)


def _scope(value: str) -> str:
    result = index_path(value.strip().strip("`\"'"))
    if any(char in result for char in "()[]{}\n\r"):
        raise ValueError("Invalid plan path")
    return result


def _mentions(text: str, paths: tuple[str, ...], keep_unknown: bool = False) -> tuple[str, ...]:
    found: set[str] = set()
    normalized = text.replace("\\", "/")
    tokens = (*extract_mentions(normalized), *_PATH.findall(normalized))
    for raw_token in tokens:
        matched = False
        token = raw_token.rstrip(".,;:").removeprefix("./")
        if ":" in token or ".." in token.split("/"):
            continue
        plain = token.removesuffix("/**").rstrip("/")
        for path in paths:
            if path == plain or path.endswith("/" + plain):
                found.add(path)
                matched = True
            elif "/" + plain + "/" in "/" + path:
                prefix = path[: path.index(plain) + len(plain)]
                found.add(prefix + "/**")
                matched = True
            elif "*" in token and path_matches(path, token):
                found.add(token)
                matched = True
        if "*" in token or (keep_unknown and not matched and ("/" in token or "." in token)):
            try:
                found.add(_scope(token))
            except ValueError:
                continue
    return tuple(sorted(found))


def _exclusions(request: MandateRequest, files: tuple[IndexedFile, ...]) -> tuple[str, ...]:
    paths = tuple(file.path for file in files)
    text = " ".join(
        (
            request.out_of_scope,
            *extract_out_of_scope(request.what),
            *extract_out_of_scope(request.why),
            *extract_out_of_scope(request.constraints),
        )
    )
    found = set(_mentions(text, paths, keep_unknown=True))
    terms = set(search_terms(text))
    if terms & {"renderer", "renderizador", "3d", "three"}:
        found.update(
            path for path in paths if set(search_terms(path)) & {"renderer", "3d", "three"}
        )
    return tuple(sorted(found))


def compile_change_plan(
    request: MandateRequest,
    files: tuple[IndexedFile, ...],
    symbols: tuple[IndexRow, ...],
    edges: tuple[IndexRow, ...],
    notes: tuple[IndexRow, ...],
    rules: tuple[IndexRow, ...],
    history: tuple[IndexRow, ...],
    tests: tuple[IndexRow, ...],
    commands: tuple[str, ...] = (),
    now: str = "",
) -> ChangePlan:
    paths = tuple(sorted(file.path for file in files))
    protection = _exclusions(request, files)
    text = " ".join(core_text(value) for value in (request.what, request.why, request.where))
    query = " ".join(term for term in search_terms(text) if term not in _GENERIC)
    hits = rank_files(files, symbols, edges, notes, rules, history, query, request.type, now, 15)
    explicit = _mentions(text, paths, keep_unknown=True)
    selected: dict[str, EditTarget] = {}
    read: set[str] = set()
    for pattern in explicit:
        if pattern not in paths and "*" not in pattern:
            selected[pattern] = EditTarget(pattern, 0.8, "explicit new file path")
        for path in paths:
            if path_matches(path, pattern):
                selected[path] = EditTarget(path, 1.0, "explicit request path")
    for hit in hits:
        read.add(hit.path)
        if hit.matched_terms and len(selected) < 6:
            selected.setdefault(
                hit.path,
                EditTarget(
                    hit.path, 0.75 if len(hit.matched_terms) > 1 else 0.55, "; ".join(hit.reasons)
                ),
            )
    selected = {
        path: item
        for path, item in selected.items()
        if not any(path_matches(path, pattern) for pattern in protection)
    }
    current = {file.path: file for file in files}
    verify = list(commands)
    for row in tests:
        if (
            row.target in selected
            and row.path in current
            and not row.stale
            and row.source_hash == current[row.path].content_hash
        ):
            read.add(row.path)
            verify.extend(line.strip() for line in row.text.splitlines() if line.strip())
            if request.type != INVESTIGATION and not any(
                path_matches(row.path, pattern) for pattern in protection
            ):
                selected.setdefault(row.path, EditTarget(row.path, 0.7, "linked regression tests"))
    readonly = request.type == INVESTIGATION or any(
        _global_readonly(value)
        for value in (
            request.what,
            request.why,
            request.where,
            request.constraints,
            request.out_of_scope,
        )
    )
    if readonly:
        read.update(selected)
        selected.clear()
    read.difference_update(selected)
    return ChangePlan(
        tuple(selected[path] for path in sorted(selected)),
        tuple(sorted(read)),
        protection,
        tuple(dict.fromkeys(value for value in verify if value)),
        readonly,
        sum(file.coverage != "inventory" for file in files) / len(files) if files else 0.0,
    )


def _global_readonly(value: str) -> bool:
    return any(
        not _READ_ONLY_SCOPE_SUFFIX.match(value[matched.end() :])
        for matched in READ_ONLY_AT.finditer(value)
    )


def move_plan(plan: ChangePlan, path: str, role: str) -> ChangePlan:
    path = _scope(path)
    if role not in {"edit", "read", "guard"}:
        raise ValueError("Unknown plan role")
    edit = tuple(item for item in plan.edit if item.path != path)
    read = {item for item in plan.read if item != path}
    guard = tuple(item for item in plan.guard if item != path)
    if role == "edit" and (plan.read_only or any(_overlap(path, item) for item in guard)):
        role = "read"
    if role == "edit":
        edit = (*edit, EditTarget(path, 1.0, "explicit user selection"))
    elif role == "read":
        read.add(path)
    else:
        guard = (*guard, path)
    protected = {
        item.path
        for item in edit
        if plan.read_only or any(_overlap(item.path, value) for value in guard)
    }
    read.update(protected)
    edit = tuple(item for item in edit if item.path not in protected)
    return replace(
        plan,
        edit=tuple(sorted(edit, key=lambda item: item.path)),
        read=tuple(sorted(read)),
        guard=tuple(sorted(guard)),
    )


def _overlap(path: str, pattern: str) -> bool:
    if not any(char in path for char in "*?"):
        return path_matches(path, pattern)
    left = _pattern_parts(path)
    right = _pattern_parts(pattern)
    pending = [(0, 0)]
    visited: set[tuple[int, int]] = set()
    while pending:
        first, second = pending.pop()
        if (first, second) in visited:
            continue
        visited.add((first, second))
        if first == len(left) and second == len(right):
            return True
        if first < len(left) and left[first] == "**":
            pending.append((first + 1, second))
            if second < len(right):
                pending.append((first, second + 1))
        elif second < len(right) and right[second] == "**":
            pending.append((first, second + 1))
            if first < len(left):
                pending.append((first + 1, second))
        elif (
            first < len(left)
            and second < len(right)
            and _segment_overlap(left[first], right[second])
        ):
            pending.append((first + 1, second + 1))
    return False


def _pattern_parts(pattern: str) -> tuple[str, ...]:
    parts = tuple(pattern.replace("\\", "/").removeprefix("./").split("/"))
    return ("**", *parts) if len(parts) == 1 or (len(parts) == 2 and parts[1] == "**") else parts


def _segment_overlap(left: str, right: str) -> bool:
    if not any(char in left for char in "*?"):
        return fnmatch.fnmatchcase(left, right)
    if not any(char in right for char in "*?"):
        return fnmatch.fnmatchcase(right, left)
    first = re.split(r"[*?]", left)
    second = re.split(r"[*?]", right)
    return (first[0].startswith(second[0]) or second[0].startswith(first[0])) and (
        first[-1].endswith(second[-1]) or second[-1].endswith(first[-1])
    )


def apply_overrides(plan: ChangePlan, choices: tuple[tuple[str, str], ...]) -> ChangePlan:
    normalized = tuple({_scope(path): role for path, role in choices}.items())
    release = tuple(
        (path, role) for path, role in normalized if path in plan.guard and role != "guard"
    )
    remaining = tuple(item for item in normalized if item not in release)
    for path, role in (*release, *remaining):
        plan = move_plan(plan, path, role)
    return plan


def deny_rules(plan: ChangePlan) -> tuple[str, ...]:
    if plan.read_only:
        return (*WRITERS, *EXECUTION)
    return tuple(f"{writer}({pattern})" for pattern in plan.guard for writer in WRITERS)


def strict_tools(plan: ChangePlan) -> tuple[str, ...]:
    return READ_TOOLS if plan.read_only else (*READ_TOOLS, *WRITERS)


def plan_metrics(plan: ChangePlan, actual: tuple[str, ...], engine: str) -> dict[str, object]:
    protected = tuple(path for path in actual if guarded(path, plan))
    unplanned = tuple(
        path for path in actual if not any(path_matches(path, item.path) for item in plan.edit)
    )
    return {
        "change_plan": asdict(plan),
        "planned_edit_paths": tuple(item.path for item in plan.edit),
        "actual_edited_paths": actual,
        "out_of_plan_edits": unplanned,
        "guard_violations": protected,
        "enforcement": "strict-tools"
        if engine == "claude" and (plan.read_only or plan.guard)
        else "best-effort",
    }
