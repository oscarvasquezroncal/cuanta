from __future__ import annotations

import re
from dataclasses import dataclass

GITIGNORE = ".gitignore"
CONFIG_ESCAPES = {"n": "\n", "t": "\t", "b": "\b"}
ALWAYS_IGNORED = frozenset({".git"})


@dataclass(frozen=True, slots=True)
class IgnoreRule:
    base: str
    pattern: re.Pattern[str]
    negate: bool
    directory_only: bool


def _strip_trailing_spaces(line: str) -> str:
    stripped = line.rstrip(" ")
    if stripped.endswith("\\") and len(stripped) < len(line):
        return stripped[:-1] + " "
    return stripped


def _class(glob: str, start: int) -> tuple[str, int]:
    index = start + 1
    if index < len(glob) and glob[index] in "!^":
        index += 1
    if index < len(glob) and glob[index] == "]":
        index += 1
    while index < len(glob) and glob[index] != "]":
        index += 1
    if index >= len(glob):
        return re.escape("["), start + 1
    body = glob[start + 1 : index]
    if body[:1] in "!^":
        body = "^" + body[1:]
    body = body.replace("\\", "\\\\")
    return f"[{body}]", index + 1


def _translate(glob: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(glob):
        char = glob[index]
        if glob.startswith("**/", index) and (index == 0 or glob[index - 1] == "/"):
            parts.append("(?:.*/)?")
            index += 3
        elif (
            glob.startswith("**", index)
            and index + 2 == len(glob)
            and glob[index - 1 : index] == "/"
        ):
            parts.append(".*")
            index += 2
        elif char == "*":
            while index < len(glob) and glob[index] == "*":
                index += 1
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
            index += 1
        elif char == "[":
            translated, index = _class(glob, index)
            parts.append(translated)
        elif char == "\\" and index + 1 < len(glob):
            parts.append(re.escape(glob[index + 1]))
            index += 2
        else:
            parts.append(re.escape(char))
            index += 1
    return "".join(parts)


def parse_rule(base: str, line: str, ignorecase: bool = False) -> IgnoreRule | None:
    text = _strip_trailing_spaces(line.rstrip("\r\n"))
    if not text or text.startswith("#"):
        return None
    negate = text.startswith("!")
    if negate or text.startswith(("\\!", "\\#")):
        text = text[1:]
    directory_only = text.endswith("/")
    text = text.rstrip("/")
    if not text:
        return None
    anchored = "/" in text
    text = text.lstrip("/")
    prefix = "" if anchored else "(?:.*/)?"
    flags = re.DOTALL | (re.IGNORECASE if ignorecase else 0)
    return IgnoreRule(
        base, re.compile(f"{prefix}{_translate(text)}", flags), negate, directory_only
    )


def parse_rules(base: str, text: str, ignorecase: bool = False) -> tuple[IgnoreRule, ...]:
    lines = text.removeprefix("﻿").splitlines()
    rules = (parse_rule(base, line, ignorecase) for line in lines)
    return tuple(rule for rule in rules if rule is not None)


def _relative(path: str, base: str, ignorecase: bool = False) -> str | None:
    if not base:
        return path
    prefix = base + "/"
    inside = (
        path.casefold().startswith(prefix.casefold()) if ignorecase else path.startswith(prefix)
    )
    return path[len(prefix) :] if inside else None


@dataclass(frozen=True, slots=True)
class IgnoreRules:
    rules: tuple[IgnoreRule, ...] = ()
    ignorecase: bool = False

    def with_file(self, base: str, text: str) -> IgnoreRules:
        added = parse_rules(base.strip("/"), text, self.ignorecase)
        return IgnoreRules((*self.rules, *added), self.ignorecase)

    def matches(self, path: str, is_dir: bool) -> bool:
        if path.rsplit("/", 1)[-1].casefold() in ALWAYS_IGNORED:
            return True
        ignored = False
        for rule in self.rules:
            if rule.directory_only and not is_dir:
                continue
            relative = _relative(path, rule.base, self.ignorecase)
            if relative and rule.pattern.fullmatch(relative):
                ignored = not rule.negate
        return ignored

    def ignored(self, path: str, is_dir: bool = False) -> bool:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            if self.matches("/".join(parts[:depth]), True):
                return True
        return self.matches(path, is_dir)


def _config_value(raw: str) -> str:
    text = raw.strip()
    kept: list[str] = []
    quoted = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            quoted = not quoted
        elif char == "\\" and index + 1 < len(text):
            index += 1
            kept.append(CONFIG_ESCAPES.get(text[index], text[index]))
        elif char in "#;" and not quoted:
            break
        else:
            kept.append(char)
        index += 1
    return "".join(kept).strip()


def git_config(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    section = ""
    for raw in text.removeprefix("﻿").splitlines():
        line = raw.strip()
        if line.startswith("["):
            header, _, line = line[1:].partition("]")
            name, _, sub = header.strip().partition(" ")
            section = name.casefold() + (f".{sub.strip().strip(chr(34))}" if sub else "")
            line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        key, found, value = line.partition("=")
        values[f"{section}.{key.strip().casefold()}"] = _config_value(value) if found else "true"
    return values


def git_true(value: str) -> bool:
    return value.strip().casefold() in {"true", "yes", "on", "1"}
