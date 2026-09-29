from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.forecast import PlannedForecast
from cuanta.application.governor import Governor
from cuanta.domain.change_plan import ChangePlan
from cuanta.domain.engine import EngineEvent
from cuanta.domain.envelope import RoleForecast
from cuanta.domain.governor import (
    ROTATION_CUTOFF,
    Reaction,
    ReactionKind,
    ReactionTaken,
    RolePlan,
    role_plan,
)
from cuanta.domain.messages import Message, keyed, msg
from cuanta.domain.pricing import Price, PriceTable
from cuanta.domain.progress import Status, note
from cuanta.domain.routing import Provider, Role
from cuanta.ports.progress import ProgressSink

FINISH_TURN = "termina ahora: aplica lo que está completo, escribe el handoff y lista lo que falta"
ROLE_FINISH = (
    f"{FINISH_TURN}. End with the JSON handoff and set its status to partial if anything remains."
)
TEAM_FINISH = f"{FINISH_TURN}. Start no more subagents; end with your report and list what remains."
THREAD_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z._:-]{0,127}")
CHECKPOINT_TURN = (
    "checkpoint: stop here and call no more tools. A fresh session of you continues this task "
    "from your note. End with the JSON handoff: status partial, the facts you need as "
    "path:start-end anchors, and in next_step exactly what remains."
)


def resume_prompt(prompt: str, checkpoint: str) -> str:
    return (
        f"{prompt}\n\n=== CHECKPOINT FROM YOUR EARLIER SESSION ===\n{checkpoint}\n\n"
        "Continue from this checkpoint. The files it changed are already changed in the working "
        "copy: do not redo that work or re-read what its anchors cover. Do only what remains, "
        "then end with the JSON handoff."
    )


def codex_finish_prompt(changed: Sequence[str]) -> str:
    files = "\n".join(f"- {path}" for path in changed) if changed else "- none"
    return (
        f"{FINISH_TURN}.\n"
        "cuanta stopped your run because its share of the budget is nearly spent. Work that was "
        "still running at the stop was lost. These files are changed in the working copy now:\n"
        f"{files}\n"
        "Start no new work and run no commands. End with the JSON handoff: status partial if "
        "anything remains, the files above that hold finished work, and in next_step exactly what "
        "is missing."
    )


def resumable_thread(thread: str) -> bool:
    return THREAD_ID.fullmatch(thread) is not None


def restart_left(share: float, cost: float) -> float:
    left = share - cost
    return left if left > (1.0 - ROTATION_CUTOFF) * share else 0.0


@dataclass(frozen=True, slots=True)
class GovernorSetup:
    clock: Callable[[], float]
    prices: PriceTable | None = None
    usd_per_second: Callable[[str], float] | None = None

    def price(self, model: str) -> Price | None:
        return self.prices.lookup(model) if self.prices is not None and model else None

    def seconds_rate(self, model: str) -> float:
        return self.usd_per_second(model) if self.usd_per_second is not None and model else 0.0


def role_forecast(forecast: PlannedForecast | None, role: Role) -> RoleForecast | None:
    if forecast is None:
        return None
    return next((item for item in forecast.envelope.roles if item.role is role), None)


def forecast_price(forecast: PlannedForecast | None, role: Role) -> Price | None:
    if forecast is None:
        return None
    return next((item.model.price for item in forecast.inputs.roles if item.role is role), None)


def model_price(
    setup: GovernorSetup, model: str, forecast: PlannedForecast | None, role: Role
) -> Price | None:
    found = setup.price(model)
    return found if found is not None else forecast_price(forecast, role)


def claude_role_plan(
    setup: GovernorSetup,
    role: Role,
    model: str,
    share: float,
    native: float,
    forecast: PlannedForecast | None,
    change: ChangePlan | None,
) -> RolePlan:
    price = model_price(setup, model, forecast, role)
    found = role_forecast(forecast, role)
    if found is None or forecast is None:
        return RolePlan(role, Provider.CLAUDE, model, share, 0, price=price, hard_cap_usd=native)
    planned = role_plan(
        found,
        Provider.CLAUDE,
        share,
        change,
        price,
        hard_cap_usd=native,
        ttl_s=forecast.inputs.cache_ttl_s,
        rotatable=True,
    )
    return replace(planned, model=model)


def codex_role_plan(
    setup: GovernorSetup,
    role: Role,
    model: str,
    share: float,
    forecast: PlannedForecast | None,
    change: ChangePlan | None,
) -> RolePlan:
    price = model_price(setup, model, forecast, role)
    per_second = setup.seconds_rate(model)
    found = role_forecast(forecast, role)
    if found is None:
        return RolePlan(
            role, Provider.CODEX, model, share, 0, price=price, usd_per_second=per_second
        )
    planned = role_plan(found, Provider.CODEX, share, change, price, usd_per_second=per_second)
    return replace(planned, model=model)


def session_plan(
    setup: GovernorSetup,
    model: str,
    cap: float,
    forecast: PlannedForecast | None,
    change: ChangePlan | None,
) -> RolePlan:
    plan = change if change is not None else ChangePlan()
    edits = () if plan.read_only else tuple(target.path for target in plan.edit)
    return RolePlan(
        Role.ORCHESTRATOR,
        Provider.CLAUDE,
        model,
        cap,
        forecast.envelope.requests if forecast is not None else 0,
        edit_paths=edits,
        price=model_price(setup, model, forecast, Role.ORCHESTRATOR),
        hard_cap_usd=cap,
        ttl_s=forecast.inputs.cache_ttl_s if forecast is not None else 0,
    )


