from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.bootstrap import Container
    from cuanta.cli.commands.mandate import MandateArgs
    from cuanta.cli.document import Document, Line, Table
    from cuanta.domain.cache import PrefixWindow
    from cuanta.domain.errors import CuantaError
    from cuanta.domain.queue import QueueEntry, QueueOutcome, QueueSlot

queue_app = typer.Typer(
    help="Queue mandates and run them back to back, each on the warm prefix of the one before.",
    no_args_is_help=True,
)

PASS_THROUGH = {"allow_extra_args": True, "ignore_unknown_options": True}
EMPTY_HINT = "queue empty · cuanta queue add <the options of cuanta mandate>"
DEFAULT_MODEL = "default"
STATUS_TEXT = {"ok": "ok", "failed": "failed", "not_run": "not run", "gone": "no longer queued"}
MANDATE_PARAMS = frozenset(
    {
        "type_",
        "what",
        "why",
        "evidence",
        "where",
        "constraints",
        "tests",
        "out_of_scope",
        "from_failure",
        "engine",
        "model",
        "profile",
        "variant",
        "pure",
        "budget",
        "hu",
        "dry_run",
        "route",
        "preset",
        "role_model",
        "override_env_model",
        "cross_engine",
        "cross_budget",
        "simple",
        "session",
        "depth",
        "max_turns",
        "max_wall",
        "shape",
        "sandbox",
        "keep",
        "classic",
        "docs",
    }
)


@queue_app.command(
    "add",
    help="Queue a mandate: pass the options of cuanta mandate (cuanta mandate --help).",
    context_settings=PASS_THROUGH,
    options_metavar="[MANDATE OPTIONS]",
)
def add_command(ctx: typer.Context) -> None:
    arguments = tuple(ctx.args)
    execute(ctx, lambda session: _add(session, arguments))


@queue_app.command("list", help="Queued mandates in run order, with the warm prefix window.")
def list_command(ctx: typer.Context) -> None:
    execute(ctx, _list)


@queue_app.command(
    "run", help="Run the queue back to back; shows the order and asks first, unless --yes."
)
def run_command(
    ctx: typer.Context,
    keep_going: Annotated[
        bool, typer.Option("--keep-going", help="Run the rest after a failed mandate.")
    ] = False,
) -> None:
    execute(ctx, lambda session: _run(session, keep_going))


@queue_app.command(
    "clear", help="Remove queued mandates, the ids given or all; asks first, unless --yes."
)
def clear_command(
    ctx: typer.Context,
    ids: Annotated[
        list[str] | None, typer.Argument(help="Queue ids such as q3; none clears the queue.")
    ] = None,
) -> None:
    chosen = tuple(ids or ())
    execute(ctx, lambda session: _clear(session, chosen))


def _usage_error() -> type[Exception]:
    for base in typer.BadParameter.__mro__:
        if issubclass(base, Exception) and base.__name__ == "ClickException":
            return base
    return typer.BadParameter


def parse_mandate(arguments: Sequence[str]) -> "MandateArgs":
    from typer.main import get_command

    from cuanta.cli.commands.mandate import mandate_command
    from cuanta.domain.errors import DomainFailure

    if "--help" in arguments:
        raise DomainFailure("--help cannot be queued", "cuanta mandate --help lists the options")
    holder = typer.Typer(
        add_completion=False, rich_markup_mode=None, pretty_exceptions_enable=False
    )
    holder.command("mandate")(mandate_command)
    command = get_command(holder)
    try:
        context = command.make_context("mandate", list(arguments))
    except _usage_error() as error:
        raise DomainFailure(
            f"not a mandate: {error}", "use the options of cuanta mandate, e.g. --type bug"
        ) from error
    return mandate_args(context.params)


def _value(params: Mapping[str, object], name: str) -> object:
    if name not in params:
        raise RuntimeError(f"mandate option {name} is missing from the parsed options")
    return params[name]


def _mismatch(name: str) -> TypeError:
    return TypeError(f"mandate option {name} has an unexpected type")


def _text(params: Mapping[str, object], name: str) -> str:
    value = _value(params, name)
    if isinstance(value, str):
        return value
    raise _mismatch(name)


