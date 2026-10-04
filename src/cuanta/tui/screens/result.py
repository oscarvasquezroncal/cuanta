from __future__ import annotations

from contextlib import suppress
from functools import partial

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.events import Resize
from textual.message import Message
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Markdown,
    Static,
    TabbedContent,
    TabPane,
)

from cuanta.application.results import ResultView
from cuanta.domain.engine import TURN_LIMIT_SUBTYPE
from cuanta.domain.errors import CuantaError
from cuanta.domain.handoff import Handoff
from cuanta.domain.mandate import MandateRequest, MandateType
from cuanta.domain.messages import msg
from cuanta.domain.outcomes import CROSS_KIND, REJECTED, is_attempt
from cuanta.domain.overhead import overhead_messages
from cuanta.domain.report import link_file_refs
from cuanta.domain.stop_reason import FINISHED_STATUSES, stop_message
from cuanta.tui.anatomy_text import anatomy_content
from cuanta.tui.cache_text import first_request_content
from cuanta.tui.fmt import labeled_money, money, run_money
from cuanta.tui.governor_text import governor_content
from cuanta.tui.i18n import Catalog
from cuanta.tui.implementation_text import implementation_content
from cuanta.tui.index_text import index_lines
from cuanta.tui.metrics_text import metrics_content
from cuanta.tui.read_efficiency_text import read_efficiency_content
from cuanta.tui.scout_text import scout_content
from cuanta.tui.screens.confirm import ConfirmScreen
from cuanta.tui.screens.run_file import RunFileScreen
from cuanta.tui.services import Services
from cuanta.tui.time_text import time_content
from cuanta.tui.widgets.flow import FlowRow

FILE_SCHEME = "cuanta-file:"
NARROW_WIDTH = 110
INVESTIGATION = MandateType.INVESTIGATION.value


def parse_file_link(href: str) -> tuple[str, int] | None:
    if not href.startswith(FILE_SCHEME):
        return None
    path, _, line = href.removeprefix(FILE_SCHEME).rpartition(":")
    if not path or not line.isdigit():
        return None
    return path, int(line)


