from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from functools import lru_cache

from cuanta.domain.anchors import (
    KNOWN_EXTENSIONS,
    RequestAnchor,
    anchor_candidates,
    request_anchors,
)
from cuanta.domain.code_index import IndexedFile, IndexRow, index_path
from cuanta.domain.index_search import rank_files, search_terms
from cuanta.domain.intake import (
    OUT_OF_SCOPE_AT,
    READ_ONLY_AT,
    core_text,
    extract_mentions,
    extract_out_of_scope,
    sentences,
)
from cuanta.domain.mandate import (
    INVESTIGATION,
    MIN_PARTS,
    MandateRequest,
    MandateType,
    Span,
    in_parts,
    numbered_parts,
    part_spans,
)
from cuanta.domain.messages import Message, msg

WRITERS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
EXECUTION = ("Bash", "PowerShell", "Computer", "ComputerUse")
READ_TOOLS = ("Read", "Grep", "Glob", "Agent", "Task")
WRITING_TYPES = frozenset({MandateType.FEATURE, MandateType.BUG, MandateType.REFACTOR})
READ_ONLY_FIELDS = ("what", "why", "where", "constraints", "out_of_scope")
_PATH = re.compile(r"(?<![\w.@*?-])(?:[\w.@*?-]+(?:/[\w.@*?-]+)+|[\w@*?-]+\.[\w*?-]+(?:/\*\*)?)")
ROOTED = ("/", "~", "$", "%")
PHASE_LOCAL = re.compile(
    r"\b(?:en|durante)\s+esta\s+fase\b|\b(?:in|during)\s+this\s+phase\b", re.IGNORECASE
)
THIRD_PARTY_DIRS = frozenset(
    {"node_modules", ".venv", "venv", "site-packages", "dist-packages", "__pycache__", ".git"}
)
RENDERER_TERMS = frozenset({"renderer", "renderizador", "3d", "three"})
RENDERER_PATH_TERMS = frozenset({"renderer", "3d", "three"})
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
    released: tuple[str, ...] = ()


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


def _guessable(token: str, roots: frozenset[str]) -> bool:
    if token.startswith(ROOTED) or any(char.isspace() for char in token):
        return False
    parts = [part for part in token.removesuffix("/**").split("/") if part]
    if not parts or THIRD_PARTY_DIRS.intersection(parts[:-1]):
        return False
    stem, dot, extension = parts[-1].rpartition(".")
    if dot and stem and extension.lower() in KNOWN_EXTENSIONS:
        return True
    return len(parts) > 1 and (token.endswith("/") or parts[0] in roots)


@lru_cache(maxsize=8)
def _segments(
    paths: tuple[str, ...],
) -> tuple[Mapping[str, tuple[str, ...]], Mapping[str, tuple[str, ...]]]:
    names: dict[str, list[str]] = {}
    folders: dict[str, list[str]] = {}
    for path in paths:
        *parents, name = path.split("/")
        names.setdefault(name, []).append(path)
        for folder in dict.fromkeys(parents):
            folders.setdefault(folder, []).append(path)
    return (
        {key: tuple(value) for key, value in names.items()},
        {key: tuple(value) for key, value in folders.items()},
    )


