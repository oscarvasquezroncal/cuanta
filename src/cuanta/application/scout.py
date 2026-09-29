from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from cuanta.domain.agents import role_of
from cuanta.domain.engine import AssistantText, EngineEvent, ToolCall
from cuanta.domain.evidence_pack import (
    EvidencePack,
    LinesOf,
    PackCheck,
    SeniorScope,
    check_pack,
    fallback_pack,
    outside_edits,
    pack_instruction,
    parse_pack,
    render_pack,
    touched_files,
)
from cuanta.domain.role_handoff import (
    estimate_tokens,
    handoff_paths,
    last_json_object,
    schema_instruction,
)
from cuanta.domain.routing import Role
from cuanta.domain.scout import SCOUT_BODY, ScoutMode
from cuanta.domain.scout_report import scout_payload
from cuanta.domain.spectrum import EDIT_TOOLS

AFFECTED_TESTS = "cuanta test --affected"
DEFAULT_READ_SLACK = 2
PATH_KEYS = ("file_path", "notebook_path", "path")


def _opener(role: Role) -> str:
    return (
        f"You are the {role.value} of a pipeline where every role runs separately. "
        "Do only your role's part."
    )


def scout_prompt(
    body: str,
    request_text: str,
    context: str = "",
    volatile: str = "",
    advice: str = "",
) -> str:
    parts = [body.strip() or SCOUT_BODY, _opener(Role.SCOUT)]
    if context:
        parts.append(context)
    if volatile:
        parts.append(volatile)
    parts.append(f"=== REQUEST ===\n{request_text}")
    if advice:
        parts.append(advice)
    parts.append(pack_instruction())
    return "\n\n".join(parts)


def read_budget(pack: EvidencePack, planned: int = 0) -> int:
    if planned > 0:
        return max(planned, len(set(pack.edit)) + DEFAULT_READ_SLACK)
    return len({*pack.edit, *pack.files}) + DEFAULT_READ_SLACK


def senior_rules(reads: int) -> str:
    return "\n".join(
        (
            "WORK FROM THE PACK: it is all the exploration this run does. Open only the pack's "
            "anchored ranges and the files in its edit set; do not explore the code again.",
            f"Read budget: about {reads} file reads. cuanta counts every read outside the pack's "
            "files and the edit set as exploration leak.",
            "Edit only the files in the edit set. If the change needs another file, edit it only "
            "when you must, list it in your handoff's plan.edit and say why in decisions; cuanta "
            "flags every edit outside the edit set in the result.",
        )
    )


def senior_prompt(
    body: str,
    request_text: str,
    pack: EvidencePack,
    reads: int,
    advice: str = "",
) -> str:
    parts = [body.strip()] if body.strip() else []
    parts.append(_opener(Role.SENIOR))
    parts.append(f"=== REQUEST ===\n{request_text}")
    parts.append(f"=== EVIDENCE PACK FROM THE SCOUT (checked by cuanta) ===\n{render_pack(pack)}")
    parts.append(senior_rules(reads))
    if advice:
        parts.append(advice)
    parts.append(schema_instruction())
    return "\n\n".join(parts)


def checker_prompt(
    body: str,
    request_text: str,
    chain: str,
    digest: str,
    tests: Sequence[str],
    advice: str = "",
    no_builds: bool = False,
) -> str:
    parts = [body.strip()] if body.strip() else []
    parts.append(_opener(Role.TESTER))
    parts.append(f"=== REQUEST ===\n{request_text}")
    parts.append(
        "=== CHANGES (from cuanta's snapshots) ===\n" + (digest or "no file changed so far")
    )
    run = (
        "cuanta runs the checks; do not run tests or builds yourself."
        if no_builds
        else f"Run `{AFFECTED_TESTS}` to run only the tests these changes affect."
    )
    linked = "\n".join(f"- {test}" for test in tests) or "- none"
    parts.append(
        "=== AFFECTED TESTS ===\n"
        f"{run}\nTests the scout linked:\n{linked}\n"
        "Do not re-explore the code: open only the changed files, the diff above and these tests."
    )
    parts.append(
        "=== HANDOFF CHAIN FROM EARLIER ROLES ===\n" + (chain or "none: you are the first role.")
    )
    if advice:
        parts.append(advice)
    parts.append(schema_instruction())
    return "\n\n".join(parts)