class ResultScreen(Screen[None]):
    BINDINGS = [Binding("escape", "close", show=False)]

    class ContinueRequested(Message):
        def __init__(self, request: MandateRequest) -> None:
            super().__init__()
            self.request = request

    class OutcomeChanged(Message):
        pass

    class OpenMap(Message):
        pass

    class OpenSpectrum(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    def __init__(self, services: Services, catalog: Catalog, view: ResultView) -> None:
        super().__init__(classes="result-screen")
        self._services = services
        self._t = catalog
        self.view = view
        self._handoff: Handoff | None = None

    @property
    def investigation(self) -> bool:
        return self.view.task_type == INVESTIGATION

    def compose(self) -> ComposeResult:
        t, view = self._t, self.view
        with Horizontal(id="result-bar"):
            yield Static(t("result.title"), classes="card-title", id="result-title")
            yield Static(self._status(), id="result-status")
            yield Button(t("result.close"), id="result-close", compact=True)
        yield Static(self._facts(), id="result-facts")
        with Horizontal(id="result-decision-row"):
            yield Static("", id="result-decision")
            yield Button(t("result.accept"), id="result-accept", variant="success", compact=True)
            yield Button(t("result.reject"), id="result-reject", compact=True)
        yield from self._notices()
        yield Static(self._split(), id="result-split")
        yield Static(first_request_content(t, view.cache), id="result-cache")
        yield Static(self._map_summary(), id="result-map-summary")
        if view.implementation is not None:
            yield Static(
                implementation_content(t, view.implementation, view.run.status != "running"),
                id="result-implementation",
            )
        if view.trial is not None:
            with Vertical(id="result-trial"):
                yield Static("", id="result-trial-line")
                yield Static("", id="result-trial-notes")
                yield Static("", id="result-trial-handoff")
                with FlowRow(id="result-trial-actions", classes="button-row"):
                    yield Button(
                        t("result.apply"), id="result-apply", variant="primary", compact=True
                    )
                    yield Button(t("result.discard"), id="result-discard", compact=True)
                    yield Button(t("result.copy_commands"), id="result-copy-commands", compact=True)
        with FlowRow(id="result-actions", classes="button-row"):
            yield from self._actions()
        with TabbedContent(initial="tab-report", id="result-tabs"):
            with (
                TabPane(t("result.tab_report"), id="tab-report"),
                VerticalScroll(id="result-report-scroll"),
            ):
                if view.text.strip():
                    yield Markdown(
                        link_file_refs(view.text, FILE_SCHEME),
                        id="result-report",
                        open_links=False,
                    )
                else:
                    yield Static(
                        Content.styled(t("result.no_report"), "$text-muted"),
                        id="result-report-empty",
                    )
            with (
                TabPane(t("result.tab_files"), id="tab-files"),
                Vertical(id="result-files-pane"),
            ):
                yield DataTable(id="result-files", cursor_type="row", zebra_stripes=True)
                yield Static(
                    Content.styled(t("result.files_hint"), "$text-muted"),
                    id="result-files-hint",
                )
            with (
                TabPane(t("result.tab_consumption"), id="tab-consumption"),
                VerticalScroll(id="result-consumption-scroll"),
            ):
                yield from self._panels()
                yield Static(
                    read_efficiency_content(t, view.read_efficiency), id="result-read-efficiency"
                )
                yield Static(anatomy_content(t, view.anatomy), id="result-anatomy", classes="bars")
                yield DataTable(id="result-agents", cursor_type="none", zebra_stripes=True)
                yield Static(self._consumption(), id="result-consumption")
            yield from self._time()
        yield Footer()

    def _notices(self) -> ComposeResult:
        t, view = self._t, self.view
        if view.run.status not in {*FINISHED_STATUSES, "running"}:
            stop = stop_message(view.run, view.implementation, view.governor)
            yield Static(Content.styled(t.message(stop), "$warning"), id="result-stop")
        if view.fallback_error:
            yield Static(
                Content.styled(
                    t.message(
                        msg(
                            "instinct.fallback",
                            backend=view.fallback_from.capitalize(),
                            error=view.fallback_error,
                        )
                    ),
                    "$warning",
                ),
                id="result-instinct-fallback",
            )
        if view.simple:
            yield Static(Content.styled(t("result.simple_note"), "$warning"), id="result-simple")

    def _time(self) -> ComposeResult:
        with TabPane(self._t("time.title"), id="tab-time"), VerticalScroll():
            yield Static(time_content(self._t, self.view.time), id="result-time", classes="bars")

    def _panels(self) -> ComposeResult:
        yield from self._metrics()
        yield from self._scout()
        yield from self._governor()

    def _metrics(self) -> ComposeResult:
        if self.view.metrics.shown:
            yield Static(metrics_content(self._t, self.view.metrics), id="result-metrics")

    def _scout(self) -> ComposeResult:
        if self.view.scout.shown:
            yield Static(scout_content(self._t, self.view.scout), id="result-scout")

    def _governor(self) -> ComposeResult:
        if self.view.governor.shown:
            yield Static(governor_content(self._t, self.view.governor), id="result-governor")

    def _actions(self) -> ComposeResult:
        t = self._t
        yield Button(
            t("result.save_docs"),
            id="result-save",
            variant="primary" if self.investigation else "default",
            compact=True,
        )
        yield Button(t("result.copy"), id="result-copy", compact=True)
        yield Button(
            t("result.continue"),
            id="result-continue",
            variant="default" if self.investigation else "primary",
            compact=True,
        )
        for key in ("spectrum", "map", "export"):
            yield Button(t(f"result.{key}"), id=f"result-{key}", compact=True)

    def _status(self) -> Content:
        t = self._t
        run = self.view.run
        label = t.keyed("run_status", run.status)
        style = "$success" if run.status in {"ok", "completed"} else "$error"
        badge = Content.styled(f" {label} ", f"bold {style}")
        if not self.view.completion:
            return badge
        state = t.message(msg(f"completion.{self.view.completion}"))
        tone = "$success" if self.view.completion.startswith("complete") else "$warning"
        return Content.assemble(badge, (f" {state}", tone))

    def _verification(self) -> Content:
        t = self._t
        lines = [
            t(
                "result.verify_round",
                role=t(f"models.role_{item.role}") if item.role else "",
                passed=item.passed,
                total=item.total,
                seconds=f"{item.seconds:.1f}",
            )
            for item in self.view.verification
        ]
        failed = any(item.passed < item.total for item in self.view.verification[-1:])
        return Content.styled("\n".join(lines), "$warning" if failed else "$text-muted")

    def _facts(self) -> Content:
        t = self._t
        view = self.view
        kind = view.task_type
        kind_label = t(f"wizard.intent_{kind}") if kind else t.keyed("run_kind", view.run.kind)
        duration = (
            t("result.seconds", seconds=f"{view.duration_s:,.0f}")
            if view.duration_s is not None
            else t("spectrum.na")
        )
        mode = (
            t("result.mode_unknown")
            if not view.shape_known
            else t("result.mode_simple")
            if view.simple
            else t("result.mode_single")
            if view.single
            else t("result.mode_pipeline")
        )
        parts = [
            kind_label,
            mode,
            duration,
            attempt_money(view, t),
            view.run.model or t("wizard.engine_default"),
        ]
        turns = self._turns()
        if turns:
            parts.append(turns)
            if view.terminal_turn and view.run.max_turns > 0:
                parts.append(t("result.terminal_turn"))
        facts = Content.styled("  ·  ".join(parts), "$text-muted")
        if not view.verification:
            return facts
        return Content("\n").join((facts, self._verification()))

    def _turns(self) -> str:
        t, run = self._t, self.view.run
        if run.max_turns > 0:
            text = t("result.turns", used=run.turns, limit=run.max_turns)
        elif run.end_reason == TURN_LIMIT_SUBTYPE or (run.partial and run.turns > 0):
            text = t("result.turns_used", used=run.turns)
        else:
            return ""
        return t.message(msg("result.partial_turns", turns=text)) if run.partial else text

    def _split(self) -> Content:
        t = self._t
        split = self.view.split
        if split is None:
            return Content.styled(t("result.split_unknown"), "$text-muted")
        return Content.assemble(
            (f"{t('result.split_title')}  ", "$text-muted"),
            (
                t(
                    "result.split",
                    fixed=f"{split.fixed:,}",
                    share=f"{split.fixed_share:.0%}",
                    request=f"{split.request:,}",
                    total=f"{split.first_request:,}",
                ),
                "",
            ),
        )

    def _map_summary(self) -> Content:
        metrics = self.view.index
        lines = [Content(self._t("result.findings_saved", count=metrics.findings_saved))]
        if metrics.out_of_plan_edits:
            lines.append(
                Content.styled(
                    self._t("index_metrics.out_of_plan", count=len(metrics.out_of_plan_edits))
                    + "\n"
                    + ", ".join(metrics.out_of_plan_edits),
                    "$warning",
                )
            )
        if metrics.guard_violations:
            lines.append(
                Content.styled(
                    self._t("index_metrics.guard", count=len(metrics.guard_violations))
                    + "\n"
                    + ", ".join(metrics.guard_violations),
                    "$error",
                )
            )
        return Content("\n").join(lines)

    def _consumption(self) -> Content:
        t = self._t
        rows = [
            t("result.cost_line", cost=attempt_money(self.view, t)),
            t("result.tests_line", tests=self.view.tests or t("spectrum.na")),
        ]
        rows.extend(("", t("index_metrics.title"), *index_lines(t, self.view.index)))
        overhead = self.view.overhead
        if overhead is not None:
            rows.append("")
            rows.append(t("result.overhead_title"))
            rows.extend(t.message(line) for line in overhead_messages(overhead))
        return Content.styled("\n".join(rows), "$text-muted")

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._fill()
        self.call_after_refresh(self._services.result_shown, self.view.run.id)

    def on_resize(self, event: Resize) -> None:
        self.set_class(event.size.width < NARROW_WIDTH, "-narrow")

    def _fill(self) -> None:
        t = self._t
        files = self.query_one("#result-files", DataTable)
        files.add_column(t("result.col_file"), key="file")
        for path in self.view.changed_files:
            files.add_row(path, key=path)
        self.query_one("#result-files-hint").display = not self.view.changed_files
        agents = self.query_one("#result-agents", DataTable)
        agents.add_column(t("result.col_agent"), key="agent")
        agents.add_column(t("result.col_tokens"), key="tokens")
        for agent, tokens in self.view.tokens_by_agent.items():
            agents.add_row(agent, f"{tokens:,}")
        self.query_one("#result-continue", Button).disabled = self.view.follow_up is None
        self._paint_decision()
        if self.view.trial is not None:
            self._paint_trial()
            self.load_handoff()

    def _paint_decision(self) -> None:
        view = self.view
        accept, reject = decision_offers(view)
        content = decision_content(self._t, view, accept or reject)
        widget = self.query_one("#result-decision", Static)
        widget.update(content if content is not None else "")
        widget.display = content is not None
        self.query_one("#result-accept", Button).display = accept
        self.query_one("#result-reject", Button).display = reject
        self.query_one("#result-decision-row").display = content is not None or accept or reject

    def _paint_trial(self) -> None:
        t = self._t
        summary = self.view.trial
        if summary is None:
            return
        trial = summary.trial
        if summary.applied_at:
            state = t("result.trial_applied", at=summary.applied_at[:16].replace("T", " "))
        elif summary.pending:
            state = t("result.trial_pending")
        else:
            when = summary.outcome_at[:16].replace("T", " ")
            state = t(f"result.trial_{summary.outcome}", at=when)
        counts = t(
            "result.trial_summary",
            files=len(trial.changes),
            added=trial.added,
            removed=trial.removed,
        )
        self.query_one("#result-trial-line", Static).update(
            Content.assemble(
                (f"{t('result.trial_title')}  ", "bold $accent"),
                (f"{counts}  ", ""),
                (state, "$text-muted"),
            )
        )
        notes: list[tuple[str, str]] = []
        if trial.dependencies_changed_count:
            notes.append(
                (t("result.trial_dependencies", count=trial.dependencies_changed_count), "$error")
            )
        if trial.state_changed_count:
            notes.append(
                (t("result.trial_state", paths=", ".join(trial.state_changed[:5])), "$error")
            )
        if trial.base_missing:
            notes.append(
                (t("result.trial_base_missing", count=len(trial.base_missing)), "$warning")
            )
        if trial.read_only_breach:
            notes.append((t("result.trial_breach"), "$warning"))
        if summary.drift:
            notes.append((t("result.trial_drift", paths=", ".join(summary.drift[:5])), "$warning"))
        if trial.ignored_changes_count:
            ignored = t(
                "result.trial_ignored",
                count=trial.ignored_changes_count,
                paths=", ".join(trial.ignored_changes[:5]),
            )
            notes.append((ignored, "$warning"))
        if trial.kept:
            notes.append((t("result.trial_kept", path=trial.copy_root), "$text-muted"))
        body = self.query_one("#result-trial-notes", Static)
        body.update(Content("\n").join(Content.styled(text, style) for text, style in notes))
        body.display = bool(notes)
        rejected = summary.outcome == REJECTED
        open_apply = trial.applicable and not summary.applied_at and not rejected
        apply = self.query_one("#result-apply", Button)
        apply.display = open_apply
        apply.disabled = bool(summary.drift)
        self.query_one("#result-discard", Button).display = (
            summary.pending and not summary.applied_at
        )
        self.query_one("#result-copy-commands", Button).display = trial.applicable and not rejected
        actions = self.query_one("#result-trial-actions", FlowRow)
        actions.display = any(button.display for button in actions.query(Button))
        actions.reflow()

    def _paint_handoff(self, handoff: Handoff | None) -> None:
        t = self._t
        self._handoff = handoff
        widget = self.query_one("#result-trial-handoff", Static)
        if handoff is None:
            widget.display = False
            return
        lines = [t("result.trial_commit", subject=handoff.subject)]
        if handoff.branch:
            lines.append(t("result.trial_branch", branch=handoff.branch))
        elif handoff.current_branch:
            lines.append(t("result.trial_current", branch=handoff.current_branch))
        content = Content.styled("\n".join(lines), "$text-muted")
        if handoff.uncommitted:
            warning = t("result.trial_uncommitted", paths=", ".join(handoff.uncommitted[:5]))
            content = Content("\n").join((content, Content.styled(warning, "$warning")))
        if handoff.untracked:
            warning = t("result.trial_untracked", paths=", ".join(handoff.untracked[:5]))
            content = Content("\n").join((content, Content.styled(warning, "$warning")))
        widget.update(content)
        widget.display = True

    @work(thread=True, exclusive=True, group="trial-handoff", exit_on_error=False)
    def load_handoff(self) -> None:
        try:
            handoff = self._services.trial_handoff(self.view.run.id)
        except Exception:
            handoff = None
        self.app.call_from_thread(self._paint_handoff, handoff)

    def confirm_apply(self) -> None:
        summary = self.view.trial
        if summary is None:
            return
        trial = summary.trial
        shown = tuple(f"{change.kind.value}  {change.path}" for change in trial.changes[:12])
        extra = (f"… +{len(trial.changes) - 12}",) if len(trial.changes) > 12 else ()
        self.app.push_screen(
            ConfirmScreen(
                self._t,
                "result.apply_title",
                "result.apply_body",
                "result.apply_confirm",
                "result.apply_cancel",
                {"files": len(trial.changes), "added": trial.added, "removed": trial.removed},
                (*shown, *extra),
            ),
            self.apply_answer,
        )

    def apply_answer(self, confirmed: bool | None) -> None:
        if confirmed:
            self.apply_trial()

    def _refresh(self, view: ResultView | None) -> None:
        if view is None:
            return
        self.view = view
        self.query_one("#result-map-summary", Static).update(self._map_summary())
        self.query_one("#result-consumption", Static).update(self._consumption())
        self.query_one("#result-read-efficiency", Static).update(
            read_efficiency_content(self._t, view.read_efficiency)
        )
        with suppress(NoMatches):
            self.query_one("#result-governor", Static).update(
                governor_content(self._t, view.governor)
            )
        with suppress(NoMatches):
            self.query_one("#result-scout", Static).update(scout_content(self._t, view.scout))
        with suppress(NoMatches):
            self.query_one("#result-metrics", Static).update(metrics_content(self._t, view.metrics))
        self._paint_decision()
        if view.trial is not None:
            self._paint_trial()
            self.load_handoff()

    def confirm_decision(self, accept: bool) -> None:
        prefix = "result.accept" if accept else "result.reject"
        self.app.push_screen(
            ConfirmScreen(
                self._t,
                f"{prefix}_title",
                f"{prefix}_body",
                f"{prefix}_confirm",
                f"{prefix}_cancel",
            ),
            partial(self.decision_answer, accept),
        )

    def decision_answer(self, accept: bool, confirmed: bool | None) -> None:
        if confirmed:
            self.decide(accept)

    @work(thread=True, exclusive=True, group="decision", exit_on_error=False)
    def decide(self, accept: bool) -> None:
        run_id = self.view.run.id
        try:
            if accept:
                self._services.accept_run(run_id)
            else:
                self._services.reject_run(run_id)
        except CuantaError as error:
            failed = self._t("result.decision_failed", error=str(error))
            failed = f"{failed}\n{error.hint}" if error.hint else failed
            self.app.call_from_thread(self.app.notify, failed, severity="error", markup=False)
            return
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        note = "result.accepted_note" if accept else "result.rejected_note"
        self.app.call_from_thread(self.app.notify, self._t(note))
        self.app.call_from_thread(self._refresh, self._services.result_view(run_id))
        self.app.post_message(self.OutcomeChanged())

    @work(thread=True, exclusive=True, group="trial", exit_on_error=False)
    def apply_trial(self) -> None:
        run_id = self.view.run.id
        try:
            files = self._services.apply_trial(run_id)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.app.notify, self._t("result.applied", files=files))
        self.app.call_from_thread(self._refresh, self._services.result_view(run_id))
        self.app.post_message(self.OutcomeChanged())

    @work(thread=True, exclusive=True, group="trial", exit_on_error=False)
    def discard_trial(self) -> None:
        run_id = self.view.run.id
        try:
            self._services.discard_trial(run_id)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.app.notify, self._t("result.discarded"))
        self.app.call_from_thread(self._refresh, self._services.result_view(run_id))
        self.app.post_message(self.OutcomeChanged())

    @work(thread=True, exclusive=True, group="trial-copy", exit_on_error=False)
    def copy_commands(self) -> None:
        try:
            handoff = self._services.trial_handoff(self.view.run.id)
            if handoff is None:
                return
            text = handoff.chained
            self.app.call_from_thread(self.app.copy_to_clipboard, text)
            self._services.copy(text)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.app.notify, self._t("result.commands_copied"))

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        event.stop()
        target = parse_file_link(event.href)
        if target is not None:
            self.open_file(target[0], target[1])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "result-files":
            return
        event.stop()
        path = event.row_key.value
        if path:
            self.open_file(path, 0)

    def open_file(self, path: str, line: int) -> None:
        self.app.push_screen(RunFileScreen(self._services, self._t, self.view.run.id, path, line))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button.id
        if button == "result-close":
            self.action_close()
        elif button == "result-save":
            self.save()
        elif button == "result-copy":
            self.copy_report()
        elif button == "result-continue" and self.view.follow_up is not None:
            self.app.post_message(self.ContinueRequested(self.view.follow_up))
            self.dismiss(None)
        elif button == "result-spectrum":
            self.app.post_message(self.OpenSpectrum(self.view.run.id))
            self.dismiss(None)
        elif button == "result-map":
            self.app.post_message(self.OpenMap())
            self.dismiss(None)
        elif button == "result-export":
            self.export()
        elif button == "result-apply":
            self.confirm_apply()
        elif button == "result-discard":
            self.discard_trial()
        elif button == "result-copy-commands":
            self.copy_commands()
        elif button == "result-accept":
            self.confirm_decision(True)
        elif button == "result-reject":
            self.confirm_decision(False)

    @work(thread=True, exit_on_error=False)
    def save(self) -> None:
        try:
            path = self._services.save_result(self.view.run.id)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.app.notify, self._t("result.saved", path=path))

    @work(thread=True, exit_on_error=False)
    def export(self) -> None:
        try:
            path = self._services.export_result(self.view.run.id)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.app.notify, self._t("result.exported", path=path))

    @work(thread=True, exit_on_error=False)
    def copy_report(self) -> None:
        copied = self._services.copy(self.view.text)
        key = "result.copied" if copied else "result.copy_failed"
        self.app.call_from_thread(self.app.notify, self._t(key))

    def action_close(self) -> None:
        self.dismiss(None)


