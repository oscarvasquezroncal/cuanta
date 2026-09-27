from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from cuanta.domain.affected import is_test_file
from cuanta.domain.code_index import IndexedFile, IndexRow, index_path
from cuanta.domain.report import file_refs

_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_INLINE = re.compile(r"`([^`\n]+)`|\[[^]\n]*\]\(([^)\n]+)\)")
_PATH = re.compile(r"[A-Za-z0-9_.@()+/-]+\.[A-Za-z0-9]+(?::\d+(?:-\d+)?)?")
_IMPROVEMENT = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+(IMP-\d+)\b")
_DOCUMENTS = {
    "docs/GROUND_TRUTH.md": "ground-truth",
    "docs/FLAGS.md": "flag",
    "docs/IMPROVEMENTS.md": "improvement",
    "docs/HISTORIAS.md": "story",
    "HISTORIAS.md": "story",
}


@dataclass(frozen=True, slots=True)
class _Section:
    heading: str
    line: int
    end_line: int
    text: str


def _sections(text: str) -> tuple[_Section, ...]:
    lines = text.splitlines()
    headings: list[tuple[int, str]] = []
    hierarchy: list[tuple[int, str]] = []
    fence = ""
    for number, line in enumerate(lines, 1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if fence == marker:
                fence = ""
            elif not fence:
                fence = marker
            continue
        if fence:
            continue
        match = _HEADING.match(line)
        if not match:
            continue
        level = len(match.group(1))
        hierarchy = [item for item in hierarchy if item[0] < level]
        hierarchy.append((level, match.group(2)))
        headings.append((number, " > ".join(title for _, title in hierarchy)))
    if not headings or headings[0][0] > 1:
        headings.insert(0, (1, "Preamble"))
    result: list[_Section] = []
    for offset, (start, heading) in enumerate(headings):
        end = headings[offset + 1][0] - 1 if offset + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start - 1 : end]).rstrip()
        if body.strip():
            result.append(_Section(heading, start, end, body))
    return tuple(result)


def _references(text: str) -> tuple[str, ...]:
    values = [ref.path for ref in file_refs(text)]
    values.extend(
        match.group(1) or match.group(2)
        for match in _INLINE.finditer(text)
        if _PATH.fullmatch(match.group(1) or match.group(2))
    )
    found: set[str] = set()
    for value in values:
        try:
            path = index_path(value.split(":", 1)[0])
        except ValueError:
            continue
        found.add(path)
    return tuple(sorted(found))


def _record_lines(section: _Section) -> tuple[tuple[int, str], ...]:
    result: list[tuple[int, str]] = []
    fence = ""
    for offset, line in enumerate(section.text.splitlines()):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if fence == marker:
                fence = ""
            elif not fence:
                fence = marker
            continue
        if not fence:
            result.append((section.line + offset, line))
    return tuple(result)


def _rule(
    file: IndexedFile,
    section: _Section,
    target: str,
    relation: str,
    stale: bool,
) -> IndexRow:
    return IndexRow(
        f"{file.path}:{section.line}:{relation}:{target}",
        file.path,
        file.content_hash,
        f"{file.path} § {section.heading}",
        text=section.text,
        line=section.line,
        end_line=section.end_line,
        target=target,
        relation=relation,
        stale=stale,
    )


def scoped_rules(file: IndexedFile, text: str) -> tuple[IndexRow, ...]:
    try:
        path = index_path(file.path)
    except ValueError:
        return ()
    rulebook = PurePosixPath(path).name == "CLAUDE.md"
    relation = "rule" if rulebook else _DOCUMENTS.get(path, "")
    if not relation:
        return ()
    parent = PurePosixPath(path).parent.as_posix()
    scope = "**" if parent == "." else f"{parent}/**"
    stale = hashlib.sha256(text.encode()).hexdigest() != file.content_hash
    result: list[IndexRow] = []
    for section in _sections(text):
        if rulebook:
            result.append(_rule(file, section, scope, relation, stale))
            continue
        if relation == "improvement":
            for number, line in _record_lines(section):
                match = _IMPROVEMENT.match(line)
                if match:
                    item = _Section(f"{section.heading} > {match.group(1)}", number, number, line)
                    for target in _references(line) or ("**",):
                        result.append(_rule(file, item, target, relation, stale))
            continue
        if relation == "flag":
            entries = [
                (number, line, _references(line))
                for number, line in _record_lines(section)
                if line.lstrip().startswith("|") and _references(line)
            ]
            if entries:
                for number, line, targets in entries:
                    title = line.split("|", 2)[1].strip()
                    item = _Section(f"{section.heading} > {title}", number, number, line)
                    result.extend(_rule(file, item, target, relation, stale) for target in targets)
                continue
        for target in _references(section.text) or ("**",):
            result.append(_rule(file, section, target, relation, stale))
    return tuple(result)


def test_links(
    files: tuple[IndexedFile, ...], edges: tuple[IndexRow, ...], commands: tuple[str, ...]
) -> tuple[IndexRow, ...]:
    by_path = {file.path: file for file in files}
    verification = "\n".join(
        dict.fromkeys(command.strip() for command in commands if command.strip())
    )
    result: dict[tuple[str, str], IndexRow] = {}
    for edge in sorted(edges, key=lambda row: (row.path, row.line, row.id)):
        file = by_path.get(edge.path)
        if (
            file is None
            or not is_test_file(edge.path)
            or edge.relation != "imports"
            or edge.stale
            or edge.source_hash != file.content_hash
            or edge.target not in by_path
            or edge.target == edge.path
        ):
            continue
        result.setdefault(
            (edge.path, edge.target),
            IndexRow(
                f"{edge.path}:{edge.line}:tests:{edge.target}",
                edge.path,
                file.content_hash,
                f"{edge.provenance} import {edge.path}:{edge.line}",
                text=verification,
                line=edge.line,
                end_line=edge.end_line,
                target=edge.target,
                relation="tests",
                confidence=edge.confidence,
            ),
        )
    return tuple(result.values())
