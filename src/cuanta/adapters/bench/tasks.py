from __future__ import annotations

import re
import tomllib
from pathlib import Path

from cuanta.domain.answer_check import AnswerSpec
from cuanta.domain.bench import DEFAULT_ACCEPT, BenchTask, Source
from cuanta.domain.mandate import MandateRequest

REQUEST_FIELDS = ("type", "what", "why", "where", "constraints", "tests", "out_of_scope")


def _strings(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item).lstrip("\n") for key, item in value.items()}


def _source(value: object) -> Source | None:
    if not isinstance(value, dict):
        return None
    url, digest = value.get("url"), value.get("sha256")
    if not isinstance(url, str) or not isinstance(digest, str):
        return None
    return Source(url, digest.lower())


def _edits(value: object) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        (str(item["path"]), str(item["old"]), str(item["new"]))
        for item in value
        if isinstance(item, dict) and {"path", "old", "new"} <= item.keys()
    )


def _answer(value: object) -> AnswerSpec | None:
    if not isinstance(value, dict) or set(value) != {"keywords", "citations"}:
        return None
    keywords, citations = value.get("keywords"), value.get("citations")
    if not isinstance(keywords, list) or not isinstance(citations, list):
        return None
    if not keywords or not citations:
        return None
    if any(not isinstance(item, str) or not item.strip() for item in (*keywords, *citations)):
        return None
    try:
        for pattern in citations:
            re.compile(pattern)
    except re.error:
        return None
    return AnswerSpec(tuple(keywords), tuple(citations))


def parse_task(text: str) -> BenchTask | None:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    request = data.get("request")
    name, fixture = data.get("name"), data.get("fixture")
    if not isinstance(name, str) or not isinstance(fixture, str) or not isinstance(request, dict):
        return None
    answer = _answer(data.get("answer"))
    if "answer" in data and answer is None:
        return None
    suites = data.get("suites")
    setup = data.get("setup")
    fields = {key: str(request.get(key, "")) for key in REQUEST_FIELDS}
    return BenchTask(
        name=name,
        suites=tuple(str(item) for item in suites) if isinstance(suites, list) else (),
        fixture=fixture,
        request=MandateRequest(**fields),
        prompt=str(data.get("prompt", "")),
        hidden=_strings(data.get("hidden")),
        files=_strings(data.get("files")),
        setup=tuple(str(item) for item in setup) if isinstance(setup, list) else (),
        accept=str(data.get("accept") or DEFAULT_ACCEPT),
        source=_source(data.get("source")),
        edits=_edits(data.get("edit")),
        answer=answer,
    )


def load_tasks(directory: Path) -> tuple[BenchTask, ...]:
    found: list[BenchTask] = []
    for path in sorted(directory.glob("*.toml")):
        task = parse_task(path.read_text(encoding="utf-8"))
        if task is not None:
            found.append(task)
    return tuple(found)