def estimate_label(t: Catalog, view: ResultView) -> str:
    run = view.run
    if run.estimate_low is None:
        return t("result.estimate_none")
    na = t("spectrum.na")
    low, high = money(run.estimate_low, na), money(run.estimate_high, na)
    span = low if run.estimate_high in (None, run.estimate_low) else f"{low}–{high}"
    if run.estimate_source == "history":
        return t("result.estimate_history", span=span, count=run.estimate_samples)
    return t("result.estimate_plan", span=span)


def error_label(t: Catalog, error: float | None) -> str:
    if error is None:
        return t("spectrum.na")
    return t("result.error_in_range") if error == 0 else f"{error:+.0%}"


def attempt_money(view: ResultView, t: Catalog) -> str:
    run = view.run
    if run.kind != CROSS_KIND or run.parent_id or view.actual_usd == run.cost_usd:
        return run_money(run, t)
    value = money(view.actual_usd, t("spectrum.na"))
    return labeled_money(value, view.actual_usd, view.actual_estimated, view.actual_partial, t)


def decision_offers(view: ResultView) -> tuple[bool, bool]:
    if not view.decidable:
        return False, False
    trial = view.trial
    return trial is None or not trial.trial.applicable, trial is None


def decision_content(t: Catalog, view: ResultView, offered: bool) -> Content | None:
    run = view.run
    if not is_attempt(run):
        return None
    lines: list[Content] = []
    if run.estimate_source:
        actual = money(view.actual_usd, t("spectrum.na"))
        if view.actual_usd is not None and view.actual_estimated:
            actual = t("cost.estimated", cost=actual)
        parts: list[tuple[str, str]] = [
            (f"{t('result.decision_estimate')}  ", "bold"),
            (f"{estimate_label(t, view)}    ", ""),
            (f"{t('result.decision_actual')}  ", "bold"),
            (actual, ""),
        ]
        if run.estimate_low is not None:
            parts.append((f"    {t('result.decision_error')}  ", "bold"))
            parts.append((error_label(t, view.estimate_error), ""))
        lines.append(Content.assemble(*parts))
    when = run.outcome_at[:16].replace("T", " ")
    if run.outcome:
        style = "$success" if run.outcome != REJECTED else "$error"
        lines.append(Content.styled(t(f"result.outcome_{run.outcome}", at=when), style))
    elif offered:
        lines.append(Content.styled(t("result.outcome_pending"), "$text-muted"))
    return Content("\n").join(lines) if lines else None
