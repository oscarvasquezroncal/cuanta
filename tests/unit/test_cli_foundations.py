from __future__ import annotations

import io
import json
import threading

import pytest
from rich.console import Console

from cuanta.cli.document import Column as TableColumn
from cuanta.cli.document import Document, Line, Table
from cuanta.cli.fmt import compact, duration, percent, thousands, usd
from cuanta.cli.group import hoist_globals
from cuanta.cli.mascot import michi
from cuanta.cli.output import (
    Environment,
    GlobalOptions,
    OutputMode,
    OutputSettings,
    resolve_output,
)
from cuanta.cli.presenters.json_presenter import JsonPresenter
from cuanta.cli.presenters.plain import PlainPresenter
from cuanta.cli.presenters.pretty import PrettyPresenter, build_theme
from cuanta.cli.theme import (
    DARK_WORDMARK,
    ThemeName,
    interpolate,
    resolve_theme,
    semantic_colors,
    wordmark_colors,
)
from cuanta.domain.errors import DomainFailure
from cuanta.domain.messages import msg
from cuanta.domain.progress import (
    Note,
    Status,
    StepFinished,
    file_count,
    finished,
    note,
    started,
    took,
)
from cuanta.domain.voice import Mood, glyph, hairball_phrase
from tests.support import strip_ansi

TTY = Environment(is_tty=True, no_color=False, encoding="utf-8", colorfgbg=None)


def test_hoist_moves_global_flags_to_front() -> None:
    args = ["test", "--json", "--theme", "light", "--project=x", "--dry-run"]
    assert hoist_globals(args) == ["--json", "--theme", "light", "--project=x", "test", "--dry-run"]


def test_hoist_stops_at_double_dash() -> None:
    assert hoist_globals(["cat", "--", "--json"]) == ["cat", "--", "--json"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ThemeName.DARK),
        ("15;0", ThemeName.DARK),
        ("0;15", ThemeName.LIGHT),
        ("x;y", ThemeName.DARK),
    ],
)
def test_resolve_theme_auto(value: str | None, expected: ThemeName) -> None:
    assert resolve_theme(ThemeName.AUTO, value) is expected


def test_explicit_theme_wins_over_colorfgbg() -> None:
    assert resolve_theme(ThemeName.DARK, "0;15") is ThemeName.DARK


def test_wordmark_dark_matches_spec() -> None:
    assert wordmark_colors(ThemeName.DARK) == DARK_WORDMARK


def test_wordmark_light_interpolates_light_tokens() -> None:
    colors = wordmark_colors(ThemeName.LIGHT)
    assert len(colors) == 6
    assert colors[0] == "#C8693A"
    assert colors[-1] == "#5A62C9"


def test_interpolate_endpoints() -> None:
    assert interpolate(("#000000", "#FFFFFF"), 3) == ("#000000", "#808080", "#FFFFFF")


def test_semantic_colors_cover_all_names() -> None:
    names = {"brand", "accent", "secondary", "title", "ok", "warn", "err", "info", "muted"}
    assert set(semantic_colors(ThemeName.DARK)) == names


def test_mode_resolution() -> None:
    assert resolve_output(GlobalOptions(), TTY).mode is OutputMode.PRETTY
    assert resolve_output(GlobalOptions(json=True), TTY).mode is OutputMode.JSON
    assert resolve_output(GlobalOptions(plain=True), TTY).mode is OutputMode.PLAIN
    piped = Environment(is_tty=False, no_color=False, encoding="utf-8", colorfgbg=None)
    assert resolve_output(GlobalOptions(), piped).mode is OutputMode.PLAIN
    no_color = Environment(is_tty=True, no_color=True, encoding="utf-8", colorfgbg=None)
    assert resolve_output(GlobalOptions(), no_color).mode is OutputMode.PLAIN


def test_emoji_needs_unicode_encoding_and_pretty() -> None:
    assert resolve_output(GlobalOptions(), TTY).emoji
    assert not resolve_output(GlobalOptions(no_emoji=True), TTY).emoji
    legacy = Environment(is_tty=True, no_color=False, encoding="cp1252", colorfgbg=None)
    assert not resolve_output(GlobalOptions(), legacy).unicode
    assert not resolve_output(GlobalOptions(), TTY, emoji_default=False).emoji


def test_config_theme_default_applies_only_to_auto() -> None:
    settings = resolve_output(GlobalOptions(), TTY, theme_default=ThemeName.LIGHT)
    assert settings.theme is ThemeName.LIGHT
    explicit = resolve_output(GlobalOptions(theme=ThemeName.DARK), TTY, ThemeName.LIGHT)
    assert explicit.theme is ThemeName.DARK


def test_glyphs_have_ascii_fallbacks() -> None:
    assert [
        glyph(status, False) for status in (Status.OK, Status.FAIL, Status.WARN, Status.RESUME)
    ] == [
        "+",
        "x",
        "!",
        "~",
    ]
    assert glyph(Status.OK, True) == "✓"


