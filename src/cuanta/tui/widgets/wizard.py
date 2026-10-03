from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace

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
from cuanta.application.cross_engine import TEAM_ROLES
from cuanta.application.estimate import Estimate
from cuanta.application.intake import Understanding
from cuanta.application.mandate_flow import MandateOptions, MandatePreview, resolved_profile
from cuanta.application.route_apply import RouteOptions
from cuanta.application.routing import RoutePlan
from cuanta.domain.cache import UNKNOWN_PREFIX, PrefixWindow
from cuanta.domain.change_plan import ChangePlan, apply_overrides, move_plan, path_matches
from cuanta.domain.claude_variants import VARIANTS, resolve_variant
from cuanta.domain.depth import DEFAULT_DEPTH, DEPTHS, parse_depth, profile
from cuanta.domain.drafts import Draft
from cuanta.domain.errors import DomainFailure
from cuanta.domain.guarantees import (
    Guarantee,
    GuaranteeStatus,
    cap_warning,
    engine_guarantees,
    readonly_unavailable,
)
from cuanta.domain.implementation import (
    AUTO_PROFILE,
    FastChoice,
    ImplementationProfile,
    fast_choice,
)
from cuanta.domain.intake import GAP_ANSWERS, GAP_FIELD
from cuanta.domain.limits import (
    NO_LIMITS,
    LimitSettings,
    LimitsMode,
    RunLimits,
    depth_limits,
    limits_message,
)
from cuanta.domain.mandate import (
    DELIVERABLES,
    ESSENTIAL_FIELDS,
    INVESTIGATION,
    MandateRequest,
    MandateType,
    deliverable_line,
    deliverable_of,
    missing_fields,
    second_field,
)
from cuanta.domain.mandate_file import LonePath, NotText, lone_path, trimmed, typed_path
from cuanta.domain.messages import Message as Said
from cuanta.domain.messages import msg
from cuanta.domain.models import ModelEntry
from cuanta.domain.progress import Status
from cuanta.domain.routing import ENGINE_ORDER, Provider, Role
from cuanta.domain.scout import DocsChoice, DocsReason, eligible
from cuanta.domain.team import ProviderAdvice, RoleCard, advice_message, runs_per_role
from cuanta.tui.cache_text import prefix_content
from cuanta.tui.fmt import grouped, money
from cuanta.tui.i18n import Catalog
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
BY_KIND = "auto"
PLAN_MODEL = "plan"
DEPTH_EFFORT = "depth"
NO_CHOICE = frozenset({BY_KIND, PLAN_MODEL, DEPTH_EFFORT})
FAST_OUTPUT = frozenset(name for name in VARIANTS if name.partition("-")[0] == "fast")
MODEL_SELECT = "#wiz-implementation-model"
VARIANT_SELECT = "#wiz-implementation-variant"
MODEL_LABELS = {
    "claude-opus-5-5": "wizard.implementation_opus",
    "claude-sonnet-5": "wizard.implementation_sonnet",
}
NAV_KEYS = ("wizard.next", "wizard.understand", "wizard.launch", "wizard.back")
NAV_PADDING = 6
PLAN_ROLES = ("edit", "read", "guard")
PROFILES = (AUTO_PROFILE, ImplementationProfile.BALANCED.value, ImplementationProfile.FAST.value)
PLAN_DELAY_S = 0.15


def new_draft_id() -> str:
    return f"d{secrets.token_hex(6)}"


def fast_output_model(model: str) -> bool:
    try:
        resolve_variant("fast", model)
    except DomainFailure:
        return False
    return True


LIMIT_INPUTS = ("wiz-limit-budget", "wiz-limit-turns", "wiz-limit-wall")
LIMIT_CELL = 34
NONE_SET = "0"


def limit_text(value: float, whole: bool = False) -> str:
    if whole:
        return str(int(value))
    return f"{value:f}".rstrip("0").rstrip(".")


def parse_limit(text: str, default: float, whole: bool = False) -> float | None:
    cleaned = text.strip().lstrip("$").strip()
    if not cleaned:
        return default
    try:
        value = int(cleaned) if whole else float(cleaned)
    except ValueError:
        return None
    return float(value) if value >= 0 else None


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


