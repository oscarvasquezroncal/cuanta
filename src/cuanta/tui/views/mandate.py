from __future__ import annotations

from contextlib import suppress

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button

from cuanta.application.mandate_flow import MandateOptions, MandateSetup
from cuanta.application.tests_view import Hairball
from cuanta.domain.errors import CuantaError
from cuanta.domain.mandate import MandateRequest, MandateType
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services
from cuanta.tui.widgets.wizard import GUIDED, LAYOUTS, MandateWizard

LAYOUT_KEY = "ui.mandate_layout"


def parse_budget(text: str) -> float | None:
    cleaned = text.strip().lstrip("$")
    if not cleaned:
        return 0.0
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value >= 0 else None


def prefill_request(hairball: Hairball, what: str) -> MandateRequest:
    evidence = "\n".join(
        part
        for part in (hairball.verbatim, f"first test: {hairball.first_test}", hairball.location)
        if part
    )
    return MandateRequest(
        type=MandateType.BUG.value,
        what=what,
        why=evidence,
        where=hairball.location,
    )


class MandateView(Vertical):
    class RunRequested(Message):
        def __init__(
            self, request: MandateRequest, signatures: int, options: MandateOptions
        ) -> None:
            super().__init__()
            self.request = request
            self.signatures = signatures
            self.options = options

    class AttachRequested(Message):
        pass

    class InitRequested(Message):
        pass

    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="mandates", classes="view")
        self._services = services
        self._t = catalog
        self.setup: MandateSetup | None = None
        self.signatures = 0

    def compose(self) -> ComposeResult:
        t = self._t
        with Horizontal(id="mandate-mode"):
            for name in LAYOUTS:
                yield Button(
                    t(f"wizard.layout_{name}"),
                    id=f"layout-{name}",
                    classes="chip layout-chip",
                    compact=True,
                )
        yield MandateWizard(self._services, t)

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._setup()

    def _setup(self) -> None:
        settings = getattr(self.app, "settings", None)
        layout = str(getattr(settings, "mandate_layout", GUIDED))
        self.set_layout(layout, save=False)
        self.load_setup()

    @property
    def wizard(self) -> MandateWizard:
        return self.query_one(MandateWizard)

    @property
    def layout_name(self) -> str:
        return self.wizard.layout_name

    def set_layout(self, name: str, save: bool = True) -> None:
        wizard = self.wizard
        wizard.set_layout(name)
        for layout in LAYOUTS:
            self.query_one(f"#layout-{layout}", Button).set_class(
                layout == wizard.layout_name, "-current"
            )
        if save:
            self.save_layout(wizard.layout_name)

    @work(thread=True, exclusive=True, group="mandate-layout", exit_on_error=False)
    def save_layout(self, name: str) -> None:
        with suppress(Exception):
            self._services.save_setting(LAYOUT_KEY, name)

    def on_mandate_wizard_launch(self, message: MandateWizard.Launch) -> None:
        message.stop()
        signatures = self.signatures
        self.signatures = 0
        self.post_message(self.RunRequested(message.request, signatures, message.options))

    def on_mandate_wizard_init_wanted(self, message: MandateWizard.InitWanted) -> None:
        message.stop()
        self.post_message(self.InitRequested())

    def on_mandate_wizard_evidence_wanted(self, message: MandateWizard.EvidenceWanted) -> None:
        message.stop()
        if message.source == "attach":
            self.post_message(self.AttachRequested())
        else:
            self.load_failure()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        if button.startswith("layout-"):
            event.stop()
            self.set_layout(button.removeprefix("layout-"))

    @work(thread=True, exclusive=True, group="mandate-setup", exit_on_error=False)
    def load_setup(self) -> None:
        try:
            setup = self._services.mandate_setup()
        except Exception as error:
            self._call(self.app.notify, str(error), severity="error")
            return
        self._call(self.apply_setup, setup)

    def _call(self, callback: object, *args: object, **kwargs: object) -> None:
        if not self.app.is_running or not callable(callback):
            return
        with suppress(RuntimeError):
            self.app.call_from_thread(callback, *args, **kwargs)

    def apply_setup(self, setup: MandateSetup) -> None:
        with suppress(NoMatches):
            self.setup = setup
            self.wizard.configure(
                setup.default_engine,
                setup.budget_usd,
                setup.engines,
                setup.forge_ready,
                setup.init_estimate,
            )
            if not any(ready for _, ready in setup.engines):
                self.app.notify(self._t("mandate.no_engine"), severity="warning")

    def continue_with(self, request: MandateRequest) -> None:
        self.wizard.prefilled(request)
        self.signatures = 0

    def prefill(self, hairball: Hairball) -> None:
        request = prefill_request(hairball, self._t("mandate.prefill_what", signature=hairball.id))
        self.wizard.prefilled(request)
        self.signatures = 1

    def attach(self, path: str) -> None:
        self.read_file(path)

    @work(thread=True, exit_on_error=False)
    def read_file(self, path: str) -> None:
        try:
            text = self._services.read_evidence(path)
        except OSError as error:
            message = self._t("mandate.attach_failed", path=path, error=error.strerror or error)
            self._call(self.app.notify, message, severity="error")
            return
        self._call(self._append_evidence, text, path)

    def _append_evidence(self, text: str, path: str) -> None:
        self.wizard.set_evidence(text, replace_all=False)
        self.app.notify(self._t("mandate.attached", path=path))

    @work(thread=True, exit_on_error=False)
    def load_failure(self) -> None:
        try:
            evidence, count = self._services.failure_evidence()
        except CuantaError as error:
            message = self._t("mandate.failure_failed", error=str(error), hint=error.hint)
            self._call(self.app.notify, message, severity="warning")
            return
        self._call(self._apply_failure, evidence, count)

    def _apply_failure(self, evidence: str, count: int) -> None:
        self.signatures = count
        wizard = self.wizard
        if not wizard.kind:
            wizard.kind = MandateType.BUG.value
            wizard.apply_kind()
        wizard.set_evidence(evidence, replace_all=True)
        self.app.notify(self._t("mandate.failure_loaded", count=count))
