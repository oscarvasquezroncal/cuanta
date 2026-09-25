from __future__ import annotations

import locale
import os
import tomllib
from importlib.resources import files

from cuanta.domain.messages import Message, english, keyed, render

LANGUAGES = ("en", "es")
DEFAULT_LANGUAGE = "en"
_SPANISH_PREFIXES = ("es", "spanish")


def _flatten(table: dict[str, object], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in table.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}."))
        else:
            flat[name] = str(value)
    return flat


def load_catalog(language: str) -> dict[str, str]:
    source = files("cuanta.tui").joinpath("locales", f"{language}.toml")
    return _flatten(tomllib.loads(source.read_text(encoding="utf-8")))


def language_from_locale(name: str) -> str:
    lowered = name.strip().lower()
    return "es" if lowered.startswith(_SPANISH_PREFIXES) else DEFAULT_LANGUAGE


def os_locale() -> str:
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable, "")
        if value:
            return value
    current = locale.getlocale()[0]
    return current or ""


def resolve_language(explicit: str, configured: str, system: str) -> str:
    for candidate in (explicit, configured):
        if candidate in LANGUAGES:
            return candidate
    return language_from_locale(system)


class Catalog:
    def __init__(self, language: str) -> None:
        self.language = language if language in LANGUAGES else DEFAULT_LANGUAGE
        self._fallback = load_catalog(DEFAULT_LANGUAGE)
        self._strings = (
            self._fallback if self.language == DEFAULT_LANGUAGE else load_catalog(self.language)
        )

    def __call__(self, key: str, **values: object) -> str:
        text = self._strings.get(key) or self._fallback.get(key) or key
        return text.format(**values) if values else text

    def message(self, message: Message | None, fallback: str = "") -> str:
        if message is None:
            return fallback
        template = self._strings.get(f"msg.{message.key}") or self._fallback.get(
            f"msg.{message.key}"
        )
        if template is None:
            return english(message)
        return render(template, message.values(self.message))

    def keyed(self, prefix: str, value: str) -> str:
        found = keyed(prefix, value)
        return self.message(found) if isinstance(found, Message) else found

    def check_name(self, name: str) -> str:
        head, _, rest = name.partition(" ")
        label = self.keyed("check", head)
        return f"{label} {rest}" if rest else label

    def keys(self) -> frozenset[str]:
        return frozenset(self._strings)
