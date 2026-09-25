from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from cuanta.cli.theme import ThemeName


class OutputMode(StrEnum):
    PRETTY = "pretty"
    PLAIN = "plain"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class GlobalOptions:
    plain: bool = False
    json: bool = False
    no_emoji: bool = False
    theme: ThemeName = ThemeName.AUTO
    project: Path | None = None
    yes: bool = False
    verbose: bool = False


@dataclass(frozen=True, slots=True)
class Environment:
    is_tty: bool
    no_color: bool
    encoding: str
    colorfgbg: str | None


@dataclass(frozen=True, slots=True)
class OutputSettings:
    mode: OutputMode
    theme: ThemeName
    unicode: bool
    emoji: bool
    verbose: bool


def resolve_mode(options: GlobalOptions, environment: Environment) -> OutputMode:
    if options.json:
        return OutputMode.JSON
    if options.plain or not environment.is_tty or environment.no_color:
        return OutputMode.PLAIN
    return OutputMode.PRETTY


def supports_unicode(encoding: str) -> bool:
    return encoding.lower().replace("-", "").startswith("utf")


def resolve_output(
    options: GlobalOptions,
    environment: Environment,
    theme_default: ThemeName | None = None,
    emoji_default: bool = True,
) -> OutputSettings:
    from cuanta.cli.theme import resolve_theme

    mode = resolve_mode(options, environment)
    unicode = mode is OutputMode.PRETTY and supports_unicode(environment.encoding)
    unicode = unicode and not options.no_emoji
    emoji = unicode and emoji_default
    requested = options.theme
    if requested is ThemeName.AUTO and theme_default is not None:
        requested = theme_default
    return OutputSettings(
        mode=mode,
        theme=resolve_theme(requested, environment.colorfgbg),
        unicode=unicode,
        emoji=emoji,
        verbose=options.verbose,
    )
