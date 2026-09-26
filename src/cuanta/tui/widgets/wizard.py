from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from contextlib import suppress

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.events import Click, Resize
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Button, Checkbox, Input, Label, Select, Static, TextArea

from cuanta.application.assistant import Improvement
from cuanta.application.estimate import Estimate
from cuanta.application.intake import Understanding
from cuanta.application.mandate_flow import MandateOptions, MandatePreview
from cuanta.application.route_apply import RouteOptions
from cuanta.application.routing import RoutePlan
from cuanta.domain.cache import UNKNOWN_PREFIX, PrefixWindow
from cuanta.domain.depth import DEFAULT_DEPTH, DEPTHS, parse_depth, profile, turn_limit
from cuanta.domain.drafts import Draft
from cuanta.domain.intake import GAP_ANSWERS, GAP_FIELD
from cuanta.domain.mandate import (
    DELIVERABLES,
    INVESTIGATION,
    MandateRequest,
    MandateType,
    deliverable_line,
    deliverable_of,
    missing_fields,
    second_field,
)
from cuanta.domain.messages import msg
from cuanta.domain.routing import ENGINE_ORDER
from cuanta.tui.cache_text import prefix_content
from cuanta.tui.fmt import money
from cuanta.tui.i18n import Catalog
from cuanta.tui.screens.confirm import ConfirmScreen
from cuanta.tui.services import Services
from cuanta.tui.widgets.flow import FlowRow

STEPS = ("tell", "confirm", "team")
GUIDED = "guided"
ONE_PAGE = "one_page"
LAYOUTS = (GUIDED, ONE_PAGE)
INTENTS = (
    (MandateType.BUG.value, "✖"),
    (MandateType.FEATURE.value, "✚"),
    (MandateType.REFACTOR.value, "↻"),
    (MandateType.INVESTIGATION.value, "?"),
)
KINDS = tuple(item.value for item in MandateType)
EXAMPLES = 4
AUTOSAVE_S = 1.5
NARROW_STEPS = 96
AUTO_MODEL = ""
NAV_KEYS = ("wizard.next", "wizard.understand", "wizard.launch", "wizard.back")
NAV_PADDING = 6


def new_draft_id() -> str:
    return f"d{secrets.token_hex(6)}"


def parse_cap(text: str) -> float | None:
    cleaned = text.strip().lstrip("$").strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None


class IntentCard(Static):
    class Chosen(Message):
        def __init__(self, kind: str) -> None:
            super().__init__()
            self.kind = kind

    def __init__(self, kind: str, body: Content) -> None:
        super().__init__(body, id=f"intent-{kind}", classes="intent-card")
        self.kind = kind

    def on_click(self, event: Click) -> None:
        event.stop()
        self.post_message(self.Chosen(self.kind))