def _candidates(token: str, plain: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    if "*" in token:
        return paths
    names, folders = _segments(paths)
    last = plain.rsplit("/", 1)[-1]
    return (*names.get(last, ()), *folders.get(last, ()))


def _rooted_only(text: str, token: str) -> bool:
    start = text.find(token)
    while start != -1:
        if text[start - 1 : start] not in ROOTED:
            return False
        start = text.find(token, start + 1)
    return True


def _admitted(token: str, roots: frozenset[str], guess: bool, relative: bool) -> bool:
    if not guess:
        return "/" in token or "." in token
    return relative and _guessable(token, roots)


def mentioned_paths(
    text: str, paths: tuple[str, ...], keep_unknown: bool = False, guess: bool = False
) -> tuple[str, ...]:
    found: set[str] = set()
    roots = frozenset(path.split("/", 1)[0] for path in paths if "/" in path)
    normalized = text.replace("\\", "/")
    mentions = extract_mentions(normalized)
    relative = {token for token in mentions if not _rooted_only(normalized, token)}
    tokens = dict.fromkeys(mentions)
    for occurrence in _PATH.finditer(normalized):
        tokens[occurrence.group()] = None
        if normalized[max(0, occurrence.start() - 1) : occurrence.start()] not in ROOTED:
            relative.add(occurrence.group())
    for raw_token in tokens:
        matched = False
        token = raw_token.rstrip(".,;:").removeprefix("./")
        if ":" in token or ".." in token.split("/"):
            continue
        plain = token.removesuffix("/**").rstrip("/")
        for path in _candidates(token, plain, paths):
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
        if "*" in token or (
            keep_unknown and not matched and _admitted(token, roots, guess, raw_token in relative)
        ):
            try:
                found.add(_scope(token))
            except ValueError:
                continue
    return tuple(sorted(found))


def _guard_paths(text: str, paths: tuple[str, ...]) -> set[str]:
    found = set(mentioned_paths(text, paths, keep_unknown=True))
    if RENDERER_TERMS.intersection(search_terms(text)):
        found.update(path for path in paths if RENDERER_PATH_TERMS.intersection(search_terms(path)))
    return found


def _kept(text: str) -> str:
    return " ".join(
        sentence for sentence in sentences(text) if not OUT_OF_SCOPE_AT.search(sentence)
    )


def _intended(request: MandateRequest, paths: tuple[str, ...]) -> tuple[str, ...]:
    kept = replace(
        request,
        what=_kept(request.what),
        why=_kept(request.why),
        where=_kept(request.where),
        constraints="",
        tests=_kept(request.tests),
    )
    anchored, _ = _anchor_targets(request_anchors(kept), paths)
    text = " ".join((kept.what, kept.why, kept.where, kept.tests))
    return (*anchored, *mentioned_paths(text, paths, keep_unknown=True, guess=True))


def _exclusions(
    request: MandateRequest, paths: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    outside, phases = _split_phases(request.why, _phase_spans(request, request.why))
    text = " ".join(
        (
            request.out_of_scope,
            *extract_out_of_scope(request.what),
            *extract_out_of_scope(outside),
            *extract_out_of_scope(request.constraints),
        )
    )
    found = _guard_paths(text, paths)
    local = " ".join(
        sentence for sentence in extract_out_of_scope(phases) if not PHASE_LOCAL.search(sentence)
    )
    phased = _guard_paths(local, paths) - found if local else set()
    intended = _intended(request, paths) if phased else ()
    released = {
        pattern
        for pattern in phased
        if any(_overlap(path, pattern) for path in intended)
        and not any(_overlap(pattern, guard) for guard in found)
    }
    return tuple(sorted(found | (phased - released))), tuple(sorted(released))


def request_query(request: MandateRequest) -> str:
    text = " ".join(core_text(value) for value in (request.what, request.why, request.where))
    return " ".join(term for term in search_terms(text) if term not in _GENERIC)


def _concrete(found: tuple[str, ...], paths: tuple[str, ...]) -> tuple[str, ...]:
    known = set(paths)
    return tuple(path for path in found if path in known)


def _anchored_token(
    pattern: str, anchors: tuple[RequestAnchor, ...], paths: tuple[str, ...]
) -> bool:
    if "*" in pattern or pattern in paths:
        return False
    return any(anchor.path == pattern or anchor.path.endswith("/" + pattern) for anchor in anchors)


def _anchor_targets(
    anchors: tuple[RequestAnchor, ...], paths: tuple[str, ...]
) -> tuple[dict[str, EditTarget], set[str]]:
    targets: dict[str, EditTarget] = {}
    context: set[str] = set()
    for anchor in anchors:
        found = anchor_candidates(anchor.path, paths)
        if len(found) != 1:
            continue
        path = found[0]
        if anchor.field == "constraints":
            context.add(path)
        elif path not in targets:
            label = RequestAnchor(path, anchor.start, anchor.end).label
            targets[path] = EditTarget(path, 1.0, f"explicit request anchor {label}")
    return targets, context


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
    protection, released = _exclusions(request, paths)
    text = " ".join(core_text(value) for value in (request.what, request.why, request.where))
    query = request_query(request)
    hits = rank_files(files, symbols, edges, notes, rules, history, query, request.type, now, 15)
    anchors = request_anchors(request)
    explicit = tuple(
        pattern
        for pattern in mentioned_paths(text, paths, keep_unknown=True, guess=True)
        if not _anchored_token(pattern, anchors, paths)
    )
    selected: dict[str, EditTarget] = {}
    read: set[str] = set()
    for pattern in explicit:
        if pattern not in paths and "*" not in pattern:
            selected[pattern] = EditTarget(pattern, 0.8, "explicit new file path")
        for path in paths:
            if path_matches(path, pattern):
                selected[path] = EditTarget(path, 1.0, "explicit request path")
    for path in _concrete(mentioned_paths(core_text(request.tests), paths), paths):
        selected.setdefault(path, EditTarget(path, 1.0, "explicit request path"))
    read.update(_concrete(mentioned_paths(core_text(request.constraints), paths), paths))
    anchored, context = _anchor_targets(anchors, paths)
    selected.update(anchored)
    read.update(context)
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
    readonly = request.type == INVESTIGATION or bool(read_only_phrase(request))
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
        released,
    )


def _phase_spans(request: MandateRequest, value: str) -> tuple[Span, ...]:
    if request.type not in WRITING_TYPES or numbered_parts(value).count < MIN_PARTS:
        return ()
    return part_spans(value)


def _split_phases(value: str, spans: tuple[Span, ...]) -> tuple[str, str]:
    outside: list[str] = []
    inside: list[str] = []
    last = 0
    for start, end in spans:
        outside.append(value[last:start])
        inside.append(value[start:end])
        last = end
    outside.append(value[last:])
    return "\n".join(outside), "\n".join(inside)


def _global_readonly(value: str, spans: tuple[Span, ...] = ()) -> str:
    for matched in READ_ONLY_AT.finditer(value):
        if in_parts(matched.start(), spans):
            continue
        if not _READ_ONLY_SCOPE_SUFFIX.match(value, matched.end()):
            return matched.group(0)
    return ""


def read_only_phrase(request: MandateRequest) -> str:
    values = {
        "what": request.what,
        "why": request.why,
        "where": request.where,
        "constraints": request.constraints,
        "out_of_scope": request.out_of_scope,
    }
    for name in READ_ONLY_FIELDS:
        phrase = _global_readonly(values[name], _phase_spans(request, values[name]))
        if phrase:
            return phrase
    return ""


def read_only_notes(request: MandateRequest, plan: ChangePlan | None) -> tuple[Message, ...]:
    if plan is None or not plan.read_only or request.type not in WRITING_TYPES:
        return ()
    phrase = read_only_phrase(request)
    return (msg("change_plan.read_only_phrase", phrase=phrase),) if phrase else ()


def guard_notes(plan: ChangePlan | None) -> tuple[Message, ...]:
    if plan is None or plan.read_only:
        return ()
    released = tuple(
        path
        for path in plan.released
        if path not in plan.read and not any(_overlap(path, guard) for guard in plan.guard)
    )
    return (msg("change_plan.guard_released", paths=", ".join(released)),) if released else ()


def plan_notes(request: MandateRequest, plan: ChangePlan | None) -> tuple[Message, ...]:
    return (*read_only_notes(request, plan), *guard_notes(plan))


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