def test_hairball_phrase() -> None:
    assert hairball_phrase(7, 2) == "7 failed in 2 hairballs"
    assert hairball_phrase(1, 1) == "1 failed in 1 hairball"


def test_michi_moods() -> None:
    assert michi(Mood.ALARMED)[1] == "( O.O )"
    assert michi(Mood.SLEEPY)[1].endswith("z")


def test_formatters() -> None:
    assert compact(1_234_567) == "1.2M"
    assert compact(999) == "999"
    assert compact(12_300) == "12.3K"
    assert thousands(1234567) == "1,234,567"
    assert percent(0.125) == "12.5%"
    assert usd(0.5) == "$0.5000"
    assert usd(12.5) == "$12.50"
    assert duration(75) == "1m15s"


def test_plain_presenter_renders_table_and_progress() -> None:
    out = io.StringIO()
    presenter = PlainPresenter(out=out, err=io.StringIO())
    presenter.publish(StepFinished("detect", Status.OK, "3 files"))
    presenter.publish(Note(Status.WARN, "whiskers: VERIFY_TIER=moderate"))
    table = Table("t", (TableColumn("name"), TableColumn("n", numeric=True)), (("a", "1,000"),))
    presenter.render(Document(blocks=(Line("hello", Status.OK), table)))
    text = out.getvalue()
    assert "+ detect 3 files" in text
    assert "! whiskers: VERIFY_TIER=moderate" in text
    assert "a     1,000" in text


def test_json_presenter_emits_exactly_one_document() -> None:
    out, err = io.StringIO(), io.StringIO()
    presenter = JsonPresenter(out=out, err=err)
    presenter.publish(Note(Status.INFO, "hi"))
    presenter.render(Document(blocks=(), payload={"a": 1}))
    presenter.fail(DomainFailure("late"))
    assert json.loads(out.getvalue()) == {"a": 1}
    assert "late" in err.getvalue()


PRETTY = OutputSettings(OutputMode.PRETTY, ThemeName.DARK, unicode=True, emoji=False, verbose=False)


class GatedFile(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, text: str) -> int:
        if not self.entered.is_set():
            self.entered.set()
            self.release.wait(5)
        return super().write(text)


def test_pretty_presenter_takes_one_publish_at_a_time_from_two_threads() -> None:
    gated = GatedFile()
    console = Console(
        file=gated, width=80, theme=build_theme(PRETTY), force_terminal=False, highlight=False
    )
    presenter = PrettyPresenter(PRETTY, console)
    printing = threading.Thread(
        target=presenter.publish, args=(note(Status.INFO, msg("progress.plan")),), daemon=True
    )
    printing.start()
    assert gated.entered.wait(5)
    revealed = threading.Event()

    def reveal() -> None:
        presenter.publish(started("index", msg("progress.index")))
        revealed.set()

    threading.Thread(target=reveal, daemon=True).start()
    try:
        assert not revealed.wait(0.3)
    finally:
        gated.release.set()
    assert revealed.wait(5)
    printing.join(5)
    presenter.publish(finished("index", Status.OK, took(3.0), 3.0))
    presenter.close()
    assert "index  3 s" in gated.getvalue()


def test_pretty_presenter_shows_a_step_revealed_from_a_timer_thread() -> None:
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=80, theme=build_theme(PRETTY), force_terminal=True, highlight=False
    )
    presenter = PrettyPresenter(PRETTY, console)
    shown = threading.Event()
    errors: list[BaseException] = []

    def reveal() -> None:
        try:
            presenter.publish(started("index", msg("progress.index")))
        except BaseException as error:
            errors.append(error)
        finally:
            shown.set()

    timer = threading.Timer(0.01, reveal)
    timer.daemon = True
    timer.start()
    assert shown.wait(5)
    presenter.publish(finished("index", Status.OK, took(52.0, file_count(854)), 52.0))
    presenter.close()
    assert errors == []
    lines = [line.strip() for line in strip_ansi(buffer.getvalue()).splitlines() if line.strip()]
    assert lines[-1] == "✓ index  854 files · 52 s"


def test_plain_prints_one_line_when_a_slow_step_ends() -> None:
    out = io.StringIO()
    presenter = PlainPresenter(out=out, err=io.StringIO())
    presenter.publish(started("index", msg("progress.index")))
    presenter.publish(finished("index", Status.OK, took(52.0, file_count(854)), 52.0))
    presenter.publish(finished("plan", Status.FAIL, took(3.0), 3.0))
    assert out.getvalue().splitlines() == ["+ index 854 files · 52 s", "x plan 3 s"]


def test_json_streams_a_finished_step_with_its_seconds() -> None:
    out, err = io.StringIO(), io.StringIO()
    JsonPresenter(out=out, err=err).publish(
        finished("index", Status.OK, took(52.0, file_count(854)), 52.0)
    )
    record = json.loads(err.getvalue())
    assert (record["event"], record["key"], record["seconds"]) == ("StepFinished", "index", 52.0)
    assert record["detail"] == "854 files · 52 s"
    assert out.getvalue() == ""