class MandateWizard(Vertical):
    class Launch(Message):
        def __init__(self, request: MandateRequest, options: MandateOptions) -> None:
            super().__init__()
            self.request = request
            self.options = options

    class InitWanted(Message):
        pass

    class EvidenceWanted(Message):
        def __init__(self, source: str) -> None:
            super().__init__()
            self.source = source

    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="wizard")
        self._services = services
        self._t = catalog
        self.layout_name = GUIDED
        self.step = 0
        self.kind = ""
        self.suggested_kind = ""
        self.engine = ""
        self.engines: tuple[str, ...] = ()
        self.budget = 0.0
        self.max_turns = 0
        self.depth = DEFAULT_DEPTH.value
        self.custom_cap = False
        self.no_cap = False
        self.understanding: Understanding | None = None
        self.plan: RoutePlan | None = None
        self.estimate: Estimate | None = None
        self.proposal: Improvement | None = None
        self.forge_ready = True
        self.simple = False
        self.init_estimate: float | None = None
        self.draft_id = new_draft_id()
        self.example = 0
        self.last_story = ""
        self._autosave: Timer | None = None
        self._team_lock = asyncio.Lock()
        self._team_revision = 0

    def compose(self) -> ComposeResult:
        t = self._t
        with Horizontal(id="wizard-steps"):
            yield Static("", id="wiz-step-of")
            for index, name in enumerate(STEPS):
                yield Button(
                    f"{index + 1} {t(f'wizard.step_{name}')}",
                    id=f"wiz-step-{index}",
                    classes="chip step-chip",
                    compact=True,
                )
        yield Static("", id="wiz-mode")
        with Horizontal(id="wizard-main"):
            with VerticalScroll(id="wizard-body"):
                with Vertical(id="step-tell", classes="wiz-section"):
                    yield from self._tell()
                with Vertical(id="step-confirm", classes="wiz-section"):
                    yield from self._confirm()
                with Vertical(id="step-team", classes="wiz-section"):
                    yield from self._team()
                with Vertical(id="preview-card", classes="card"):
                    yield Static(t("mandate.preview_title"), classes="card-title")
                    yield Static("", id="preview-command", classes="command")
                    yield TextArea(id="preview-prompt", read_only=True, soft_wrap=True)
            with Vertical(id="wiz-summary", classes="card"):
                yield Static(t("wizard.summary_title"), classes="card-title")
                yield Static("", id="summary-body")
                with Horizontal(id="summary-actions"):
                    yield Button(t("wizard.preview"), id="wiz-preview", compact=True)
                    yield Button(
                        t("wizard.launch"), id="wiz-launch", variant="primary", compact=True
                    )
        yield Static("", id="wiz-error", classes="field-error")
        with Horizontal(id="wizard-nav"):
            yield Button(t("wizard.back"), id="wiz-back", compact=True)
            yield Button(t("wizard.next"), id="wiz-next", variant="primary", compact=True)

    def _tell(self) -> ComposeResult:
        t = self._t
        with Vertical(id="forge-gate", classes="card"):
            yield Static(t("wizard.gate_title"), classes="card-title")
            yield Static(Content.styled(t("wizard.gate_body"), "$text-muted"))
            with Vertical(id="forge-gate-actions"):
                yield Button(t("wizard.gate_init"), id="wiz-init", variant="primary", compact=True)
                yield Button(t("wizard.gate_simple"), id="wiz-simple", compact=True)
        yield Static(t("wizard.tell_title"), classes="card-title")
        yield Static(Content.styled(t("wizard.tell_help"), "$text-muted"), id="wiz-tell-help")
        yield TextArea(id="wiz-story", soft_wrap=True, show_line_numbers=False)
        with FlowRow(id="story-chips", classes="chips"):
            yield Button(
                t("wizard.another_example"), id="wiz-example", classes="chip", compact=True
            )
            yield Button(t("wizard.reuse_last"), id="wiz-reuse", classes="chip", compact=True)
            yield Button(t("wizard.understand"), id="wiz-understand", classes="chip", compact=True)
        yield Static(Content.styled(t("wizard.drafts_title"), "$text-muted"), id="drafts-title")
        yield FlowRow(id="draft-chips", classes="chips")

    def _confirm(self) -> ComposeResult:
        t = self._t
        yield Static(t("wizard.confirm_title"), classes="card-title")
        yield Static("", id="wiz-detected")
        with Horizontal(id="intent-cards"):
            for kind, glyph in INTENTS:
                body = Content.assemble(
                    (f"{glyph}  {t(f'wizard.intent_{kind}')}\n", "bold"),
                    (t(f"wizard.intent_{kind}_help"), "$text-muted"),
                )
                yield IntentCard(kind, body)
        yield Label(t("wizard.bug_what"), id="wiz-what-label")
        yield TextArea(id="wiz-what", soft_wrap=True, show_line_numbers=False)
        yield Label(t("wizard.bug_second"), id="wiz-why-label")
        yield Static(Content.styled(t("wizard.bug_second_help"), "$text-muted"), id="wiz-why-help")
        yield TextArea(id="wiz-why", soft_wrap=True, show_line_numbers=False)
        with FlowRow(classes="chips", id="wiz-evidence-actions"):
            yield Button(t("mandate.attach"), id="wiz-attach", classes="chip", compact=True)
            yield Button(t("mandate.last_failure"), id="wiz-failure", classes="chip", compact=True)
        with Vertical(id="wiz-deliverable-field"):
            yield Label(t("wizard.deliverable"))
            yield Select(
                [(t(f"wizard.deliverable_{key}"), key) for key in DELIVERABLES],
                value="report",
                allow_blank=False,
                id="wiz-deliverable",
            )
        yield Label(t("wizard.bug_where"), id="wiz-where-label")
        yield Input(placeholder=t("wizard.bug_where_example"), id="wiz-where")
        yield Static(Content.styled(t("wizard.places_title"), "$text-muted"), id="places-title")
        yield FlowRow(id="where-chips", classes="chips")
        yield Label(t("wizard.out_of_scope"))
        yield Input(placeholder=t("mandate.out_of_scope_placeholder"), id="wiz-out")
        yield Static("", id="wiz-out-error", classes="field-error")
        with Vertical(id="limits-constraints"):
            yield Label(t("wizard.constraints"))
            yield Input(placeholder=t("mandate.constraints_placeholder"), id="wiz-constraints")
        with Vertical(id="limits-tests"):
            yield Label(t("wizard.tests"))
            yield Input(placeholder=t("mandate.tests_placeholder"), id="wiz-tests")
        yield Static(t("wizard.missing_title"), id="missing-title", classes="card-title")
        yield Vertical(id="missing-rows")
        with FlowRow(classes="chips"):
            yield Button(
                t("wizard.refine"),
                id="wiz-improve",
                classes="chip",
                compact=True,
                tooltip=t("tips.improve"),
            )
            yield Button(t("wizard.sent"), id="wiz-sent", classes="chip", compact=True)
        yield Static("", id="wiz-improve-note")
        with FlowRow(id="improve-actions", classes="chips"):
            yield Button(t("wizard.accept"), id="wiz-accept", classes="chip", compact=True)
            yield Button(t("wizard.discard"), id="wiz-discard", classes="chip", compact=True)
        yield Static("", id="wiz-sent-body")

    def _team(self) -> ComposeResult:
        t = self._t
        yield Static(t("wizard.team_title"), classes="card-title")
        yield Label(t("wizard.team_engine"), id="wiz-engine-label")
        yield Select([], allow_blank=True, disabled=True, id="wiz-engine")
        with Vertical(id="team-cards"):
            yield Static("", id="team-simple-note")
        yield Static(t("wizard.depth_title"), classes="card-title")
        with FlowRow(id="depth-row", classes="chips"):
            for depth in DEPTHS:
                yield Button(
                    t(f"wizard.depth_{depth.value}"),
                    id=f"depth-{depth.value}",
                    classes="chip depth-chip",
                    compact=True,
                )
        yield Static("", id="wiz-depth-note")
        yield Static("", id="wiz-estimate")
        yield Static("", id="wiz-prefix")
        yield Static("", id="wiz-cap-note")
        with Horizontal(id="cap-row"):
            yield Button(t("wizard.cap_custom"), id="wiz-custom-cap", classes="chip", compact=True)
            yield Checkbox(t("wizard.no_cap"), False, id="wiz-no-cap", compact=True)
        with Horizontal(id="wiz-cap-field"):
            yield Label(t("wizard.cap_label"))
            yield Input(placeholder=t("wizard.cap_placeholder"), id="wiz-budget")
        with FlowRow(id="team-actions", classes="chips"):
            yield Button(t("wizard.preview"), id="wiz-team-preview", classes="chip", compact=True)

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._start()

    def _start(self) -> None:
        self.apply_kind()
        self.query_one("#improve-actions").display = False
        self.query_one("#wiz-sent-body").display = False
        self.query_one("#forge-gate").display = False
        self.query_one("#preview-card").display = False
        self.query_one("#wiz-cap-field").display = False
        self.query_one("#team-cards").display = False
        self.query_one("#team-simple-note").display = False
        self._size_nav()
        self._show_example()
        self._paint()
        self.load_drafts()

    def _size_nav(self) -> None:
        widest = max(len(self._t(key)) for key in NAV_KEYS) + NAV_PADDING
        for selector in ("#wiz-next", "#wiz-back"):
            self.query_one(selector, Button).styles.width = widest

    def on_resize(self, event: Resize) -> None:
        with suppress(NoMatches):
            self._paint()

    def configure(
        self,
        engine: str,
        budget: float,
        engines: tuple[tuple[str, bool], ...],
        forge_ready: bool = True,
        init_estimate: float | None = None,
        max_turns: int = 0,
    ) -> None:
        ready = {name for name, installed in engines if installed}
        self.engines = tuple(name for name in ENGINE_ORDER if name in ready)
        self.engine = engine if engine in self.engines else next(iter(self.engines), "")
        selector = self.query_one("#wiz-engine", Select)
        selector.set_options([(self._t(f"wizard.engine_{name}"), name) for name in self.engines])
        selector.disabled = not self.engines
        if self.engine:
            selector.value = self.engine
        self.budget = budget
        self.max_turns = max_turns
        self.forge_ready = forge_ready
        self.init_estimate = init_estimate
        label = (
            self._t("wizard.gate_init_cost", cost=money(init_estimate))
            if init_estimate is not None
            else self._t("wizard.gate_init")
        )
        self.query_one("#wiz-init", Button).label = label
        self._paint()

    def set_layout(self, name: str) -> None:
        self.layout_name = name if name in LAYOUTS else GUIDED
        self._paint()
        if self.one_page and self.understanding is not None:
            self.refresh_team()

    @property
    def one_page(self) -> bool:
        return self.layout_name == ONE_PAGE

    @property
    def gated(self) -> bool:
        return not self.forge_ready and not self.simple

    @property
    def story(self) -> str:
        return self.query_one("#wiz-story", TextArea).text.strip()

    def choose_simple(self) -> None:
        self.simple = True
        self.error("")
        self._paint()

    def request(self) -> MandateRequest:
        kind = self.kind
        second = self.query_one("#wiz-why", TextArea).text.strip()
        target = second_field(kind)
        tests = self.query_one("#wiz-tests", Input).value.strip()
        if kind == INVESTIGATION:
            chosen = self.query_one("#wiz-deliverable", Select).value
            tests = deliverable_line(chosen if isinstance(chosen, str) else "report")
        return MandateRequest(
            type=kind,
            what=self.query_one("#wiz-what", TextArea).text.strip(),
            why=second if target == "why" else "",
            where=self.query_one("#wiz-where", Input).value.strip(),
            constraints=(
                second
                if target == "constraints"
                else self.query_one("#wiz-constraints", Input).value.strip()
            ),
            tests=second if target == "tests" else tests,
            out_of_scope=self.query_one("#wiz-out", Input).value.strip(),
        )

    def apply_kind(self) -> None:
        t = self._t
        kind = self.kind if self.kind in KINDS else MandateType.BUG.value
        self.query_one("#wiz-what-label", Label).update(t(f"wizard.{kind}_what"))
        self.query_one("#wiz-what", TextArea).placeholder = t(f"wizard.{kind}_what_example")
        self.query_one("#wiz-why-label", Label).update(t(f"wizard.{kind}_second"))
        self.query_one("#wiz-why-help", Static).update(
            Content.styled(t(f"wizard.{kind}_second_help"), "$text-muted")
        )
        self.query_one("#wiz-why", TextArea).placeholder = t(f"wizard.{kind}_second_example")
        self.query_one("#wiz-where-label", Label).update(t(f"wizard.{kind}_where"))
        self.query_one("#wiz-where", Input).placeholder = t(f"wizard.{kind}_where_example")
        self.query_one("#wiz-evidence-actions").display = kind == MandateType.BUG.value
        self.query_one("#wiz-deliverable-field").display = kind == INVESTIGATION
        target = second_field(kind)
        self.query_one("#limits-constraints").display = (
            target != "constraints" and kind != INVESTIGATION
        )
        self.query_one("#limits-tests").display = target != "tests" and kind != INVESTIGATION
        self.show_missing()

    def cap(self) -> float | None:
        if self.no_cap:
            return 0.0
        if self.custom_cap:
            return parse_cap(self.query_one("#wiz-budget", Input).value)
        return profile(parse_depth(self.depth), self.kind).cost_cap_usd

    def options(self) -> MandateOptions:
        chosen = self.query_one("#wiz-engine", Select).value
        engine = chosen if isinstance(chosen, str) and chosen in self.engines else self.engine
        pinned = (
            tuple(
                (select.id.removeprefix("override-"), value)
                for select in self.query_one("#team-cards").query(Select)
                if select.id and isinstance(value := select.value, str) and value
            )
            if self.plan is not None and not self.simple
            else ()
        )
        custom = parse_cap(self.query_one("#wiz-budget", Input).value) if self.custom_cap else None
        understood = self.understanding
        return MandateOptions(
            engine=engine,
            budget_usd=custom or 0.0,
            route=RouteOptions(
                role_models=pinned,
                scope=understood.scope if understood is not None else None,
                risk=understood.risk if understood is not None else None,
            ),
            simple=self.simple,
            depth=self.depth,
            max_turns=self.max_turns,
            no_cap=self.no_cap,
            intake_scope=understood.intake_scope if understood is not None else "",
        )

    def _paint(self) -> None:
        t = self._t
        one_page = self.one_page
        self.query_one("#wiz-step-of", Static).update(
            Content.styled(t("wizard.step_of", step=self.step + 1, total=len(STEPS)), "$text-muted")
        )
        narrow = self.size.width and self.size.width < NARROW_STEPS
        self.query_one("#wizard-steps").display = not one_page
        self.query_one("#wiz-step-of", Static).display = not narrow
        for index, name in enumerate(STEPS):
            chip = self.query_one(f"#wiz-step-{index}", Button)
            full = f"{index + 1} {t(f'wizard.step_{name}')}"
            chip.label = full if index == self.step or not narrow else str(index + 1)
            chip.disabled = index > self.step
            chip.set_class(index == self.step, "-current")
            chip.set_class(index < self.step, "-done")
        for index, name in enumerate(STEPS):
            self.query_one(f"#step-{name}").display = one_page or index == self.step
        self.query_one("#wiz-summary").display = one_page
        self.query_one("#wizard-nav").display = not one_page
        self.query_one("#wiz-understand").display = one_page
        self.query_one("#wiz-back", Button).display = self.step > 0
        labels = {0: "wizard.understand", len(STEPS) - 1: "wizard.launch"}
        self.query_one("#wiz-next", Button).label = t(labels.get(self.step, "wizard.next"))
        for card in self.query(IntentCard):
            card.set_class(card.kind == self.kind, "-chosen")
            card.set_class(card.kind == self.suggested_kind and not self.kind, "-suggested")
        for depth in DEPTHS:
            chip = self.query_one(f"#depth-{depth.value}", Button)
            chip.set_class(depth.value == self.depth, "-current")
        self.query_one("#forge-gate").display = self.gated
        mode = Content.styled(t("wizard.simple_mode"), "$warning") if self.simple else ""
        self.query_one("#wiz-mode", Static).update(mode)
        self.query_one("#wiz-mode").display = self.simple
        self.query_one("#wiz-reuse").display = bool(self.last_story)
        self._paint_depth()
        self._paint_summary()

    def _paint_depth(self) -> None:
        t = self._t
        chosen = profile(parse_depth(self.depth), self.kind)
        self.query_one("#wiz-depth-note", Static).update(
            Content.styled(
                t(
                    f"wizard.depth_{chosen.depth.value}_help",
                    reads=chosen.read_budget,
                    tier=t(f"models.tier_{chosen.tier_cap.value}"),
                ),
                "$text-muted",
            )
        )
        if self.no_cap:
            note = t("wizard.cap_none")
        elif self.custom_cap:
            value = parse_cap(self.query_one("#wiz-budget", Input).value)
            note = t("wizard.cap_note", cap=money(value)) if value else t("wizard.bad_cap")
        else:
            note = t("wizard.cap_depth", cap=money(chosen.cost_cap_usd))
        limit = turn_limit(chosen, self.max_turns)
        if self.engine == "claude" and limit > 0:
            note = f"{note}  ·  {t('wizard.turn_limit', turns=limit)}"
        self.query_one("#wiz-cap-note", Static).update(Content.styled(note, "$text-muted"))
        self.query_one("#wiz-cap-field").display = self.custom_cap and not self.no_cap

    def _paint_summary(self) -> None:
        if not self.one_page:
            return
        t = self._t
        kind = t(f"wizard.intent_{self.kind}") if self.kind else t("wizard.untyped")
        team = (
            ", ".join(t(f"models.role_{route.role.value}") for route in self.plan.routes)
            if self.plan is not None and not self.simple
            else t("wizard.simple_mode_short")
            if self.simple
            else "–"
        )
        estimate = t.message(self.estimate.message) if self.estimate is not None else "–"
        rows = (
            (t("wizard.summary_type"), kind),
            (t("wizard.summary_team"), team),
            (t("wizard.summary_depth"), t(f"wizard.depth_{self.depth}")),
            (t("wizard.summary_estimate"), estimate),
        )
        body = Content("\n").join(
            Content.assemble((f"{label}\n", "$text-muted"), (value, "bold"))
            for label, value in rows
        )
        self.query_one("#summary-body", Static).update(body)

    def error(self, key: str, **values: object) -> None:
        text = Content.styled(self._t(key, **values), "$error") if key else ""
        self.query_one("#wiz-error", Static).update(text)

    def _show_example(self) -> None:
        text = self._t(f"wizard.example_{self.example % EXAMPLES + 1}")
        self.query_one("#wiz-story", TextArea).placeholder = self._t(
            "wizard.example_prefix", text=text
        )

    def next_example(self) -> None:
        self.example = (self.example + 1) % EXAMPLES
        self._show_example()

    def check_story(self) -> bool:
        if self.gated:
            self.error("wizard.gate_required")
            return False
        if not self.story:
            self.error("wizard.tell_required")
            return False
        self.error("")
        return True

    def check_step(self) -> bool:
        return self.check_story() if self.step == 0 else self.check_request()

    def check_request(self) -> bool:
        request = self.request()
        fields = {
            "what": "#wiz-what",
            "why": "#wiz-why",
            "tests": "#wiz-why",
            "constraints": "#wiz-why",
            "out_of_scope": "#wiz-out",
        }
        for selector in set(fields.values()):
            self.query_one(selector).remove_class("-invalid")
        self.query_one("#wiz-out-error", Static).update("")
        missing = missing_fields(request)
        if missing:
            field = missing[0]
            if self.step != 1:
                self.go(1)
            if field in ("why", "tests", "constraints"):
                label = self._t(f"wizard.{self.kind}_second")
            elif field == "what":
                label = self._t(f"wizard.{self.kind}_what")
            elif field == "type":
                label = self._t("wizard.intent_title")
            else:
                label = self._t("wizard.out_of_scope")
            self.error("wizard.missing_field", field=label)
            if field == "out_of_scope":
                self.query_one("#wiz-out-error", Static).update(
                    Content.styled(self._t("mandate.required"), "$error")
                )
            target_selector = fields.get(field)
            if target_selector is not None:
                widget = self.query_one(target_selector)
                widget.add_class("-invalid")
                widget.focus()
            return False
        if self.custom_cap and not self.no_cap and self.cap() is None:
            self.error("wizard.bad_cap")
            return False
        self.error("")
        return True

    def go(self, step: int) -> None:
        self.step = max(0, min(step, len(STEPS) - 1))
        self._paint()
        if STEPS[self.step] == "team":
            self.refresh_team()

    def refresh_team(self) -> None:
        self._team_revision += 1
        self.plan = None
        self.estimate = None
        self.query_one("#team-cards").display = False
        self.query_one("#wiz-estimate", Static).update("")
        self.query_one("#wiz-prefix", Static).update(prefix_content(self._t, UNKNOWN_PREFIX))
        self._paint_summary()
        if self.simple:
            self.show_simple_team()
        elif self.kind:
            self.load_team()
        self.load_prefix()

    @work(thread=True, exclusive=True, group="prefix", exit_on_error=False)
    def load_prefix(self) -> None:
        engine = self.engine
        revision = self._team_revision
        try:
            window = self._services.prefix_window(engine)
        except Exception:
            window = UNKNOWN_PREFIX
        self._call(self.show_prefix, window, engine, revision)

    def show_prefix(self, window: PrefixWindow, engine: str, revision: int) -> None:
        if engine != self.engine or revision != self._team_revision:
            return
        with suppress(NoMatches):
            self.query_one("#wiz-prefix", Static).update(prefix_content(self._t, window))

    def advance(self) -> None:
        if self.step == 0:
            if self.check_step():
                self.understand()
            return
        if not self.check_step():
            return
        if self.step == len(STEPS) - 1:
            self.launch()
            return
        self.go(self.step + 1)

    def launch(self) -> None:
        if self.gated:
            self.error("wizard.gate_required")
            return
        if not self.check_request():
            return
        story = self.story or self.request().what
        self.post_message(self.Launch(self.request(), self.options()))
        self.remember_launch(self.draft_id, story)
        self.reset(story)

    def reset(self, last: str = "") -> None:
        if self._autosave is not None:
            self._autosave.stop()
            self._autosave = None
        if last:
            self.last_story = last
        self.draft_id = new_draft_id()
        self.understanding = None
        self._team_revision += 1
        self.plan = None
        self.estimate = None
        self.proposal = None
        self.kind = ""
        self.suggested_kind = ""
        self.depth = DEFAULT_DEPTH.value
        self.custom_cap = False
        self.no_cap = False
        self.query_one("#wiz-story", TextArea).text = ""
        for selector in ("#wiz-what", "#wiz-why"):
            self.query_one(selector, TextArea).text = ""
        for selector in ("#wiz-where", "#wiz-out", "#wiz-constraints", "#wiz-tests", "#wiz-budget"):
            self.query_one(selector, Input).value = ""
        self.query_one("#wiz-no-cap", Checkbox).value = False
        self.query_one("#wiz-deliverable", Select).value = "report"
        self.query_one("#wiz-detected", Static).update("")
        self.query_one("#preview-card").display = False
        self.query_one("#team-cards").display = False
        self.query_one("#where-chips", FlowRow).remove_children()
        self.query_one("#wiz-estimate", Static).update("")
        self.discard_proposal()
        self.next_example()
        self.apply_kind()
        self.error("")
        self.step = 0
        self._paint()
        self.load_drafts()

    def on_intent_card_chosen(self, message: IntentCard.Chosen) -> None:
        message.stop()
        if self.gated:
            self.error("wizard.gate_required")
            return
        self.kind = message.kind
        self.apply_kind()
        self.error("")
        self._paint()
        if self.one_page:
            self.refresh_team()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "wiz-budget":
            self._paint_depth()
            return
        if not self.one_page:
            self.advance()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "wiz-budget":
            self._paint_depth()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "wiz-engine" or not isinstance(event.value, str):
            return
        if event.value not in self.engines or event.value == self.engine:
            return
        self.engine = event.value
        self._paint_depth()
        if self.kind and (self.one_page or STEPS[self.step] == "team"):
            self.refresh_team()
        else:
            self._team_revision += 1

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "wiz-story":
            return
        if self._autosave is not None:
            self._autosave.stop()
        self._autosave = self.set_timer(AUTOSAVE_S, self.autosave)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        if event.checkbox.id != "wiz-no-cap":
            return
        if not event.value:
            self.no_cap = False
            self._paint_depth()
            return
        if self.no_cap:
            return
        self.app.push_screen(
            ConfirmScreen(
                self._t,
                "wizard.no_cap_title",
                "wizard.no_cap_body",
                "wizard.no_cap_confirm",
                "wizard.no_cap_cancel",
            ),
            self.no_cap_answer,
        )

    def no_cap_answer(self, confirmed: bool | None) -> None:
        self.no_cap = bool(confirmed)
        if not confirmed:
            self.query_one("#wiz-no-cap", Checkbox).value = False
        self._paint_depth()
        self._paint_summary()

    def _call(self, callback: Callable[..., object], *args: object, **kwargs: object) -> None:
        if not self.app.is_running:
            return
        with suppress(RuntimeError):
            self.app.call_from_thread(callback, *args, **kwargs)

    @work(thread=True, exclusive=True, group="understand", exit_on_error=False)
    def understand(self) -> None:
        story = self.story
        self._call(self._busy, True)
        try:
            understanding = self._services.understand(story)
        except Exception as error:
            self._call(self._busy, False)
            self._call(self.app.notify, str(error), severity="error")
            return
        self._call(self.apply_understanding, understanding)

    def _busy(self, busy: bool) -> None:
        with suppress(NoMatches):
            key = "wizard.understanding" if busy else ""
            text = Content.styled(self._t(key), "$accent") if key else ""
            self.query_one("#wiz-detected", Static).update(text)

    def apply_understanding(self, understanding: Understanding) -> None:
        with suppress(NoMatches):
            self._apply(understanding)

    def _apply(self, understanding: Understanding) -> None:
        self.understanding = understanding
        confident = not understanding.needs_confirm
        self.kind = understanding.kind.option if confident else ""
        self.suggested_kind = understanding.kind.option
        self.depth = understanding.depth.option if understanding.depth.option else self.depth
        self._fill(understanding.request(understanding.kind.option))
        self._show_detected()
        self.apply_kind()
        self._show_places(understanding.places)
        if self.one_page:
            self._paint()
            self.refresh_team()
        else:
            self.go(1)

    def _show_detected(self) -> None:
        understanding = self.understanding
        if understanding is None:
            return
        t = self._t
        kind = t(f"wizard.intent_{understanding.kind.option}")
        confidence = f"{understanding.kind.probability:.0%}"
        if understanding.needs_confirm:
            head = (t("wizard.type_unsure", kind=kind, confidence=confidence), "$warning")
        else:
            head = (t("wizard.detected", kind=kind, confidence=confidence), "bold")
        parts: list[tuple[str, str]] = [head]
        if understanding.is_read_only:
            parts.append((f"  · {t('wizard.read_only_badge')}", "$accent"))
        by = t("wizard.detected_by", backend=understanding.backend)
        if understanding.cost_usd > 0:
            by = f"{by} · {money(understanding.cost_usd)}"
        parts.append((f"\n{by}", "$text-muted"))
        if understanding.fallback_error:
            fallback = t.message(
                msg(
                    "instinct.fallback",
                    backend=understanding.fallback_from.capitalize(),
                    error=understanding.fallback_error,
                )
            )
            parts.append((f"\n{fallback}", "$warning"))
        self.query_one("#wiz-detected", Static).update(Content.assemble(*parts))

    def _fill(self, request: MandateRequest) -> None:
        target = second_field(request.type)
        second = {"why": request.why, "tests": request.tests, "constraints": request.constraints}
        self.query_one("#wiz-what", TextArea).text = request.what
        self.query_one("#wiz-why", TextArea).text = second[target]
        self.query_one("#wiz-where", Input).value = request.where
        self.query_one("#wiz-out", Input).value = request.out_of_scope
        if target != "constraints":
            self.query_one("#wiz-constraints", Input).value = request.constraints
        if target != "tests" and request.type != INVESTIGATION:
            self.query_one("#wiz-tests", Input).value = request.tests
        if request.type == INVESTIGATION:
            self.query_one("#wiz-deliverable", Select).value = deliverable_of(request.tests)

    def _show_places(self, places: tuple[str, ...]) -> None:
        row = self.query_one("#where-chips", FlowRow)
        row.remove_children()
        row.mount_all(
            Button(path, id=f"place-{index}", classes="chip place-chip", compact=True)
            for index, path in enumerate(places)
        )
        self.query_one("#places-title").display = bool(places)

    def missing(self) -> tuple[str, ...]:
        if self.understanding is None or not self.kind:
            return ()
        return self.understanding.missing_for(self.kind)

    def show_missing(self) -> None:
        t = self._t
        rows = self.query_one("#missing-rows", Vertical)
        rows.remove_children()
        gaps = self.missing()
        self.query_one("#missing-title").display = bool(gaps)
        widgets: list[Static | FlowRow] = []
        for gap in gaps:
            widgets.append(
                Static(Content.styled(t(f"wizard.gap_{gap}"), "$warning"), classes="gap-label")
            )
            widgets.append(
                FlowRow(
                    *(
                        Button(
                            t(f"wizard.answer_{gap}_{answer}"),
                            id=f"answer-{gap}-{answer}",
                            classes="chip answer-chip",
                            compact=True,
                        )
                        for answer in GAP_ANSWERS[gap]
                    ),
                    classes="chips gap-row",
                )
            )
        rows.mount_all(widgets)

    def answer(self, gap: str, answer: str) -> None:
        if gap == "evidence":
            self.post_message(self.EvidenceWanted("attach" if answer == "attach" else "failure"))
            return
        if gap == "deliverable":
            self.query_one("#wiz-deliverable", Select).value = answer
        else:
            text = self._t(f"wizard.answer_{gap}_{answer}")
            field = GAP_FIELD[gap]
            if field == second_field(self.kind):
                area = self.query_one("#wiz-why", TextArea)
                area.text = "\n".join(part for part in (area.text.strip(), text) if part)
            else:
                selector = "#wiz-tests" if field == "tests" else "#wiz-constraints"
                self._append(selector, text)
        if self.understanding is not None:
            self.understanding = self.understanding.answered(self.kind, gap)
        self.show_missing()

    @work(thread=True, exclusive=True, group="team", exit_on_error=False)
    def load_team(self) -> None:
        revision = self._team_revision
        try:
            request = self.request()
            options = self.options()
            plan, estimate = self._services.team_plan(request, options)
            view = self._services.models_view(False)
        except Exception as error:
            self._call(self.app.notify, str(error), severity="error")
            return
        models = tuple(entry.id for entry in view.entries if entry.engine == options.engine)
        self._call(self.show_team, plan, estimate, models, options.engine, revision)

    async def show_team(
        self,
        plan: RoutePlan,
        estimate: Estimate,
        models: tuple[str, ...],
        engine: str,
        revision: int,
    ) -> None:
        async with self._team_lock:
            if engine != self.engine or revision != self._team_revision or not self.kind:
                return
            with suppress(NoMatches):
                await self._show_team(plan, estimate, models, engine, revision)

    async def _show_team(
        self,
        plan: RoutePlan,
        estimate: Estimate,
        models: tuple[str, ...],
        engine: str,
        revision: int,
    ) -> None:
        t = self._t
        costs = {item.role: item for item in estimate.roles}
        cards = self.query_one("#team-cards", Vertical)
        await cards.remove_children(".team-card")
        if engine != self.engine or revision != self._team_revision or not self.kind:
            return
        widgets: list[Horizontal] = []
        selects: list[Select[str]] = []
        for route in plan.routes:
            model = route.model.id if route.model else t("wizard.engine_default")
            tier = t(f"models.tier_{route.tier.value}") if route.tier else "–"
            cost = costs.get(route.role)
            spread = (
                t("wizard.role_cost", low=money(cost.low), high=money(cost.high))
                if cost is not None and cost.low is not None
                else ""
            )
            body = Content.assemble(
                (f"{t(f'models.role_{route.role.value}')}", "bold"),
                (f"  {spread}\n" if spread else "\n", "$text-muted"),
                (f"{model} · {tier}\n", "$accent"),
                (t.message(route.reason), "$text-muted"),
            )
            options = [(t("wizard.keep_plan"), AUTO_MODEL), *((name, name) for name in models)]
            select = Select(
                options,
                id=f"override-{route.role.value}",
                allow_blank=False,
                value=AUTO_MODEL,
            )
            selects.append(select)
            widgets.append(
                Horizontal(
                    Static(body, classes="team-body"),
                    select,
                    classes="team-card card",
                )
            )
        await cards.mount_all(widgets)
        await asyncio.gather(*(select._mounted_event.wait() for select in selects))
        if engine != self.engine or revision != self._team_revision or not self.kind:
            return
        self.plan = plan
        self.estimate = estimate
        self.query_one("#team-simple-note").display = False
        cards.display = True
        text = t.message(estimate.message)
        if estimate.over:
            text = f"{text}  {t('wizard.over_cap', cap=money(estimate.cap))}"
        style = "$warning" if estimate.over else "$text-muted"
        self.query_one("#wiz-estimate", Static).update(Content.styled(text, style))
        self._paint_summary()

    def show_simple_team(self) -> None:
        cards = self.query_one("#team-cards", Vertical)
        for card in cards.query(".team-card"):
            card.display = False
        note = self.query_one("#team-simple-note", Static)
        note.update(Content.styled(self._t("wizard.simple_team"), "$warning"))
        note.display = True
        cards.display = True
        self.query_one("#wiz-estimate", Static).update("")

    def choose_depth(self, depth: str) -> None:
        self.depth = parse_depth(depth).value
        self._paint()
        if self.kind and (self.one_page or STEPS[self.step] == "team"):
            self.refresh_team()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        event.stop()
        simple = self._actions().get(button)
        if simple is not None:
            simple()
            return
        for prefix, handler in self._prefixed().items():
            if button.startswith(prefix):
                handler(button.removeprefix(prefix), str(event.button.label))
                return

    def _actions(self) -> dict[str, Callable[[], object]]:
        return {
            "wiz-simple": self.choose_simple,
            "wiz-init": lambda: self.post_message(self.InitWanted()),
            "wiz-next": self.advance,
            "wiz-back": lambda: self.go(self.step - 1),
            "wiz-understand": self.understand_now,
            "wiz-launch": self.launch,
            "wiz-preview": self.preview_now,
            "wiz-team-preview": self.preview_now,
            "wiz-example": self.next_example,
            "wiz-reuse": self.reuse_last,
            "wiz-attach": lambda: self.post_message(self.EvidenceWanted("attach")),
            "wiz-failure": lambda: self.post_message(self.EvidenceWanted("failure")),
            "wiz-custom-cap": self.toggle_custom_cap,
            "wiz-improve": lambda: self.improve(
                self.proposal is not None and self.proposal.proposal is None
            ),
            "wiz-accept": self.accept_proposal,
            "wiz-discard": self.discard_proposal,
            "wiz-sent": self.toggle_sent,
        }

    def _prefixed(self) -> dict[str, Callable[[str, str], object]]:
        return {
            "wiz-step-": lambda rest, _: self.go(int(rest)) if int(rest) < self.step else None,
            "draft-": lambda rest, _: self.open_draft(rest),
            "place-": lambda _, label: self._append("#wiz-where", label),
            "answer-": lambda rest, _: self.answer(*rest.partition("-")[::2]),
            "depth-": lambda rest, _: self.choose_depth(rest),
        }

    def understand_now(self) -> None:
        if self.check_story():
            self.understand()

    def preview_now(self) -> None:
        if self.check_request():
            self.load_preview()

    def reuse_last(self) -> None:
        self.query_one("#wiz-story", TextArea).text = self.last_story

    def toggle_custom_cap(self) -> None:
        self.custom_cap = not self.custom_cap
        self._paint_depth()
        if self.custom_cap:
            self.query_one("#wiz-budget", Input).focus()

    def _append(self, selector: str, text: str) -> None:
        field = self.query_one(selector, Input)
        parts = [part.strip() for part in field.value.split(",") if part.strip()]
        if text not in parts:
            parts.append(text)
        field.value = ", ".join(parts)

    def set_evidence(self, text: str, replace_all: bool) -> None:
        area = self.query_one("#wiz-why", TextArea)
        if replace_all:
            area.text = text
            return
        area.text = "\n\n".join(part for part in (area.text.strip(), text.strip()) if part)

    def autosave(self) -> None:
        self._autosave = None
        self.save_draft(self.draft_id, self.story)

    @work(thread=True, exclusive=True, group="drafts", exit_on_error=False)
    def save_draft(self, draft_id: str, story: str) -> None:
        with suppress(Exception):
            self._services.autosave(draft_id, story)

    @work(thread=True, group="drafts-launch", exit_on_error=False)
    def remember_launch(self, draft_id: str, story: str) -> None:
        with suppress(Exception):
            self._services.launched(draft_id, story)

    @work(thread=True, exclusive=True, group="drafts-list", exit_on_error=False)
    def load_drafts(self) -> None:
        try:
            last = self._services.last_story()
            drafts = self._services.drafts(self.draft_id)
        except Exception:
            return
        self._call(self.show_drafts, last, drafts)

    def show_drafts(self, last: str, drafts: tuple[Draft, ...]) -> None:
        with suppress(NoMatches):
            if last and not self.last_story:
                self.last_story = last
            self.query_one("#wiz-reuse").display = bool(self.last_story)
            row = self.query_one("#draft-chips", FlowRow)
            row.remove_children()
            row.mount_all(
                Button(draft.title, id=f"draft-{draft.id}", classes="chip draft-chip", compact=True)
                for draft in drafts
            )
            self.query_one("#drafts-title").display = bool(drafts)

    @work(thread=True, exclusive=True, group="drafts-open", exit_on_error=False)
    def open_draft(self, draft_id: str) -> None:
        try:
            story = self._services.load_draft(draft_id)
        except Exception:
            return
        self._call(self.adopt_draft, draft_id, story)

    def adopt_draft(self, draft_id: str, story: str) -> None:
        with suppress(NoMatches):
            self.draft_id = draft_id
            self.query_one("#wiz-story", TextArea).text = story
            self.load_drafts()

    @work(thread=True, exclusive=True, group="improve", exit_on_error=False)
    def improve(self, spend: bool) -> None:
        try:
            result = self._services.improve(self.request(), spend)
        except Exception as error:
            self._call(self.app.notify, str(error), severity="error")
            return
        self._call(self.show_improvement, result)

    def show_improvement(self, result: Improvement) -> None:
        t = self._t
        self.proposal = result
        note = self.query_one("#wiz-improve-note", Static)
        cost = money(result.estimate, t("spectrum.na"))
        if not result.ran:
            note.update(
                Content.styled(
                    t("wizard.improve_estimate", model=result.model, cost=cost), "$warning"
                )
            )
            self.query_one("#wiz-improve", Button).label = t("wizard.improve_confirm")
            return
        self.query_one("#wiz-improve", Button).label = t("wizard.refine")
        if result.proposal is None or not result.diff:
            note.update(Content.styled(t("wizard.improve_nothing"), "$text-muted"))
            self.proposal = None
            return
        lines: list[Content] = []
        for change in result.diff:
            lines.append(Content.styled(t(f"wizard.field_{change.field}"), "bold"))
            lines.append(Content.styled(f"- {change.before or '–'}", "$error"))
            lines.append(Content.styled(f"+ {change.after}", "$success"))
        note.update(Content("\n").join(lines))
        self.query_one("#improve-actions").display = True

    def accept_proposal(self) -> None:
        if self.proposal is None or self.proposal.proposal is None:
            return
        self._fill(self.proposal.proposal)
        self.discard_proposal()

    def discard_proposal(self) -> None:
        self.proposal = None
        self.query_one("#improve-actions").display = False
        self.query_one("#wiz-improve-note", Static).update("")
        self.query_one("#wiz-improve", Button).label = self._t("wizard.refine")

    def toggle_sent(self) -> None:
        body = self.query_one("#wiz-sent-body", Static)
        body.display = not body.display
        if body.display:
            self.load_sent()

    @work(thread=True, exit_on_error=False)
    def load_sent(self) -> None:
        try:
            text = self._services.assistant_preview(self.request())
        except Exception as error:
            self._call(self.app.notify, str(error), severity="error")
            return
        self._call(self.show_sent, text)

    def show_sent(self, text: str) -> None:
        help_text = self._t("instinct.preview_help")
        body = Content("\n").join([Content.styled(help_text, "$text-muted"), Content(text)])
        self.query_one("#wiz-sent-body", Static).update(body)

    @work(thread=True, exclusive=True, group="wizard-preview", exit_on_error=False)
    def load_preview(self) -> None:
        try:
            preview = self._services.preview_mandate(self.request(), 0, self.options())
        except Exception as error:
            self._call(self.app.notify, str(error), severity="error")
            return
        self._call(self.show_preview, preview)

    def show_preview(self, preview: MandatePreview) -> None:
        t = self._t
        self.query_one("#preview-command", Static).update(
            Content.assemble((f"{t('mandate.command')}  ", "$text-muted"), preview.command)
        )
        self.query_one("#preview-prompt", TextArea).text = preview.prompt
        card = self.query_one("#preview-card")
        card.display = True
        self.call_after_refresh(card.scroll_visible)

    def prefilled(self, request: MandateRequest) -> None:
        self.kind = request.type or self.kind or MandateType.FEATURE.value
        self.suggested_kind = self.kind
        self.understanding = None
        self.query_one("#wiz-story", TextArea).text = request.what
        self._fill(request)
        self.query_one("#wiz-constraints", Input).value = request.constraints
        if request.type != INVESTIGATION:
            self.query_one("#wiz-tests", Input).value = request.tests
        self.apply_kind()
        if self.one_page:
            self._paint()
            self.refresh_team()
        else:
            self.go(1)