def _flag(params: Mapping[str, object], name: str) -> bool:
    value = _value(params, name)
    if isinstance(value, bool):
        return value
    raise _mismatch(name)


def _number(params: Mapping[str, object], name: str) -> float:
    value = _value(params, name)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    raise _mismatch(name)


def _optional_number(params: Mapping[str, object], name: str) -> float | None:
    return None if _value(params, name) is None else _number(params, name)


def _integer(params: Mapping[str, object], name: str) -> int:
    value = _value(params, name)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise _mismatch(name)


def _optional_integer(params: Mapping[str, object], name: str) -> int | None:
    return None if _value(params, name) is None else _integer(params, name)


def _path(params: Mapping[str, object], name: str) -> Path | None:
    value = _value(params, name)
    if value is None or isinstance(value, Path):
        return value
    if isinstance(value, str):
        return Path(value)
    raise _mismatch(name)


def _texts(params: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = _value(params, name)
    if value is None:
        return ()
    if isinstance(value, list | tuple) and all(isinstance(item, str) for item in value):
        return tuple(str(item) for item in value)
    raise _mismatch(name)


def mandate_args(params: Mapping[str, object]) -> "MandateArgs":
    from cuanta.cli.commands.mandate import MandateArgs

    unmapped = sorted(set(params) - MANDATE_PARAMS)
    if unmapped:
        raise RuntimeError(f"cuanta queue does not map the mandate options {', '.join(unmapped)}")
    return MandateArgs(
        type=_text(params, "type_"),
        what=_text(params, "what"),
        why=_text(params, "why"),
        evidence=_path(params, "evidence"),
        where=_text(params, "where"),
        constraints=_text(params, "constraints"),
        tests=_text(params, "tests"),
        out_of_scope=_text(params, "out_of_scope"),
        from_failure=_flag(params, "from_failure"),
        engine=_text(params, "engine"),
        model=_text(params, "model"),
        profile=_text(params, "profile"),
        variant=_text(params, "variant"),
        pure=_flag(params, "pure"),
        budget=_optional_number(params, "budget"),
        hu=_text(params, "hu"),
        dry_run=_flag(params, "dry_run"),
        route=_text(params, "route"),
        preset=_text(params, "preset"),
        role_models=_texts(params, "role_model"),
        keep_env_model=not _flag(params, "override_env_model"),
        cross_engine=_flag(params, "cross_engine"),
        cross_budget=_optional_number(params, "cross_budget"),
        simple=_flag(params, "simple"),
        session=_text(params, "session"),
        depth=_text(params, "depth"),
        max_turns=_optional_integer(params, "max_turns"),
        max_wall=_optional_number(params, "max_wall"),
        shape=_text(params, "shape"),
        sandbox=_flag(params, "sandbox"),
        keep=_flag(params, "keep"),
        classic=_flag(params, "classic"),
        docs=_text(params, "docs"),
    )


def queued_kind(args: "MandateArgs") -> str:
    return args.type or ("bug" if args.from_failure else "")


def check_mandate(args: "MandateArgs", engine: str = "", route: str = "") -> None:
    from cuanta.cli.commands.mandate import mandate_options
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.mandate import MandateRequest, MandateType, missing_fields
    from cuanta.domain.messages import english
    from cuanta.domain.routing import RouteMode, parse_provider
    from cuanta.domain.scout import parse_forced_shape, scout_refusal

    options = mandate_options(args)
    if args.evidence is not None and not args.evidence.is_file():
        raise DomainFailure(f"evidence file not found: {args.evidence}", "queue a file that exists")
    kind = queued_kind(args)
    allowed = sorted(item.value for item in MandateType)
    if kind and kind not in allowed:
        raise DomainFailure(f"unknown type {kind}", f"use one of {', '.join(allowed)}")
    refusal = scout_refusal(
        kind,
        parse_forced_shape(options.shape),
        options.simple,
        parse_provider(options.engine or engine) is not None,
        (options.route.mode or route) != RouteMode.OFF.value,
    )
    if refusal is not None:
        raise DomainFailure(english(refusal[0]), refusal[1])
    later: set[str] = set()
    if args.evidence is not None or args.from_failure:
        later.add("why")
    if args.from_failure:
        later.add("what")
    request = MandateRequest(
        type=kind,
        what=args.what,
        why=args.why,
        where=args.where,
        constraints=args.constraints,
        tests=args.tests,
        out_of_scope=args.out_of_scope,
    )
    missing = [name for name in missing_fields(request) if name not in later]
    if missing:
        names = ", ".join(f"--{name.replace('_', '-')}" for name in missing)
        raise DomainFailure(
            f"missing required fields: {names}", "a queued mandate runs unattended: pass them"
        )


def resolve(
    entries: Sequence["QueueEntry"], engine: str
) -> "tuple[tuple[QueueSlot, ...], dict[str, MandateArgs | CuantaError]]":
    from cuanta.domain.errors import CuantaError
    from cuanta.domain.queue import QueueSlot

    slots: list[QueueSlot] = []
    parsed: dict[str, MandateArgs | CuantaError] = {}
    for entry in entries:
        try:
            args = parse_mandate(entry.args)
        except CuantaError as error:
            parsed[entry.id] = error
            slots.append(QueueSlot(entry, engine))
            continue
        parsed[entry.id] = args
        slots.append(
            QueueSlot(
                entry, args.engine or engine, args.model, queued_kind(args), args.what, args.sandbox
            )
        )
    return tuple(slots), parsed


def clock_time(window: "PrefixWindow") -> str:
    return window.until.astimezone().strftime("%H:%M") if window.until is not None else ""


def warm_line(window: "PrefixWindow") -> "Line":
    from cuanta.cli.document import Line
    from cuanta.domain.cache import PrefixState
    from cuanta.domain.messages import english
    from cuanta.domain.progress import Status
    from cuanta.domain.queue import warm_known, warm_message

    status = Status.INFO
    if warm_known(window):
        status = Status.OK if window.state is PrefixState.WARM else Status.WARN
    return Line(english(warm_message(window, clock_time(window))), status)


def _window(container: "Container", engine: str) -> "PrefixWindow":
    return container.prefix_query().run(engine)


def _cap(parsed: "MandateArgs | CuantaError") -> float | None:
    from cuanta.domain.errors import CuantaError

    if isinstance(parsed, CuantaError):
        return None
    if parsed.budget is not None and parsed.budget > 0:
        return parsed.budget
    return parsed.cross_budget


def _request_text(slot: "QueueSlot", parsed: "MandateArgs | CuantaError") -> str:
    from cuanta.domain.errors import CuantaError

    if isinstance(parsed, CuantaError):
        return f"invalid: {parsed.message}"
    return slot.title or "-"


def queue_table(
    title: str, slots: Sequence["QueueSlot"], parsed: "Mapping[str, MandateArgs | CuantaError]"
) -> "Table":
    from cuanta.cli.document import Column, Table
    from cuanta.cli.fmt import usd
    from cuanta.domain.errors import CuantaError

    rows: list[tuple[str, ...]] = []
    for index, slot in enumerate(slots, 1):
        found = parsed[slot.entry.id]
        cap = _cap(found)
        copy = not isinstance(found, CuantaError) and found.sandbox
        rows.append(
            (
                str(index),
                slot.entry.id,
                slot.kind or "-",
                slot.provider,
                slot.model or DEFAULT_MODEL,
                usd(cap) if cap is not None else "-",
                "sandbox" if copy else "-",
                _request_text(slot, found),
            )
        )
    return Table(
        title,
        (
            Column("#", numeric=True),
            Column("id"),
            Column("type"),
            Column("engine"),
            Column("model"),
            Column("cap", numeric=True),
            Column("copy"),
            Column("request"),
        ),
        tuple(rows),
    )


def slot_payload(slot: "QueueSlot", parsed: "MandateArgs | CuantaError") -> dict[str, object]:
    from cuanta.domain.errors import CuantaError

    payload: dict[str, object] = {
        "id": slot.entry.id,
        "added_at": slot.entry.added_at,
        "type": slot.kind or None,
        "engine": slot.provider,
        "model": slot.model or None,
        "request": slot.request or None,
        "args": list(slot.entry.args),
    }
    if isinstance(parsed, CuantaError):
        payload["invalid"] = parsed.message
    else:
        payload["cap_usd"] = _cap(parsed)
        payload["limits"] = {
            "budget_usd": parsed.budget,
            "max_turns": parsed.max_turns,
            "wall_min": parsed.max_wall,
        }
        payload["sandbox"] = parsed.sandbox
    return payload


def _add(session: Session, arguments: tuple[str, ...]) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint, Line
    from cuanta.domain.progress import Status
    from cuanta.domain.queue import queue_order

    args = parse_mandate(arguments)
    container = Container.for_project(session.project)
    try:
        check_mandate(args, container.config.engine, container.routing_policy().mode.value)
        queue = container.mandate_queue()
        entry = queue.add(arguments)
        slots, parsed = resolve(queue.entries(), container.config.engine)
    finally:
        container.close()
    ordered = queue_order(slots)
    slot = next(item for item in ordered if item.entry.id == entry.id)
    position = ordered.index(slot) + 1
    model = slot.model or DEFAULT_MODEL
    text = f"queued {entry.id} · {slot.kind} · {slot.provider} · {model} · {slot.title or '-'}"
    return Document(
        blocks=(
            Line(text, Status.OK),
            Hint(f"runs #{position} of {len(ordered)} · cuanta queue list · cuanta queue run"),
        ),
        payload={
            "added": slot_payload(slot, parsed[entry.id]),
            "position": position,
            "queued": len(ordered),
        },
    )


def _list(session: Session) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint
    from cuanta.domain.queue import queue_order, warm_payload

    container = Container.for_project(session.project)
    try:
        slots, parsed = resolve(container.mandate_queue().entries(), container.config.engine)
        ordered = queue_order(slots)
        window = _window(container, ordered[0].provider if ordered else container.config.engine)
    finally:
        container.close()
    hint = "cuanta queue run [--keep-going] · cuanta queue clear [id]" if ordered else EMPTY_HINT
    return Document(
        blocks=(queue_table("queue", ordered, parsed), warm_line(window), Hint(hint)),
        payload={
            "queue": [slot_payload(slot, parsed[slot.entry.id]) for slot in ordered],
            "warm": warm_payload(window),
        },
    )


def _failure(error: "CuantaError") -> str:
    return f"{error.message} ({error.hint})" if error.hint else error.message


def _launch(
    session: Session,
    slot: "QueueSlot",
    parsed: "MandateArgs | CuantaError",
    documents: dict[str, "Document"],
) -> "QueueOutcome":
    from cuanta.bootstrap import Container
    from cuanta.cli.commands.mandate import run_mandate
    from cuanta.cli.output import OutputMode
    from cuanta.domain.errors import CuantaError
    from cuanta.domain.progress import Note, Status
    from cuanta.domain.queue import QueueOutcome, QueueStatus

    model = slot.model or DEFAULT_MODEL
    label = f"queue {slot.entry.id} · {slot.kind or '-'} · {slot.provider} · {model}"
    session.presenter.publish(Note(Status.INFO, f"{label} · {slot.title or '-'}"))
    if isinstance(parsed, CuantaError):
        outcome = QueueOutcome(slot, QueueStatus.FAILED, _failure(parsed))
    else:
        try:
            document = run_mandate(session, parsed)
        except CuantaError as error:
            outcome = QueueOutcome(slot, QueueStatus.FAILED, _failure(error))
        else:
            documents[slot.entry.id] = document
            if session.settings.mode is not OutputMode.JSON:
                session.presenter.render(document)
            ok = document.exit_code == 0
            detail = "" if ok else "the mandate did not finish green"
            outcome = QueueOutcome(slot, QueueStatus.OK if ok else QueueStatus.FAILED, detail)
    if outcome.status is not QueueStatus.OK:
        session.presenter.publish(Note(Status.FAIL, f"{label} failed: {outcome.detail}"))
    container = Container.for_project(session.project, verbose=session.options.verbose)
    try:
        line = warm_line(_window(container, slot.provider))
    finally:
        container.close()
    session.presenter.publish(Note(line.status or Status.INFO, line.text))
    return outcome


def _run_id(document: "Document | None") -> str:
    value = document.payload.get("run_id") if document is not None else None
    return value if isinstance(value, str) and value else "-"


def _run(session: Session, keep_going: bool) -> "Document":
    from dataclasses import replace

    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, Hint, Table
    from cuanta.domain.queue import QueueStatus, queue_order, warm_payload

    container = Container.for_project(session.project, verbose=session.options.verbose)
    try:
        queue = container.mandate_queue()
        slots, parsed = resolve(queue.entries(), container.config.engine)
        ordered = queue_order(slots)
        window = _window(container, ordered[0].provider if ordered else container.config.engine)
    finally:
        container.close()
    if not ordered:
        return Document(blocks=(Hint(EMPTY_HINT),), payload={"ran": False, "queue": []})
    declined = {
        "ran": False,
        "queue": [slot_payload(slot, parsed[slot.entry.id]) for slot in ordered],
        "warm": warm_payload(window),
    }
    plan = queue_table("queue run order", ordered, parsed)
    if not session.options.yes:
        if not session.interactive:
            count = len(ordered)
            return Document(
                blocks=(
                    plan,
                    warm_line(window),
                    Hint(f"nothing run · add --yes to run {count} queued mandates back to back"),
                ),
                payload=declined,
            )
        session.presenter.render(Document(blocks=(plan, warm_line(window))))
        if not typer.confirm(f"Run {len(ordered)} queued mandates back to back?", default=False):
            return Document(blocks=(Hint("nothing run"),), payload=declined)
    unattended = replace(session, options=replace(session.options, yes=True))
    documents: dict[str, Document] = {}
    report = queue.run(
        ordered,
        lambda slot: _launch(unattended, slot, parsed[slot.entry.id], documents),
        keep_going,
    )
    ran = [
        item
        for item in report.outcomes
        if item.status not in {QueueStatus.NOT_RUN, QueueStatus.GONE}
    ]
    closing = Container.for_project(session.project, verbose=session.options.verbose)
    try:
        final = _window(closing, (ran or report.outcomes)[-1].slot.provider)
    finally:
        closing.close()
    rows = tuple(
        (
            item.slot.entry.id,
            STATUS_TEXT[item.status.value],
            item.slot.kind or "-",
            item.slot.provider,
            item.slot.model or DEFAULT_MODEL,
            _run_id(documents.get(item.slot.entry.id)),
            item.detail or "-",
        )
        for item in report.outcomes
    )
    table = Table(
        "queue run",
        (
            Column("id"),
            Column("status"),
            Column("type"),
            Column("engine"),
            Column("model"),
            Column("run"),
            Column("note"),
        ),
        rows,
    )
    left = f"{report.left} left in the queue · cuanta queue list" if report.left else "queue empty"
    results: list[dict[str, object]] = []
    for item in report.outcomes:
        shown = documents.get(item.slot.entry.id)
        results.append(
            {
                **slot_payload(item.slot, parsed[item.slot.entry.id]),
                "status": item.status.value,
                "detail": item.detail or None,
                "result": shown.payload if shown is not None else None,
            }
        )
    return Document(
        blocks=(table, warm_line(final), Hint(left)),
        payload={
            "ran": True,
            "ok": report.ok,
            "keep_going": keep_going,
            "results": results,
            "left": report.left,
            "warm": warm_payload(final),
        },
        exit_code=0 if report.ok else 1,
    )


def _clear(session: Session, ids: tuple[str, ...]) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint, Line
    from cuanta.domain.progress import Status

    container = Container.for_project(session.project)
    try:
        queue = container.mandate_queue()
        chosen = [entry.id for entry in queue.selected(ids)]
        if not chosen:
            return Document(blocks=(Hint(EMPTY_HINT),), payload={"removed": []})
        named = ", ".join(chosen)
        if not session.options.yes:
            if not session.interactive:
                return Document(
                    blocks=(
                        Line(f"would remove {len(chosen)} queued mandates: {named}", Status.INFO),
                        Hint("nothing removed · add --yes to clear"),
                    ),
                    payload={"removed": [], "selected": chosen},
                )
            if not typer.confirm(f"Remove {len(chosen)} queued mandates ({named})?", default=False):
                return Document(blocks=(Hint("nothing removed"),), payload={"removed": []})
        removed = queue.discard(chosen)
        left = len(queue.entries())
    finally:
        container.close()
    return Document(
        blocks=(
            Line(f"removed {len(removed)} queued mandates: {', '.join(removed)}", Status.OK),
            Hint(f"{left} left in the queue" if left else "queue empty"),
        ),
        payload={"removed": list(removed), "left": left},
    )