def finish_message(reaction: Reaction, team: bool) -> Message:
    values = {
        "trigger": keyed("governor.trigger", reaction.trigger.value),
        "spent": f"{reaction.projection.spent_usd or 0.0:.4f}",
        "limit": f"{reaction.projection.limit_usd:.4f}",
    }
    if team:
        return msg("governor.finish_team", **values)
    return msg("governor.finish", role=reaction.role.value, **values)


class Steering:
    def __init__(
        self,
        governor: Governor,
        role: Role,
        send: Callable[[str], bool],
        progress: ProgressSink,
        team: bool = False,
        run_id: str = "",
        halt: Callable[[Callable[[], float | None]], bool] | None = None,
    ) -> None:
        self._governor = governor
        self._role = role
        self._send = send
        self._progress = progress
        self._team = team
        self._halt = halt
        self._finished = False
        self.run_id = run_id
        self.checkpoint: Reaction | None = None
        self.stopped: Reaction | None = None
        self.taken: list[ReactionTaken] = []

    def started(self, run_id: str) -> None:
        self.run_id = run_id
        if self._halt is not None:
            self._governor.start(self._role)

    def estimate(self) -> float | None:
        return self._governor.progress(self._role).spent_usd

    def settle(self, outcome: str, kind: ReactionKind = ReactionKind.CODEX_STOP) -> None:
        self.taken = [
            replace(taken, outcome=outcome)
            if taken.reaction.kind is kind and taken.sent and not taken.outcome
            else taken
            for taken in self.taken
        ]

    def __call__(self, event: EngineEvent) -> None:
        for reaction in self._governor.observe(self._role, event):
            self._act(reaction)

    def restart(self) -> None:
        self._governor.restart(self._role)
        self.checkpoint = None

    def _act(self, reaction: Reaction) -> None:
        if self._finished or self.checkpoint is not None:
            return
        if reaction.kind is ReactionKind.CODEX_STOP:
            self._finished = True
            sent = self._halt is not None and self._halt(self.estimate)
            if sent:
                self.stopped = reaction
            values = {
                "role": reaction.role.value,
                "spent": f"{reaction.projection.spent_usd or 0.0:.4f}",
                "share": f"{reaction.projection.limit_usd:.4f}",
            }
            key = "governor.codex_stop" if sent else "governor.codex_stop_unsent"
            self._record(reaction, sent, msg(key, **values))
        elif reaction.kind is ReactionKind.FINISH_NOW:
            self._finished = True
            sent = self._send(TEAM_FINISH if self._team else ROLE_FINISH)
            shown = (
                finish_message(reaction, self._team)
                if sent
                else msg("governor.finish_unsent", role=reaction.role.value)
            )
            self._record(reaction, sent, shown)
        elif reaction.kind is ReactionKind.ROTATE:
            sent = self._send(CHECKPOINT_TURN)
            if sent:
                self.checkpoint = reaction
            key = "governor.checkpoint" if sent else "governor.checkpoint_unsent"
            self._record(
                reaction,
                sent,
                msg(key, role=reaction.role.value, saving=f"{reaction.saving_usd:.4f}"),
            )

    def _record(self, reaction: Reaction, sent: bool, shown: Message) -> None:
        self.taken.append(ReactionTaken(reaction, self.run_id, sent))
        self._progress.publish(note(Status.INFO if sent else Status.WARN, shown))


def observed(
    observer: Callable[[EngineEvent], None] | None, steering: Steering
) -> Callable[[EngineEvent], None]:
    def both(event: EngineEvent) -> None:
        if observer is not None:
            observer(event)
        steering(event)

    return both


def role_steering(
    setup: GovernorSetup | None,
    launcher: EngineLauncher,
    spec: LaunchSpec,
    role: Role,
    share: float,
    forecast: PlannedForecast | None,
    progress: ProgressSink,
) -> Steering | None:
    if setup is None or share <= 0 or not launcher.steerable():
        return None
    plan = claude_role_plan(
        setup, role, spec.model, share, spec.max_budget_usd, forecast, spec.change_plan
    )
    governor = Governor([plan], setup.clock, spec.cwd, setup.prices)
    return Steering(governor, role, launcher.send_turn, progress)


def codex_steering(
    setup: GovernorSetup | None,
    launcher: EngineLauncher,
    spec: LaunchSpec,
    role: Role,
    share: float,
    forecast: PlannedForecast | None,
    progress: ProgressSink,
) -> Steering | None:
    if setup is None or share <= 0:
        return None
    plan = codex_role_plan(setup, role, spec.model, share, forecast, spec.change_plan)
    if plan.usd_per_item <= 0 and plan.usd_per_second <= 0:
        return None
    governor = Governor([plan], setup.clock, spec.cwd, setup.prices)
    return Steering(governor, role, launcher.send_turn, progress, halt=launcher.halt)


def session_steering(
    setup: GovernorSetup | None,
    launcher: EngineLauncher,
    spec: LaunchSpec,
    forecast: PlannedForecast | None,
    progress: ProgressSink,
) -> Steering | None:
    if (
        setup is None
        or not spec.agents_file
        or spec.max_budget_usd <= 0
        or not launcher.steerable()
    ):
        return None
    plan = session_plan(setup, spec.model, spec.max_budget_usd, forecast, spec.change_plan)
    governor = Governor([plan], setup.clock, spec.cwd, setup.prices)
    return Steering(
        governor, Role.ORCHESTRATOR, launcher.send_turn, progress, team=True, run_id=spec.run_id
    )