class PlanChip(Button):
    def __init__(self, path: str, role: str, index: int, tooltip: str) -> None:
        super().__init__(
            path,
            id=f"plan-{role}-{index}",
            classes="chip plan-chip",
            compact=True,
            tooltip=tooltip,
            disabled=True,
        )
        self.path = path
        self.role = role


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
        self.depth = DEFAULT_DEPTH.value
        self.limits_on = False
        self.limit_settings = LimitSettings()
        self.implementation_profile = ""
        self._preferred_variant = ""
        self._configured_variant = ""
        self.sandbox = False
        self.advice = ""
        self.understanding: Understanding | None = None
        self.plan: RoutePlan | None = None
        self.estimate: Estimate | None = None
        self.scout_launch = False
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
        self._shaped_revision = -1
        self._kept_models: dict[str, str] = {}
        self._kept_engine = ""
        self._cards_engine = ""
        self.role_pins = False
        self.change_plan: ChangePlan | None = None
        self._plan_overrides: dict[str, str] = {}
        self._change_revision = 0
        self._change_timer: Timer | None = None
        self._change_lock = asyncio.Lock()
        self._limits_timer: Timer | None = None
        self.pure = False
        self.document_mode = False
        self._loaded = ""
        self._loaded_path = ""
        self._attached: dict[str, str] = {}
        self._handed: tuple[str, ...] = ()
        self._offered_fast = False
        self._offered_variants = (False, False)

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
                    yield Static("", id="preview-team")
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
        with Horizontal(id="wiz-file-row"):
            yield Input(placeholder=t("wizard.file_placeholder"), id="wiz-file")
            yield Button(t("wizard.load_file"), id="wiz-load", compact=True)
        yield Static("", id="wiz-file-note")
        with FlowRow(id="story-chips", classes="chips"):
            yield Button(t("wizard.understand"), id="wiz-understand", classes="chip", compact=True)
            yield Button(
                t("wizard.another_example"), id="wiz-example", classes="chip", compact=True
            )
            yield Button(t("wizard.reuse_last"), id="wiz-reuse", classes="chip", compact=True)
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
        with Vertical(id="wiz-body-field"):
            yield Label(t("wizard.body"))
            yield TextArea(id="wiz-body", soft_wrap=True, show_line_numbers=False)
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
        with Vertical(id="change-plan", classes="wiz-section"):
            yield Static(t("wizard.plan_title"), classes="card-title")
            yield Static(Content.styled(t("wizard.plan_help"), "$text-muted"))
            for role in PLAN_ROLES:
                with Vertical(id=f"plan-{role}-area", classes="wiz-section"):
                    yield Label(t(f"wizard.plan_{role}"))
                    yield FlowRow(id=f"plan-{role}-chips", classes="chips")
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
        yield Static("", id="team-protected")
        yield Label(t("wizard.team_engine"), id="wiz-engine-label")
        yield Select([], allow_blank=True, disabled=True, id="wiz-engine")
        yield Static("", id="wiz-guarantees")
        yield Static("", id="wiz-guarantee-warning")
        yield Label(t("wizard.provider_title"), id="wiz-provider-label")
        with FlowRow(id="provider-row", classes="chips"):
            for provider in Provider:
                yield Button(
                    t(f"wizard.provider_{provider.value}"),
                    id=f"provider-{provider.value}",
                    classes="chip provider-chip",
                    compact=True,
                )
        yield Static("", id="wiz-provider-note")
        with FlowRow(id="implementation-row", classes="chips"):
            for name in PROFILES:
                yield Button(
                    t(f"wizard.profile_{name}"),
                    id=f"implementation-{name}",
                    classes="chip implementation-chip",
                    compact=True,
                )
        with Vertical(id="implementation-options", classes="wiz-section"):
            yield Label(
                t("wizard.implementation_model_balanced"), id="wiz-implementation-model-label"
            )
            yield Select(
                self._model_choices(False),
                allow_blank=False,
                value=PLAN_MODEL,
                id="wiz-implementation-model",
            )
            yield Label(t("wizard.implementation_variant"))
            yield Select(
                self._variant_choices(False, False),
                allow_blank=False,
                value=DEPTH_EFFORT,
                id="wiz-implementation-variant",
            )
            yield Static(t("wizard.implementation_pure"), id="wiz-implementation-pure-note")
            with Horizontal(id="wiz-pure-row"):
                yield Checkbox(
                    t("wizard.implementation_pure_balanced"), False, id="wiz-pure", compact=True
                )
        yield Static("", id="wiz-verify-note")
        with Horizontal(id="sandbox-row"):
            yield Checkbox(t("wizard.sandbox"), False, id="wiz-sandbox", compact=True)
        yield Static(Content.styled(t("wizard.sandbox_note"), "$text-muted"), id="wiz-sandbox-note")
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
        yield Static("", id="wiz-limits-note")
        with Horizontal(id="run-limits-row"):
            yield Checkbox(t("wizard.limits"), False, id="wiz-limits", compact=True)
        with FlowRow(id="run-limits-fields", cell=LIMIT_CELL):
            with Vertical(classes="limit-field"):
                yield Label(t("wizard.limit_budget"))
                yield Input(id="wiz-limit-budget")
            with Vertical(classes="limit-field", id="limit-turns-field"):
                yield Label(t("wizard.limit_turns"))
                yield Input(id="wiz-limit-turns")
            with Vertical(classes="limit-field"):
                yield Label(t("wizard.limit_wall"))
                yield Input(id="wiz-limit-wall")
        with FlowRow(id="team-actions", classes="chips"):
            yield Button(t("wizard.preview"), id="wiz-team-preview", classes="chip", compact=True)

    def _model_choices(self, fast: bool) -> list[tuple[str, str]]:
        t = self._t
        first = (
            (t("wizard.implementation_by_kind"), BY_KIND)
            if fast
            else (t("wizard.keep_plan"), PLAN_MODEL)
        )
        return [first, *((t(label), model) for model, label in MODEL_LABELS.items())]

    def _variant_choices(self, fast: bool, fast_output: bool) -> list[tuple[str, str]]:
        t = self._t
        first = (
            (t("wizard.implementation_by_kind"), BY_KIND)
            if fast
            else (t("wizard.variant_depth"), DEPTH_EFFORT)
        )
        names = (name for name in VARIANTS if fast_output or name not in FAST_OUTPUT)
        return [first, *((t(f"wizard.variant_{name}"), name) for name in names)]

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._start()

    def _start(self) -> None:
        self.apply_kind()
        self.query_one("#improve-actions").display = False
        self.query_one("#wiz-sent-body").display = False
        self.query_one("#forge-gate").display = False
        self.query_one("#preview-card").display = False
        self.query_one("#wiz-verify-note").display = False
        self.query_one("#wiz-file-note").display = False
        self.query_one("#run-limits-fields").display = False
        self.query_one("#team-cards").display = False
        self.query_one("#team-simple-note").display = False
        self.query_one("#wiz-sandbox-note").display = False
        self.query_one("#change-plan").display = False
        self.query_one("#team-protected").display = False
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
        engines: tuple[tuple[str, bool], ...],
        forge_ready: bool = True,
        init_estimate: float | None = None,
        limits: LimitSettings | None = None,
        implementation_profile: str = AUTO_PROFILE,
        implementation_variant: str = "",
        role_pins: bool = False,
    ) -> None:
        self.role_pins = role_pins
        ready = {name for name, installed in engines if installed}
        self.engines = tuple(name for name in ENGINE_ORDER if name in ready)
        self.engine = engine if engine in self.engines else next(iter(self.engines), "")
        selector = self.query_one("#wiz-engine", Select)
        selector.set_options([(self._t(f"wizard.engine_{name}"), name) for name in self.engines])
        selector.disabled = not self.engines
        if self.engine:
            selector.value = self.engine
        self.limit_settings = limits or LimitSettings()
        self._apply_limit_settings()
        self.implementation_profile = implementation_profile
        self._preferred_variant = (
            implementation_variant if implementation_variant in VARIANTS else ""
        )
        self._configured_variant = self._preferred_variant
        self._offer_choices(force=True)
        if implementation_profile == "fast":
            self.choose_implementation(implementation_profile)
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
        self._order_tell()
        self._paint()
        if self.one_page and self.understanding is not None:
            self.refresh_change_plan()
            self.refresh_team()

    def _order_tell(self) -> None:
        tell = self.query_one("#step-tell")
        chips = self.query_one("#story-chips")
        row = self.query_one("#wiz-file-row")
        first = tell.children.index(chips) < tell.children.index(row)
        if self.one_page and not first:
            tell.move_child(chips, before=row)
        elif not self.one_page and first:
            tell.move_child(chips, after=self.query_one("#wiz-file-note"))

    @property
    def one_page(self) -> bool:
        return self.layout_name == ONE_PAGE

    @property
    def gated(self) -> bool:
        return not self.forge_ready and not self.simple

    @property
    def story(self) -> str:
        return self.query_one("#wiz-story", TextArea).text.strip()

    @property
    def file_path(self) -> str:
        return typed_path(self.query_one("#wiz-file", Input).value)

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
        body = trimmed(self.query_one("#wiz-body", TextArea).text) if self.document_mode else ""
        evidence = (second if target == "why" else "", body)
        return MandateRequest(
            type=kind,
            what=self.query_one("#wiz-what", TextArea).text.strip(),
            why="\n\n".join(part for part in evidence if part),
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
        self.query_one("#wiz-body-field").display = self.document_mode
        self.show_missing()

    def _apply_limit_settings(self) -> None:
        settings = self.limit_settings
        fixed = settings.fixed
        self.limits_on = settings.mode is LimitsMode.DEPTH or fixed.active
        unset = NONE_SET if settings.mode is LimitsMode.OFF and fixed.active else ""
        values = (
            (fixed.budget_usd, False),
            (float(fixed.max_turns), True),
            (fixed.wall_min, False),
        )
        for selector, (value, whole) in zip(LIMIT_INPUTS, values, strict=True):
            text = limit_text(value, whole) if value > 0 else unset
            self.query_one(f"#{selector}", Input).value = text
        self.query_one("#wiz-limits", Checkbox).value = self.limits_on

    def depth_limits(self) -> RunLimits:
        return depth_limits(profile(parse_depth(self.depth), self.kind))

    @property
    def takes_turns(self) -> bool:
        return self.engine == Provider.CLAUDE

    def _limit(self, selector: str, default: float, whole: bool = False) -> float | None:
        return parse_limit(self.query_one(f"#{selector}", Input).value, default, whole)

    def limits(self) -> RunLimits | None:
        if not self.limits_on:
            return NO_LIMITS
        derived = self.depth_limits()
        budget = self._limit("wiz-limit-budget", derived.budget_usd)
        turns = (
            self._limit("wiz-limit-turns", derived.max_turns, whole=True)
            if self.takes_turns
            else 0.0
        )
        wall = self._limit("wiz-limit-wall", derived.wall_min)
        if budget is None or turns is None or wall is None:
            return None
        return RunLimits(budget, int(turns), wall)

    def _bad_limit(self) -> str:
        derived = self.depth_limits()
        checks = (
            ("wiz-limit-budget", derived.budget_usd, False, True),
            ("wiz-limit-turns", float(derived.max_turns), True, self.takes_turns),
            ("wiz-limit-wall", derived.wall_min, False, True),
        )
        for selector, default, whole, shown in checks:
            if shown and self._limit(selector, default, whole) is None:
                return selector
        return ""

    @property
    def team_shown(self) -> bool:
        return self.one_page or STEPS[self.step] == "team"

    def decided(self, options: MandateOptions) -> MandateOptions:
        choice = self.estimate.shape if self.estimate is not None else None
        if (
            choice is None
            or options.shape
            or options.route.role_models
            or self._shaped_revision != self._team_revision
            or not (choice.scout or eligible(self.kind, options.simple))
        ):
            return options
        mode = choice.mode.value if choice.scout else options.scout_mode
        return replace(options, shape=choice.shape.value, scout_mode=mode)

    def _card_models(self) -> dict[str, str]:
        return {
            select.id.removeprefix("override-"): value
            for select in self.query_one("#team-cards").query(Select)
            if select.id and isinstance(value := select.value, str) and value
        }

    def _role_models(self, engine: str) -> tuple[tuple[str, str], ...]:
        if self.simple or self.fast_profile or self.pure_active:
            return ()
        if self.plan is not None:
            picked = self._card_models()
        elif engine == self._kept_engine:
            picked = dict(self._kept_models)
        else:
            return ()
        if self.main_model_chosen:
            picked.pop(Role.ORCHESTRATOR.value, None)
        return tuple(picked.items())

    def options(self, pinned: bool = True) -> MandateOptions:
        chosen = self.query_one("#wiz-engine", Select).value
        engine = chosen if isinstance(chosen, str) and chosen in self.engines else self.engine
        pins = self._role_models(engine) if pinned else ()
        chosen_limits = self.limits() or NO_LIMITS
        understood = self.understanding
        offered = self.implementation_offered
        return MandateOptions(
            engine=engine,
            profile=self.implementation_profile,
            variant=self.run_variant if offered else "",
            model=self.implementation_model if offered else "",
            pure=self.pure_active,
            required=ESSENTIAL_FIELDS if self.document_mode else None,
            budget_usd=chosen_limits.budget_usd if self.limits_on else None,
            max_turns=chosen_limits.max_turns if self.limits_on else None,
            max_wall_min=chosen_limits.wall_min if self.limits_on else None,
            limits="" if self.limits_on else LimitsMode.OFF.value,
            route=RouteOptions(
                role_models=pins,
                scope=understood.scope if understood is not None else None,
                risk=understood.risk if understood is not None else None,
            ),
            simple=self.simple,
            depth=self.depth,
            intake_scope=understood.intake_scope if understood is not None else "",
            sandbox=self.sandbox,
            plan_overrides=tuple(
                (path, "read" if self.kind == INVESTIGATION and role == "edit" else role)
                for path, role in self._plan_overrides.items()
            ),
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
        self._paint_implementation()
        for provider in Provider:
            chip = self.query_one(f"#provider-{provider.value}", Button)
            chip.set_class(provider.value == self.engine, "-current")
            chip.disabled = provider.value not in self.engines
        note = t(self.run_note)
        if self.advice:
            note = f"{note}\n{self.advice}"
        self.query_one("#wiz-provider-note", Static).update(Content.styled(note, "$text-muted"))
        self.query_one("#forge-gate").display = self.gated
        mode = Content.styled(t("wizard.simple_mode"), "$warning") if self.simple else ""
        self.query_one("#wiz-mode", Static).update(mode)
        self.query_one("#wiz-mode").display = self.simple
        self.query_one("#wiz-reuse").display = bool(self.last_story)
        self._paint_depth()
        self._paint_summary()

    def _paint_implementation(self) -> None:
        t = self._t
        fast = self.fast_profile
        effective = ImplementationProfile.FAST if fast else ImplementationProfile.BALANCED
        for name in PROFILES:
            button = self.query_one(f"#implementation-{name}", Button)
            button.set_class(name == effective, "-current")
            button.set_class(name == AUTO_PROFILE and self.automatic, "-on")
            button.disabled = self.fast_blocked
        self.query_one("#implementation-options").display = self.implementation_offered
        self.query_one("#wiz-implementation-model-label", Label).update(
            t("wizard.implementation_model" if fast else "wizard.implementation_model_balanced")
        )
        self.query_one("#wiz-implementation-pure-note").display = fast
        self.query_one("#wiz-pure-row").display = not fast
        self._offer_choices()

    def _offer_choices(self, force: bool = False) -> None:
        fast = self.fast_profile
        if force or fast != self._offered_fast:
            self._offered_fast = fast
            self._offer(MODEL_SELECT, self._model_choices(fast))
        variants = (fast, self.fast_output)
        if force or variants != self._offered_variants:
            self._offered_variants = variants
            choices = self._variant_choices(*variants)
            self._offer(VARIANT_SELECT, choices, self._preferred_variant)

    def _offer(self, selector: str, choices: list[tuple[str, str]], preferred: str = "") -> None:
        select = self.query_one(selector, Select)
        offered = {option for _, option in choices}
        current = select.value
        kept = current if isinstance(current, str) and current not in NO_CHOICE else ""
        chosen = next((value for value in (kept, preferred) if value in offered), "")
        with self.prevent(Select.Changed):
            select.set_options(choices)
            if chosen:
                select.value = chosen

    def _paint_depth(self) -> None:
        t = self._t
        chosen = profile(parse_depth(self.depth), self.kind)
        description = t(
            f"wizard.depth_{chosen.depth.value}_help",
            reads=chosen.read_budget,
            tier=t(f"models.tier_{chosen.tier_cap.value}"),
        )
        if self.fast_profile and self.implementation_model:
            description = t(
                "wizard.depth_fast_help",
                reads=chosen.read_budget,
                model=t(MODEL_LABELS.get(self.implementation_model, "wizard.engine_default")),
                variant=t(
                    f"wizard.variant_{self.implementation_variant}"
                    if self.implementation_variant
                    else "wizard.variant_depth"
                ),
            )
        elif self.fast_profile:
            description = t(
                "wizard.depth_fast_kind_help", reads=chosen.read_budget, choice=self._kind_choice()
            )
        elif self.implementation_offered and (
            self.implementation_model or self.implementation_variant or self.pure_active
        ):
            description = t(
                "wizard.depth_balanced_help",
                reads=chosen.read_budget,
                tier=t(f"models.tier_{chosen.tier_cap.value}"),
                model=self._balanced_model(),
                variant=t(
                    f"wizard.variant_{self.implementation_variant}"
                    if self.implementation_variant
                    else "wizard.variant_depth"
                ),
            )
        self.query_one("#wiz-depth-note", Static).update(Content.styled(description, "$text-muted"))
        self._paint_limits()
        self._paint_guarantees()

    def _balanced_model(self) -> str:
        t = self._t
        chosen = self.implementation_model
        if self.pure_active:
            named = t(MODEL_LABELS[chosen]) if chosen in MODEL_LABELS else t("wizard.main_model")
            return t("wizard.pure_models", model=named)
        return t(MODEL_LABELS[chosen]) if chosen in MODEL_LABELS else t("wizard.plan_models")

    def _paint_limits(self) -> None:
        t = self._t
        derived = depth_limits(profile(parse_depth(self.depth), self.kind))
        self.query_one("#wiz-limit-budget", Input).placeholder = t(
            "wizard.limit_budget_placeholder", cap=money(derived.budget_usd)
        )
        self.query_one("#wiz-limit-turns", Input).placeholder = t(
            "wizard.limit_turns_placeholder", turns=derived.max_turns
        )
        self.query_one("#wiz-limit-wall", Input).placeholder = t("wizard.limit_wall_placeholder")
        chosen = self.limits()
        note = t("wizard.bad_limit") if chosen is None else t.message(limits_message(chosen))
        self.query_one("#wiz-limits-note", Static).update(Content.styled(note, "$text-muted"))
        fields = self.query_one("#run-limits-fields", FlowRow)
        fields.display = self.limits_on
        self.query_one("#limit-turns-field").display = self.takes_turns
        self.call_after_refresh(fields.reflow)

    def _paint_guarantees(self) -> None:
        t = self._t
        chosen = self.limits() or NO_LIMITS
        guarantees = engine_guarantees(self.engine, limits=chosen) if self.engine else ()
        self.query_one("#wiz-guarantees", Static).update(
            Content("\n").join(Content(t.message(row.message)) for row in guarantees)
        )
        reason = readonly_unavailable(self.engine) if self.kind == INVESTIGATION else None
        warning = reason or cap_warning(self.engine, chosen.budget_usd)
        self.query_one("#wiz-guarantee-warning", Static).update(
            Content.styled(t.message(warning), "$error" if reason else "$warning")
        )
        self.query_one("#wiz-launch", Button).disabled = reason is not None
        self.query_one("#wiz-next", Button).disabled = (
            reason is not None and self.step == len(STEPS) - 1
        )

    def _paint_summary(self) -> None:
        if not self.one_page:
            return
        t = self._t
        kind = t(f"wizard.intent_{self.kind}") if self.kind else t("wizard.untyped")
        team = (
            t("wizard.profile_fast")
            if self.fast_profile
            else ", ".join(t(f"models.role_{route.role.value}") for route in self.plan.routes)
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
        if not self.story and not self.file_path:
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
        missing = missing_fields(request, ESSENTIAL_FIELDS if self.document_mode else None)
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
        bad = self._bad_limit() if self.limits_on else ""
        if bad:
            if not self.team_shown:
                self.go(STEPS.index("team"))
            self.error("wizard.bad_limit")
            self.query_one(f"#{bad}", Input).focus()
            return False
        self.error("")
        return True

    def queue_change_plan(self) -> None:
        if not self.kind or not (self.one_page or self.step > 0):
            return
        self._change_revision += 1
        if self._change_timer is not None:
            self._change_timer.stop()
        self._change_timer = self.set_timer(PLAN_DELAY_S, self.refresh_change_plan)

    def refresh_change_plan(self) -> None:
        if self._change_timer is not None:
            self._change_timer.stop()
            self._change_timer = None
        self._change_revision += 1
        if not self.kind:
            self.change_plan = None
            self.query_one("#change-plan").display = False
            self.query_one("#team-protected").display = False
            return
        if self.kind == INVESTIGATION:
            self.query_one("#plan-edit-area").display = False
        self._hand_request_files()
        self.load_change_plan(self.request(), self._change_revision)

    @work(thread=True, exclusive=True, group="change-plan", exit_on_error=False)
    def load_change_plan(self, request: MandateRequest, revision: int) -> None:
        try:
            plan = self._services.change_plan(request)
        except Exception as error:
            self._call(self.app.notify, str(error), severity="error")
            return
        self._call(self.show_change_plan, plan, request, revision)

    async def show_change_plan(
        self, plan: ChangePlan, request: MandateRequest, revision: int
    ) -> None:
        async with self._change_lock:
            if revision != self._change_revision or request != self.request():
                return
            if plan.read_only or request.type == INVESTIGATION:
                plan = replace(plan, read_only=True)
                for target in plan.edit:
                    plan = move_plan(plan, target.path, "read")
                self._plan_overrides = {
                    path: "read" if role == "edit" else role
                    for path, role in self._plan_overrides.items()
                }
            plan = apply_overrides(plan, tuple(self._plan_overrides.items()))
            for target in plan.edit:
                if any(path_matches(target.path, pattern) for pattern in plan.guard):
                    plan = move_plan(plan, target.path, "read")
                    if self._plan_overrides.get(target.path) == "edit":
                        self._plan_overrides[target.path] = "read"
            for chip in self.query(PlanChip):
                chip.disabled = True
            groups = {
                "edit": tuple(target.path for target in plan.edit),
                "read": plan.read,
                "guard": plan.guard,
            }
            for role, paths in groups.items():
                row = self.query_one(f"#plan-{role}-chips", FlowRow)
                await row.remove_children()
                if revision != self._change_revision or request != self.request():
                    return
                if paths:
                    await row.mount_all(
                        PlanChip(path, role, index, self.plan_tooltip(path, plan))
                        for index, path in enumerate(paths)
                    )
                self.query_one(f"#plan-{role}-area").display = bool(paths)
            if revision != self._change_revision or request != self.request():
                return
            self.change_plan = plan
            for chip in self.query(PlanChip):
                chip.disabled = False
            shown = any(groups.values())
            self.query_one("#change-plan").display = shown
            protected = self.query_one("#team-protected", Static)
            protected.update(self._t("wizard.plan_protected", count=len(plan.guard)))
            protected.display = shown

    def plan_tooltip(self, path: str, plan: ChangePlan) -> str:
        pattern = next(
            (pattern for pattern in plan.guard if pattern != path and path_matches(path, pattern)),
            "",
        )
        if pattern:
            return self._t("wizard.plan_locked", path=pattern)
        return self._t("wizard.plan_move")

    def cycle_plan_role(self, path: str, role: str) -> None:
        plan = self.change_plan
        if plan is None:
            return
        contained = any(pattern != path and path_matches(path, pattern) for pattern in plan.guard)
        roles = (
            PLAN_ROLES[1:]
            if plan.read_only or self.kind == INVESTIGATION or contained
            else PLAN_ROLES
        )
        if role not in roles:
            return
        chosen = roles[(roles.index(role) + 1) % len(roles)]
        self._plan_overrides[path] = chosen
        self._change_revision += 1
        self.run_worker(
            self.show_change_plan(plan, self.request(), self._change_revision),
            group="change-plan-paint",
            exclusive=True,
            exit_on_error=False,
        )

    def go(self, step: int) -> None:
        self.step = max(0, min(step, len(STEPS) - 1))
        self._paint()
        if self.step > 0:
            self.refresh_change_plan()
        if STEPS[self.step] == "team":
            self.refresh_team()

    def queue_limits(self) -> None:
        if not self.kind or not self.team_shown:
            return
        if self._limits_timer is not None:
            self._limits_timer.stop()
        self._limits_timer = self.set_timer(PLAN_DELAY_S, self.refresh_limits)

    def refresh_limits(self) -> None:
        self._limits_timer = None
        if self.kind and self.team_shown and self.limits() is not None:
            self.refresh_team()

    def refresh_team(self) -> None:
        if self._limits_timer is not None:
            self._limits_timer.stop()
            self._limits_timer = None
        if self.plan is not None and self.query("#team-cards .team-card"):
            self._kept_models = self._card_models()
            self._kept_engine = self._cards_engine
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
            self._hand_request_files()
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
        reason = readonly_unavailable(self.engine) if self.kind == INVESTIGATION else None
        if reason is not None:
            self.query_one("#wiz-error", Static).update(
                Content.styled(self._t.message(reason), "$error")
            )
            return
        story = self.story or self.request().what
        options = self.decided(self.options())
        pinned = bool(options.route.role_models)
        shown = self.estimate.bounds if self.estimate is not None and not pinned else None
        self._hand_request_files()
        self.post_message(self.Launch(self.request(), replace(options, estimate=shown)))
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
        self._change_revision += 1
        if self._change_timer is not None:
            self._change_timer.stop()
            self._change_timer = None
        if self._limits_timer is not None:
            self._limits_timer.stop()
            self._limits_timer = None
        self.change_plan = None
        self._plan_overrides.clear()
        self._kept_models = {}
        self._kept_engine = ""
        self.query_one("#change-plan").display = False
        self.query_one("#team-protected").display = False
        self.plan = None
        self.estimate = None
        self.proposal = None
        self.kind = ""
        self.suggested_kind = ""
        self.depth = DEFAULT_DEPTH.value
        self.document_mode = False
        self._loaded = ""
        self._loaded_path = ""
        self._attached = {}
        self.query_one("#wiz-story", TextArea).text = ""
        for selector in ("#wiz-what", "#wiz-why", "#wiz-body"):
            self.query_one(selector, TextArea).text = ""
        for selector in ("#wiz-where", "#wiz-out", "#wiz-constraints", "#wiz-tests", "#wiz-file"):
            self.query_one(selector, Input).value = ""
        self.query_one("#wiz-file-note").display = False
        self._apply_limit_settings()
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
        self._leave_fast()
        self.apply_kind()
        self.error("")
        self._paint()
        self.refresh_change_plan()
        if self.one_page:
            self.refresh_team()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "wiz-file":
            self.load_file()
            return
        if event.input.id in LIMIT_INPUTS:
            self._paint_depth()
            self.queue_limits()
            return
        if not self.one_page:
            self.advance()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in LIMIT_INPUTS:
            self._paint_depth()
            self.queue_limits()
        elif event.input.id in {"wiz-where", "wiz-out", "wiz-constraints", "wiz-tests"}:
            self.queue_change_plan()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in {"wiz-implementation-model", "wiz-implementation-variant"}:
            if not isinstance(event.value, str) or event.value != event.select.value:
                return
            if event.select.id == "wiz-implementation-variant":
                self._preferred_variant = ""
            self._offer_choices()
            self._paint_depth()
            self._refresh_shown_team()
            return
        if event.select.id == "wiz-deliverable":
            self.queue_change_plan()
            return
        if event.select.id != "wiz-engine" or not isinstance(event.value, str):
            return
        if event.value not in self.engines or event.value == self.engine:
            return
        self.engine = event.value
        self._leave_fast()
        self._paint_depth()
        self._paint()
        if self.kind and self.team_shown:
            self.refresh_team()
        else:
            self._team_revision += 1

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id in {"wiz-what", "wiz-why", "wiz-body"}:
            self.queue_change_plan()
            return
        if event.text_area.id != "wiz-story":
            return
        if self._loaded_path:
            self._hand_request_files()
        if self._autosave is not None:
            self._autosave.stop()
        self._autosave = self.set_timer(AUTOSAVE_S, self.autosave)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        if event.checkbox.id == "wiz-sandbox":
            self.sandbox = event.value
            self.query_one("#wiz-sandbox-note").display = event.value
            return
        if event.checkbox.id == "wiz-pure":
            if event.value != self.pure:
                self.pure = event.value
                self._paint_depth()
                self._refresh_shown_team()
            return
        if event.checkbox.id != "wiz-limits" or event.value == self.limits_on:
            return
        self.limits_on = event.value
        self._paint_depth()
        self._paint_summary()
        if self.kind and self.team_shown:
            self.refresh_team()

    def _refresh_shown_team(self) -> None:
        if self.kind and self.team_shown:
            self.refresh_team()
        else:
            self._team_revision += 1

    def _call(self, callback: Callable[..., object], *args: object, **kwargs: object) -> None:
        if not self.app.is_running:
            return
        with suppress(RuntimeError):
            self.app.call_from_thread(callback, *args, **kwargs)

    @work(thread=True, exclusive=True, group="understand", exit_on_error=False)
    def understand(self) -> None:
        told = self._told(self.story)
        if told is None:
            return
        story, whole = told
        self._call(self._busy, True)
        try:
            understanding = self._services.understand(story, whole)
        except Exception as error:
            self._call(self._busy, False)
            self._call(self.app.notify, str(error), severity="error")
            return
        self._call(self.apply_understanding, understanding)

    def _told(self, story: str) -> tuple[str, bool] | None:
        whole = bool(self._loaded) and story == self._loaded
        path = "" if story else self.file_path
        found = LonePath(path) if path else lone_path(story)
        if found is None:
            return story, whole
        try:
            loaded = self._read_file(found.path, found.story_if_missing)
        except OSError:
            return story, whole
        if loaded is None:
            return None
        self._call(self.adopt_file, found.path, loaded)
        return loaded, True

    def _busy(self, busy: bool) -> None:
        with suppress(NoMatches):
            key = "wizard.understanding" if busy else ""
            text = Content.styled(self._t(key), "$accent") if key else ""
            self.query_one("#wiz-detected", Static).update(text)

    async def apply_understanding(self, understanding: Understanding) -> None:
        with suppress(NoMatches):
            await self._apply(understanding)

    async def _apply(self, understanding: Understanding) -> None:
        self._plan_overrides.clear()
        self.understanding = understanding
        confident = not understanding.needs_confirm
        self.kind = understanding.kind.option if confident else ""
        self._leave_fast()
        self.suggested_kind = understanding.kind.option
        self.depth = understanding.depth.option if understanding.depth.option else self.depth
        request = understanding.request(understanding.kind.option)
        self.document_mode = understanding.document is not None
        self._fill(replace(request, why="") if self.document_mode else request)
        self.query_one("#wiz-body", TextArea).text = request.why if self.document_mode else ""
        self._show_detected()
        self.apply_kind()
        await self._show_places(understanding.places)
        if self.one_page:
            self._paint()
            self.refresh_change_plan()
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
        if understanding.cost_usd is None or understanding.cost_usd > 0:
            by = f"{by} · {money(understanding.cost_usd, t('spectrum.na'))}"
        parts.append((f"\n{by}", "$text-muted"))
        if understanding.document is not None:
            whole = t("wizard.whole_mandate", chars=grouped(len(understanding.story)))
            parts.append((f"\n{whole}", "$text-muted"))
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

    async def _show_places(self, places: tuple[str, ...]) -> None:
        row = self.query_one("#where-chips", FlowRow)
        await row.remove_children()
        await row.mount_all(
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
            options = self.options(pinned=False)
            plan, estimate = self._services.team_plan(request, options)
            cards = self._services.team_cards(plan, estimate, options, request.type)
            per_role = runs_per_role(
                options.engine, not options.simple and self.kind != INVESTIGATION
            )
            verify = self._services.change_plan(request).verify if per_role else ()
            advice = self._services.team_advice(request.type)
            view = self._services.models_view(False)
        except Exception as error:
            self._call(self.app.notify, str(error), severity="error")
            return
        models: dict[str, tuple[ModelEntry, ...]] = {}
        for entry in view.entries:
            models[entry.engine] = (*models.get(entry.engine, ()), entry)
        self._call(self.show_verify, verify)
        self._call(self.show_advice, advice, request.type)
        self._call(self.show_team, plan, estimate, cards, models, options.engine, revision)

    async def show_team(
        self,
        plan: RoutePlan,
        estimate: Estimate,
        details: tuple[RoleCard, ...],
        models: dict[str, tuple[ModelEntry, ...]],
        engine: str,
        revision: int,
    ) -> None:
        async with self._team_lock:
            if engine != self.engine or revision != self._team_revision or not self.kind:
                return
            with suppress(NoMatches):
                await self._show_team(plan, estimate, details, models, engine, revision)

    def show_advice(self, advice: ProviderAdvice | None, task_type: str) -> None:
        self.advice = (
            self._t.message(advice_message(advice, task_type)) if advice is not None else ""
        )
        with suppress(NoMatches):
            self._paint()

    def show_verify(self, commands: tuple[str, ...]) -> None:
        with suppress(NoMatches):
            note = self.query_one("#wiz-verify-note", Static)
            text = self._t("wizard.verify_commands", commands=", ".join(commands))
            note.update(Content.styled(text, "$text-muted") if commands and self.per_role else "")
            note.display = bool(commands and self.per_role)

    def _price_line(self, entry: ModelEntry) -> str:
        if entry.input_price is None or entry.output_price is None:
            return self._t("wizard.card_no_price")
        return self._t(
            "wizard.card_price", input=money(entry.input_price), output=money(entry.output_price)
        )

    def _model_choice(self, entry: ModelEntry) -> str:
        if entry.input_price is None or entry.output_price is None:
            return self._t("wizard.model_no_price", model=entry.id)
        return self._t(
            "wizard.model_price",
            model=entry.id,
            input=money(entry.input_price),
            output=money(entry.output_price),
        )

    def _guarantee_line(self, guarantees: tuple[Guarantee, ...]) -> str:
        t = self._t

        def names(status: GuaranteeStatus) -> str:
            found = [
                t.message(msg(f"guarantee.{row.name}"))
                for row in guarantees
                if row.status is status
            ]
            return ", ".join(found) or t("wizard.card_none")

        return t(
            "wizard.card_guarantees",
            enforced=names(GuaranteeStatus.ENFORCED),
            checked=names(GuaranteeStatus.CHECKED),
            unavailable=names(GuaranteeStatus.UNAVAILABLE),
        )

    async def _show_team(
        self,
        plan: RoutePlan,
        estimate: Estimate,
        details: tuple[RoleCard, ...],
        models: dict[str, tuple[ModelEntry, ...]],
        engine: str,
        revision: int,
    ) -> None:
        t = self._t
        costs = {item.role: item for item in estimate.roles}
        by_role = {card.role: card for card in details}
        choices = [
            (t("wizard.keep_plan"), AUTO_MODEL),
            *((self._model_choice(entry), entry.id) for entry in models.get(engine, ())),
        ]
        offered = {value for _, value in choices}
        kept = self._kept_models if engine == self._kept_engine else {}
        cards = self.query_one("#team-cards", Vertical)
        await cards.remove_children(".team-card")
        if engine != self.engine or revision != self._team_revision or not self.kind:
            return
        widgets: list[Horizontal] = []
        selects: list[Select[str]] = []
        self.scout_launch = estimate.shape is not None and estimate.shape.launch
        routes = [route for route in plan.routes if not self.per_role or route.role in TEAM_ROLES]
        if self.fast_profile:
            routes = []
        for route in routes:
            model = route.model.id if route.model else t("wizard.engine_default")
            tier = t(f"models.tier_{route.tier.value}") if route.tier else "–"
            cost = costs.get(route.role)
            card = by_role.get(route.role)
            warnings = list(card.warnings) if card is not None else []
            native = self._services.build_warning(route.engine) if not self.per_role else None
            if native is not None:
                warnings.append(native)
            spread = (
                t("wizard.role_cost", low=money(cost.low), high=money(cost.high))
                if cost is not None and cost.low is not None
                else ""
            )
            if self.per_role and cost is not None and cost.share > 0:
                spread = f"{spread}  {t('wizard.role_share', cap=money(cost.share))}".strip()
            lines: list[tuple[str, str]] = [
                (f"{t(f'models.role_{route.role.value}')}", "bold"),
                (f"  {spread}\n" if spread else "\n", "$text-muted"),
                (
                    t("wizard.card_route", engine=route.engine.capitalize(), model=model, tier=tier)
                    + "\n",
                    "$accent",
                ),
            ]
            if route.model is not None:
                lines.append((f"{self._price_line(route.model)}\n", "$text-muted"))
            lines.append((t.message(route.reason), "$text-muted"))
            if card is not None and self.per_role:
                lines.append((f"\n{self._guarantee_line(card.guarantees)}", "$text-muted"))
                lines.append((f"\n{t.message(card.context)}", "$text-muted"))
            lines.extend((f"\n{t.message(warning)}", "$warning") for warning in warnings)
            body = Content.assemble(*lines)
            picked = kept.get(route.role.value, AUTO_MODEL)
            select = Select(
                choices,
                id=f"override-{route.role.value}",
                allow_blank=False,
                value=picked if picked in offered else AUTO_MODEL,
                disabled=self.pure_active
                or (route.role is Role.ORCHESTRATOR and self.main_model_chosen),
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
        self._cards_engine = engine
        self.estimate = estimate
        self._shaped_revision = revision
        self.query_one("#team-simple-note").display = False
        cards.display = not self.fast_profile
        self.query_one("#wiz-estimate", Static).update(self._estimate_content(estimate))
        self._paint_summary()

    def _estimate_content(self, estimate: Estimate) -> Content:
        found = self._forecast_content(estimate)
        lines = [
            Content.styled(self._t.message(message), "$accent")
            for message in (
                estimate.shape.message if estimate.shape is not None else None,
                self._docs_message(estimate.docs),
            )
            if message is not None
        ]
        if not lines:
            return found
        return Content("\n").join((*lines, found))

    def _docs_message(self, docs: DocsChoice | None) -> Said | None:
        if docs is None or docs.reason is DocsReason.FORCED_ON:
            return None
        if docs.on and self.fast_profile:
            return None
        return docs.message

    def _forecast_content(self, estimate: Estimate) -> Content:
        t = self._t
        text = t.message(estimate.message)
        if estimate.over:
            text = f"{text}  {t('wizard.over_cap', cap=money(estimate.cap))}"
        style = "$warning" if estimate.over else "$text-muted"
        content = Content.styled(text, style)
        forecast = estimate.forecast
        if forecast is None and estimate.forecast_error is not None:
            failure = t.message(estimate.forecast_error)
            return Content.assemble(content, (f"\n{failure}", "$warning"))
        if forecast is None:
            return content
        tone = "$warning" if forecast.warning else "$text-muted"
        messages = [t.message(message) for message in forecast.messages]
        if forecast.envelope.p50_usd is not None:
            return Content.styled("\n".join(messages), tone)
        return Content.assemble(content, *((f"\n{line}", tone) for line in messages))

    def show_simple_team(self) -> None:
        cards = self.query_one("#team-cards", Vertical)
        for card in cards.query(".team-card"):
            card.display = False
        note = self.query_one("#team-simple-note", Static)
        note.update(Content.styled(self._t("wizard.simple_team"), "$warning"))
        note.display = True
        cards.display = True
        self.query_one("#wiz-estimate", Static).update("")

    @property
    def pipeline(self) -> bool:
        return not self.simple and self.kind != INVESTIGATION

    @property
    def automatic(self) -> bool:
        return self.implementation_profile in ("", AUTO_PROFILE)

    @property
    def implementation_offered(self) -> bool:
        return (
            self.engine == Provider.CLAUDE
            and not self.simple
            and self.kind != INVESTIGATION
            and (bool(self.kind) or self.fast_profile)
        )

    @property
    def pure_active(self) -> bool:
        return self.pure and self.implementation_offered and not self.fast_profile

    @property
    def main_model_chosen(self) -> bool:
        return (
            bool(self.implementation_model)
            and self.implementation_offered
            and not self.fast_profile
            and not self.per_role
        )

    @property
    def fast_blocked(self) -> bool:
        return self.engine != Provider.CLAUDE or self.kind == INVESTIGATION

    @property
    def fast_output(self) -> bool:
        model = self.implementation_model
        if model:
            return fast_output_model(model)
        if self.implementation_profile != ImplementationProfile.FAST:
            return False
        choices = (fast_choice(self.kind, False), fast_choice(self.kind, True))
        return all(choice is not None and fast_output_model(choice.model) for choice in choices)

    @property
    def implementation_model(self) -> str:
        return self._picked(MODEL_SELECT)

    @property
    def implementation_variant(self) -> str:
        return self._picked(VARIANT_SELECT)

    def _picked(self, selector: str) -> str:
        value = self.query_one(selector, Select).value
        return value if isinstance(value, str) and value not in NO_CHOICE else ""

    @property
    def run_variant(self) -> str:
        variant = self.implementation_variant
        if variant or self.fast_profile or not self._configured_variant:
            return variant
        return profile(parse_depth(self.depth), self.kind).effort

    def _leave_fast(self) -> None:
        if self.fast_blocked and self.implementation_profile == ImplementationProfile.FAST:
            self.implementation_profile = AUTO_PROFILE

    @property
    def fast_profile(self) -> bool:
        chosen = resolved_profile(
            MandateOptions(
                engine=self.engine, profile=self.implementation_profile, simple=self.simple
            ),
            AUTO_PROFILE,
            self.engine,
            self.kind,
            pinned=self.role_pins,
        )
        return chosen is ImplementationProfile.FAST

    def _kind_choice(self) -> str:
        t = self._t
        if self.kind == MandateType.FEATURE:
            small = self._choice_label(fast_choice(self.kind, False))
            large = self._choice_label(fast_choice(self.kind, True))
            if small == large:
                return t("wizard.fast_choice_features", choice=small)
            return t("wizard.fast_choice_feature", small=small, large=large)
        found = fast_choice(self.kind, False)
        if found is not None:
            return t("wizard.fast_choice_bug", fix=self._choice_label(found))
        return t("wizard.fast_choice_other")

    def _choice_label(self, choice: FastChoice | None) -> str:
        if choice is None:
            return self._t("wizard.engine_default")
        model = self._t(MODEL_LABELS.get(choice.model, "wizard.engine_default"))
        variant = self.implementation_variant or choice.variant
        return f"{model} · {self._t(f'wizard.variant_{variant}')}"

    @property
    def per_role(self) -> bool:
        if self.fast_profile:
            return False
        return runs_per_role(self.engine, self.pipeline) or (self.pipeline and self.scout_launch)

    @property
    def run_note(self) -> str:
        if self.fast_profile:
            return "wizard.provider_single"
        if self.per_role:
            return "wizard.provider_per_role"
        if self.pipeline and self.engine == Provider.CLAUDE:
            return "wizard.provider_native"
        return "wizard.provider_single"

    def choose_provider(self, provider: str) -> None:
        if provider not in self.engines or provider == self.engine:
            return
        self.engine = provider
        self._leave_fast()
        self.query_one("#wiz-engine", Select).value = provider
        self._paint_depth()
        self._paint()
        if self.kind and self.team_shown:
            self.refresh_team()
        else:
            self._team_revision += 1

    def choose_depth(self, depth: str) -> None:
        self.depth = parse_depth(depth).value
        self._paint()
        if self.kind and self.team_shown:
            self.refresh_team()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        event.stop()
        if isinstance(event.button, PlanChip):
            self.cycle_plan_role(event.button.path, event.button.role)
            return
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
            "wiz-load": self.load_file,
            "wiz-attach": lambda: self.post_message(self.EvidenceWanted("attach")),
            "wiz-failure": lambda: self.post_message(self.EvidenceWanted("failure")),
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
            "provider-": lambda rest, _: self.choose_provider(rest),
            "implementation-": lambda rest, _: self.choose_implementation(rest),
        }

    def choose_implementation(self, name: str) -> None:
        self.implementation_profile = name if name in PROFILES else AUTO_PROFILE
        if name == ImplementationProfile.FAST and self.engine != Provider.CLAUDE:
            self.choose_provider(Provider.CLAUDE.value)
        self._leave_fast()
        self._paint()
        self.refresh_team()

    def understand_now(self) -> None:
        if self.check_story():
            self.understand()

    def preview_now(self) -> None:
        if self.check_request():
            self._hand_request_files()
            self.load_preview()

    def reuse_last(self) -> None:
        self.query_one("#wiz-story", TextArea).text = self.last_story

    def load_file(self) -> None:
        box = self.query_one("#wiz-file", Input)
        path = self.file_path
        if not path:
            self.file_note("wizard.file_needed", failed=True)
            box.focus()
            return
        self.read_mandate(path)

    @work(thread=True, exclusive=True, group="mandate-file", exit_on_error=False)
    def read_mandate(self, path: str) -> None:
        text = self._read_file(path)
        if text is not None:
            self._call(self.adopt_file, path, text)

    def _read_file(self, path: str, story_if_missing: bool = False) -> str | None:
        try:
            text = self._services.read_evidence(path)
        except OSError as error:
            if story_if_missing:
                raise
            if isinstance(error, FileNotFoundError):
                self._call(self.file_note, "wizard.file_missing", True, path=path)
                return None
            reason = error.strerror or str(error)
            self._call(self.file_note, "wizard.file_unreadable", True, path=path, error=reason)
            return None
        except NotText:
            reason = self._t("mandate.not_text")
            self._call(self.file_note, "wizard.file_unreadable", True, path=path, error=reason)
            return None
        if not text.strip():
            self._call(self.file_note, "wizard.file_empty", True, path=path)
            return None
        return text.removeprefix("\ufeff")

    def adopt_file(self, path: str, text: str) -> None:
        with suppress(NoMatches):
            self.query_one("#wiz-story", TextArea).text = text
            self._loaded = self.story
            self._loaded_path = path
            self.query_one("#wiz-file", Input).value = path
            self.file_note("wizard.file_loaded", path=path, chars=grouped(len(self._loaded)))
            self.error("")
            self._hand_request_files()

    @property
    def request_files(self) -> tuple[str, ...]:
        evidence = self.query_one("#wiz-why", TextArea).text
        loaded = self._loaded_path if self._loaded and self.story == self._loaded else ""
        attached = (path for path, text in self._attached.items() if text in evidence)
        return tuple(dict.fromkeys(path for path in (loaded, *attached) if path))

    def _hand_request_files(self) -> None:
        files = self.request_files
        if files != self._handed:
            self._handed = files
            self._services.use_request_files(files)

    def file_note(self, key: str, failed: bool = False, **values: object) -> None:
        with suppress(NoMatches):
            note = self.query_one("#wiz-file-note", Static)
            style = "$error" if failed else "$text-muted"
            note.update(Content.styled(self._t(key, **values), style))
            note.display = True

    def _append(self, selector: str, text: str) -> None:
        field = self.query_one(selector, Input)
        parts = [part.strip() for part in field.value.split(",") if part.strip()]
        if text not in parts:
            parts.append(text)
        field.value = ", ".join(parts)

    def set_evidence(self, text: str, replace_all: bool, path: str = "") -> None:
        area = self.query_one("#wiz-why", TextArea)
        if replace_all:
            area.text = text
        else:
            area.text = "\n\n".join(part for part in (area.text.strip(), text.strip()) if part)
        if path and text.strip():
            self._attached[path] = text.strip()
            self._hand_request_files()

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
        proposal = self.proposal.proposal
        if self.document_mode:
            self._fill(replace(proposal, why=""))
            self.query_one("#wiz-body", TextArea).text = proposal.why
        else:
            self._fill(proposal)
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
            preview = self._services.preview_mandate(
                self.request(), 0, self.decided(self.options())
            )
        except Exception as error:
            self._call(self.app.notify, str(error), severity="error")
            return
        self._call(self.show_preview, preview)

    def show_preview(self, preview: MandatePreview) -> None:
        t = self._t
        command = (
            Content.styled(t("wizard.preview_per_role"), "$text-muted")
            if preview.per_role
            else Content.assemble((f"{t('mandate.command')}  ", "$text-muted"), preview.command)
        )
        self.query_one("#preview-command", Static).update(command)
        team = self.query_one("#preview-team", Static)
        team.update(self._role_lines(preview))
        team.display = bool(preview.roles)
        self.query_one("#preview-prompt", TextArea).text = preview.prompt
        card = self.query_one("#preview-card")
        card.display = True
        self.call_after_refresh(card.scroll_visible)
        told = preview.template_note
        if told is not None and told.status is Status.WARN:
            self.app.notify(t.message(told.message, told.text), severity="warning")
        elif told is not None:
            self.app.notify(t.message(told.message, told.text))

    def _role_lines(self, preview: MandatePreview) -> Content:
        t = self._t
        lines = [
            t(
                "wizard.preview_role",
                role=t(f"models.role_{route.role.value}"),
                model=route.model.id if route.model is not None else t("wizard.engine_default"),
                tier=t(f"models.tier_{route.tier.value}") if route.tier is not None else "–",
            )
            for route in preview.roles
        ]
        return Content.assemble(
            (f"{t('wizard.preview_team')}\n", "bold"), ("\n".join(lines), "$accent")
        )

    def prefilled(self, request: MandateRequest) -> None:
        self._plan_overrides.clear()
        self.kind = request.type or self.kind or MandateType.FEATURE.value
        self._leave_fast()
        self.suggested_kind = self.kind
        self.understanding = None
        self.document_mode = False
        self.query_one("#wiz-body", TextArea).text = ""
        self.query_one("#wiz-story", TextArea).text = request.what
        self._fill(request)
        self.query_one("#wiz-constraints", Input).value = request.constraints
        if request.type != INVESTIGATION:
            self.query_one("#wiz-tests", Input).value = request.tests
        self.apply_kind()
        if self.one_page:
            self._paint()
            self.refresh_change_plan()
            self.refresh_team()
        else:
            self.go(1)