def named_edits(text: str) -> tuple[str, ...]:
    data = last_json_object(text)
    if data is None:
        return ()
    plan = data.get("plan")
    edits = plan.get("edit") if isinstance(plan, dict) else data.get("edit")
    return handoff_paths(edits)


def tool_path(call: ToolCall) -> str:
    for key in PATH_KEYS:
        value = call.inputs.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


@dataclass
class SessionWatch:
    spawns: dict[str, str] = field(default_factory=dict)
    texts: dict[str, list[str]] = field(default_factory=dict)
    prompts: dict[str, int] = field(default_factory=dict)
    edits: dict[str, list[str]] = field(default_factory=dict)

    def __call__(self, event: EngineEvent) -> None:
        if isinstance(event, ToolCall) and event.spawned_agent and event.tool_use_id:
            agent = event.spawned_agent
            self.spawns[event.tool_use_id] = agent
            prompt = event.inputs.get("prompt")
            if isinstance(prompt, str):
                self.prompts[agent] = self.prompts.get(agent, 0) + estimate_tokens(prompt)
        elif isinstance(event, ToolCall) and event.parent_tool_use_id in self.spawns:
            path = tool_path(event)
            if event.name in EDIT_TOOLS and path:
                agent = self.spawns[event.parent_tool_use_id]
                self.edits.setdefault(agent, []).append(path)
        elif isinstance(event, AssistantText) and event.parent_tool_use_id in self.spawns:
            agent = self.spawns[event.parent_tool_use_id]
            self.texts.setdefault(agent, []).append(event.text)

    def dispatched_to(self, role: Role) -> bool:
        return any(role_of(agent) is role for agent in self.spawns.values())

    def edited(self, role: Role) -> tuple[str, ...]:
        return tuple(
            path for agent, paths in self.edits.items() if role_of(agent) is role for path in paths
        )

    def last(self, role: Role) -> str:
        found = [
            texts[-1] for agent, texts in self.texts.items() if role_of(agent) is role and texts
        ]
        return found[-1] if found else ""

    def dispatched(self, role: Role) -> int:
        return sum(tokens for agent, tokens in self.prompts.items() if role_of(agent) is role)


def watched(
    observer: Callable[[EngineEvent], None] | None, watch: SessionWatch
) -> Callable[[EngineEvent], None]:
    def both(event: EngineEvent) -> None:
        if observer is not None:
            observer(event)
        watch(event)

    return both


def session_check(
    watch: SessionWatch, lines_of: LinesOf | None, planned_edit: Sequence[str]
) -> PackCheck:
    text = watch.last(Role.SCOUT)
    pack = parse_pack(text) or fallback_pack(text, (), ())
    return check_pack(pack, lines_of, planned_edit)


def session_scout(
    watch: SessionWatch,
    lines_of: LinesOf | None,
    planned_edit: Sequence[str],
    changed: Sequence[str],
    store: Callable[[str], str],
    run_id: str,
) -> dict[str, object]:
    check = session_check(watch, lines_of, planned_edit)
    capsule = store(render_pack(check.pack))
    edit = check.pack.edit
    senior_changed = touched_files(changed, watch.edited(Role.SENIOR))
    senior = SeniorScope(
        edit,
        check.pack.files,
        None,
        outside_edits(senior_changed, edit, named_edits(watch.last(Role.SENIOR))),
    )
    payload = scout_payload(ScoutMode.NATIVE.value, run_id, capsule, check, senior)
    payload["dispatched"] = watch.dispatched_to(Role.SCOUT)
    payload["dispatch_tokens"] = {
        role.value: watch.dispatched(role) for role in (Role.SCOUT, Role.SENIOR, Role.TESTER)
    }
    return payload
