from __future__ import annotations

import string
import time
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from cuanta.adapters.engines.claude_code import build_command
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.domain.change_plan import (
    ChangePlan,
    EditTarget,
    compile_change_plan,
    deny_rules,
    mentioned_paths,
    move_plan,
    plan_metrics,
    plan_notes,
    read_only_notes,
)
from cuanta.domain.code_index import IndexedFile, IndexRow
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import english
from tests.real_run import phased_mandate
from tests.unit.test_lean_session import NamedEngine


def _file(path: str) -> IndexedFile:
    return IndexedFile(path, "current", "typescript", 100, coverage="ast")


@pytest.mark.parametrize(
    "out", ["Do not touch `hero-nutfall-3d/**`.", "Sin tocar `hero-nutfall-3d/**`."]
)
def test_gsap_plan_compiles_edit_read_protected_and_verify_without_remote(out: str) -> None:
    paths = (
        "src/app/globals.css",
        "src/lib/gsap.ts",
        "src/components/hero-nutfall-3d/view.tsx",
        "tests/gsap.test.ts",
    )
    files = tuple(_file(path) for path in paths)
    tests = (
        IndexRow(
            "test",
            paths[3],
            "current",
            "ast",
            "tsc --noEmit\nnpm run build",
            target=paths[1],
            relation="tests",
        ),
    )
    request = MandateRequest(
        "bug",
        "Fix GSAP reveal fallback",
        "GSAP must leave content visible",
        "src/app/globals.css src/lib/gsap.ts",
        out_of_scope=out,
    )
    plan = compile_change_plan(
        request, files, (), (), (), (), (), tests, ("tsc --noEmit", "npm run lint")
    )
    assert {item.path for item in plan.edit} == {paths[0], paths[1], paths[3]}
    assert "src/components/hero-nutfall-3d/**" in plan.guard
    assert plan.verify == ("tsc --noEmit", "npm run lint", "npm run build")
    assert all(0 < item.confidence <= 1 for item in plan.edit)
    assert "Edit(src/components/hero-nutfall-3d/**)" in deny_rules(plan)
    assert "Write(src/components/hero-nutfall-3d/**)" in deny_rules(plan)


def test_future_protected_paths_survive_without_current_files() -> None:
    plan = compile_change_plan(
        MandateRequest(
            "feature",
            "Add cart",
            out_of_scope="Keep future/private.py and future/generated/** untouched",
        ),
        (_file("cart.py"),),
        (),
        (),
        (),
        (),
        (),
        (),
    )
    assert "future/private.py" in plan.guard and "future/generated/**" in plan.guard
    assert all(".." not in path for path in plan.guard)


def test_investigation_and_moved_choices_preserve_read_only() -> None:
    plan = compile_change_plan(
        MandateRequest("investigation", "Explain cart.py"),
        (_file("cart.py"),),
        (),
        (),
        (),
        (),
        (),
        (),
    )
    assert not plan.edit and plan.read == ("cart.py",) and plan.read_only
    moved = move_plan(plan, "cart.py", "edit")
    assert not moved.edit and moved.read == ("cart.py",)
    with pytest.raises(ValueError, match="inside the project"):
        move_plan(plan, "../outside.py", "edit")


def test_actual_snapshot_paths_distinguish_unplanned_and_guarded_changes() -> None:
    plan = ChangePlan((EditTarget("cart.py", 1),), guard=("renderer/**",))
    result = plan_metrics(plan, ("cart.py", "new.py", "renderer/view.tsx"), "claude")
    assert result["out_of_plan_edits"] == ("new.py", "renderer/view.tsx")
    assert result["guard_violations"] == ("renderer/view.tsx",)
    assert plan_metrics(plan, (), "codex")["enforcement"] == "best-effort"


@pytest.mark.parametrize("readonly", [False, True])
def test_central_strict_policy_has_no_shell_or_writer_bypass(
    tmp_path: Path, readonly: bool
) -> None:
    plan = ChangePlan((EditTarget("cart.py", 1),), guard=("renderer/**",), read_only=readonly)
    engine = NamedEngine("claude")
    launcher = EngineLauncher(
        engine,
        MemoryLedger(),
        FixedClock(),
        lambda: "RUN",
        lambda size: b"x" * size,
        "fixture",
        4318,
        None,
    )
    request = launcher.request(
        LaunchSpec(
            "mandate",
            "request",
            str(tmp_path),
            ("Read", "Edit", "Bash", "Agent"),
            read_only=readonly,
            change_plan=plan,
        ),
        "RUN",
        "trace",
        None,
    )
    command = build_command(("claude",), request)
    tools = command[command.index("--tools") + 1].split(",")
    assert "Bash" not in tools and "PowerShell" not in tools and "Skill" not in tools
    assert "Agent" in tools and "Task" in tools
    assert request.strict_guard
    assert "Bash" in request.disallowed_tools
    assert (
        all(
            writer in request.disallowed_tools
            for writer in ("Edit", "Write", "MultiEdit", "NotebookEdit")
        )
        if readonly
        else "Write(renderer/**)" in request.disallowed_tools
    )
    if readonly:
        assert "Edit" not in tools and "Write" not in tools


BACKEND = ("app/services/interpreter.py", "app/routers/consult.py", "tests/test_consult.py")
PHASES = (
    "## Fase 1 — Auditoría (solo lectura)\n"
    "Leer app/services/interpreter.py:287-289 y anotar el flujo.\n\n"
    "## Fase 2 — Corrección\n"
    "Corregir app/services/interpreter.py y app/routers/consult.py.\n"
)


def _backend(request: MandateRequest, extra: tuple[str, ...] = ()) -> ChangePlan:
    files = tuple(_file(path) for path in (*BACKEND, *extra))
    return compile_change_plan(request, files, (), (), (), (), (), ())


def test_an_excluded_file_is_never_ranked_but_stays_when_the_request_names_it() -> None:
    files = tuple(_file(path) for path in (*BACKEND, "notes/interpreter.md"))
    excluded = frozenset({"app/services/interpreter.py", "notes/interpreter.md"})
    anchored = MandateRequest("bug", "the IndexError at app/services/interpreter.py:20")
    plan = compile_change_plan(anchored, files, (), (), (), (), (), (), excluded=excluded)
    assert (
        EditTarget(
            "app/services/interpreter.py",
            1.0,
            "explicit request anchor app/services/interpreter.py:20",
        )
        in plan.edit
    )
    named = MandateRequest("bug", "fix the steps in notes/interpreter.md")
    kept = compile_change_plan(named, files, (), (), (), (), (), (), excluded=excluded)
    assert EditTarget("notes/interpreter.md", 1.0, "explicit request path") in kept.edit
    ranked = MandateRequest("bug", "the interpreter drops an intent")
    plain = compile_change_plan(ranked, files, (), (), (), (), (), (), excluded=excluded)
    assert excluded.isdisjoint({target.path for target in plain.edit})
    assert excluded.isdisjoint(plain.read)
    unexcluded = compile_change_plan(ranked, files, (), (), (), (), (), ())
    assert "app/services/interpreter.py" in {target.path for target in unexcluded.edit}


def test_anchors_from_every_field_become_explicit_targets_and_never_new_files() -> None:
    request = MandateRequest(
        "feature",
        "Implementar el mandato",
        why=r"Revisar C:\proj\app\services\interpreter.py:287-289 y app/models/legacy.py:12.",
        tests=r"tests/test_consult.py:12 cubre app\routers\consult.py:67",
    )
    reasons = {item.path: item.reason for item in _backend(request).edit}
    assert reasons == {
        "app/services/interpreter.py": (
            "explicit request anchor app/services/interpreter.py:287-289"
        ),
        "app/routers/consult.py": "explicit request anchor app/routers/consult.py:67",
        "tests/test_consult.py": "explicit request anchor tests/test_consult.py:12",
    }


def test_the_tests_field_names_edit_targets_and_constraints_only_add_context() -> None:
    request = MandateRequest(
        "bug",
        "Corregir el corte de intenciones",
        why="Se pierde la segunda intención.",
        constraints="Respetar app/settings.py y app/routers/consult.py:10",
        tests="Ampliar tests/test_consult.py",
    )
    plan = _backend(request, ("app/settings.py",))
    assert {item.path: item.reason for item in plan.edit} == {
        "tests/test_consult.py": "explicit request path"
    }
    assert {"app/settings.py", "app/routers/consult.py"} <= set(plan.read)


def test_read_only_words_inside_a_phase_leave_a_phased_writing_request_writable() -> None:
    request = MandateRequest("feature", "Implementar el mandato", why="Contexto.\n\n" + PHASES)
    plan = _backend(request)
    assert not plan.read_only
    assert {"app/services/interpreter.py", "app/routers/consult.py"} <= {
        item.path for item in plan.edit
    }
    assert read_only_notes(request, plan) == ()


@pytest.mark.parametrize(
    ("request_", "phrase"),
    [
        (
            MandateRequest("feature", "Implementar", why="Solo lectura al principio.\n" + PHASES),
            "Solo lectura",
        ),
        (MandateRequest("bug", "Corregir", why=PHASES, constraints="read-only"), "read-only"),
        (
            MandateRequest("refactor", "Ordenar", why="## Fase 1 — Revisión (solo lectura)"),
            "solo lectura",
        ),
    ],
)
def test_read_only_words_outside_the_phases_still_freeze_the_plan_and_say_why(
    request_: MandateRequest, phrase: str
) -> None:
    plan = _backend(request_)
    assert plan.read_only and not plan.edit
    assert [english(message) for message in read_only_notes(request_, plan)] == [
        f'Change plan: read-only, because the request says "{phrase}"'
    ]


def test_an_investigation_stays_read_only_without_a_read_only_note() -> None:
    request = MandateRequest("investigation", "Entender el flujo", why=PHASES)
    plan = _backend(request)
    assert plan.read_only and not plan.edit
    assert read_only_notes(request, plan) == ()
    assert read_only_notes(MandateRequest("feature", "x"), None) == ()


def test_a_phased_mandate_only_guesses_new_files_that_look_like_files() -> None:
    request = MandateRequest(
        "feature",
        "Implementar el mandato adjunto",
        why=phased_mandate(),
        tests="pytest en verde",
        out_of_scope="el frontend",
    )
    assert {item.path for item in _backend(request).edit} == {
        "app/routers/consult.py",
        "app/services/interpreter.py",
        "tests/test_consult.py",
        "CHANGELOG.md",
        "docs/audits/auditoria-interprete.md",
    }
    guessed = MandateRequest(
        "feature",
        "Create app/features/whatsapp and app/widgets/badge.tsx",
        why="Steps 2.1 and 3.12, e.g. y/o `POST /consult` and /api/v1",
    )
    edits = {item.path for item in _backend(guessed).edit}
    assert {"app/features/whatsapp", "app/widgets/badge.tsx"} <= edits
    assert not edits & {"2.1", "3.12", "e.g", "y/o", "POST /consult", "/api/v1", "api/v1"}


UNINDEXED = (
    "uv.lock",
    "poetry.lock",
    "yarn.lock",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "nginx.conf",
    "infra/main.tf",
    "certs/server.pem",
    ".env",
    "Dockerfile.prod",
    "config/settings.prod",
)


def test_out_of_scope_files_the_index_never_holds_stay_guarded() -> None:
    listed = ", ".join(f"`{path}`" if path == ".env" else path for path in UNINDEXED)
    request = MandateRequest(
        "feature",
        "Implementar el mandato",
        why="Contexto.",
        tests="pytest en verde",
        out_of_scope=f"No tocar {listed}",
    )
    plan = _backend(request)
    assert set(UNINDEXED) <= set(plan.guard)
    assert {f"Edit({path})" for path in UNINDEXED} <= set(deny_rules(plan))
    assert plan_metrics(plan, (), "claude")["enforcement"] == "strict-tools"
    phased = MandateRequest(
        "feature",
        "Implementar el mandato",
        why=f"## Fase 1\nNo toques {listed}.\n## Fase 2\nCorregir el flujo.\n",
        tests="pytest en verde",
        out_of_scope="el frontend",
    )
    assert set(UNINDEXED) <= set(_backend(phased).guard)


PHASE_GUARDS = (
    "Contexto del cambio.\n\n"
    "## Fase 1 — Auditoría\n"
    "{forbid}\n\n"
    "## Fase 3 — Endpoint\n"
    "Corrige app/routers/consult.py:67.\n"
)
RELEASED_NOTE = (
    "Change plan: app/routers/consult.py stays editable: a phase says not to touch it, but the "
    "request anchors or names it elsewhere; put it in Out of scope to protect it"
)


@pytest.mark.parametrize(
    "forbid",
    [
        "No toques app/routers/consult.py en esta fase.",
        "Durante esta fase no toques app/routers/consult.py.",
        "Do not touch app/routers/consult.py in this phase.",
        "During this phase, do not edit app/routers/consult.py.",
    ],
)
def test_a_prohibition_scoped_to_its_phase_never_guards_the_run(forbid: str) -> None:
    request = MandateRequest(
        "feature", "Implementar el mandato", why=PHASE_GUARDS.format(forbid=forbid)
    )
    plan = _backend(request)
    assert "app/routers/consult.py" not in plan.guard and not plan.released
    assert {item.path: item.reason for item in plan.edit}["app/routers/consult.py"] == (
        "explicit request anchor app/routers/consult.py:67"
    )
    assert plan_notes(request, plan) == ()


@pytest.mark.parametrize(
    ("kind", "elsewhere"),
    [
        ("bug", ""),
        ("feature", "Ampliar app/routers/consult.py con el campo truncated."),
        ("refactor", "Paso final: ampliar tests/test_consult.py y app/routers/consult.py."),
    ],
)
def test_a_phase_prohibition_of_a_path_the_request_edits_elsewhere_is_dropped_with_a_note(
    kind: str, elsewhere: str
) -> None:
    why = PHASE_GUARDS.format(
        forbid="No toques app/routers/consult.py ni app/services/interpreter.py."
    )
    if elsewhere:
        why = why.replace("Corrige app/routers/consult.py:67.", elsewhere)
    request = MandateRequest(kind, "Corregir el endpoint", why=why, constraints="mypy")
    plan = _backend(request)
    assert plan.guard == ("app/services/interpreter.py",)
    assert plan.released == ("app/routers/consult.py",)
    assert "app/routers/consult.py" in {item.path for item in plan.edit}
    assert "Edit(app/routers/consult.py)" not in deny_rules(plan)
    assert [english(message) for message in plan_notes(request, plan)] == [RELEASED_NOTE]
    guarded_again = move_plan(plan, "app/routers/consult.py", "guard")
    assert plan_notes(request, guarded_again) == ()


def test_a_phase_prohibition_inside_a_global_guard_stays_guarded_without_a_note() -> None:
    forbid = PHASE_GUARDS.format(forbid="No toques app/routers/consult.py.")
    request = MandateRequest(
        "bug", "Corregir el endpoint", why="No toques app/routers/.\n\n" + forbid
    )
    plan = _backend(request)
    assert {"app/routers/**", "app/routers/consult.py"} <= set(plan.guard)
    assert not plan.released and plan_notes(request, plan) == ()
    assert "app/routers/consult.py" not in {item.path for item in plan.edit}
    phased = MandateRequest("bug", "Corregir el endpoint", why=forbid)
    released = _backend(phased)
    assert released.released == ("app/routers/consult.py",)
    covered = move_plan(released, "app/routers/**", "guard")
    assert covered.released == ("app/routers/consult.py",)
    assert plan_notes(phased, covered) == ()


def test_a_phase_prohibition_that_only_names_its_own_anchor_still_guards() -> None:
    why = PHASE_GUARDS.format(forbid="No toques app/services/interpreter.py:287-289.")
    plan = _backend(MandateRequest("bug", "Corregir el endpoint", why=why))
    assert plan.guard == ("app/services/interpreter.py",) and not plan.released


@pytest.mark.parametrize(
    ("kind", "why", "out"),
    [
        (
            "feature",
            "No toques app/routers/consult.py.\n\n" + PHASE_GUARDS.format(forbid="Leer."),
            "z",
        ),
        ("bug", PHASE_GUARDS.format(forbid="Leer el flujo."), "app/routers/consult.py"),
        (
            "investigation",
            PHASE_GUARDS.format(forbid="No toques app/routers/consult.py en esta fase."),
            "z",
        ),
        (
            "bug",
            "No toques app/routers/consult.py en esta fase. Corrige app/routers/consult.py:67.",
            "z",
        ),
    ],
)
def test_the_preamble_the_out_of_scope_field_and_unphased_evidence_keep_their_guards(
    kind: str, why: str, out: str
) -> None:
    plan = _backend(MandateRequest(kind, "Implementar el mandato", why=why, out_of_scope=out))
    assert "app/routers/consult.py" in plan.guard and not plan.released
    assert "app/routers/consult.py" not in {item.path for item in plan.edit}


NAMED_IN_PHASE = (
    "leído del mismo objeto de ajustes",
    "leído del mismo objeto de ajustes (definido en `pyproject.toml`)",
)
TRAILING_RULES = (
    phased_mandate().replace(*NAMED_IN_PHASE)
    + "\n## Restricciones\n\n- No toques `pyproject.toml`: la configuración no cambia.\n",
    "Context.\n\n## Phase 1 - Fix\nFix app/services/interpreter.py:5. The settings come from "
    "pyproject.toml.\n\n## Phase 2 - Tests\nAdd tests in tests/test_consult.py.\n\n"
    "## Rules\nDo not modify pyproject.toml.\n",
    "## Fase 1 — Auditoría\nLeer pyproject.toml.\n\n## Notas\nNo toques pyproject.toml.\n\n"
    "## Fase 2 — Corrección\nCorregir app/services/interpreter.py.\n",
    "**Fase 1:** leer pyproject.toml.\n**Fase 2:** corregir app/routers/consult.py.\n"
    "**Reglas generales**\nNo toques pyproject.toml.\n",
)


@pytest.mark.parametrize("why", TRAILING_RULES, ids=["real_run", "english", "between", "bold"])
def test_a_prohibition_after_the_phases_under_its_own_heading_guards_the_run(why: str) -> None:
    request = MandateRequest("bug", "Corregir el intérprete", why, out_of_scope="none")
    plan = _backend(request, ("pyproject.toml",))
    assert "pyproject.toml" in plan.guard and not plan.released
    assert "pyproject.toml" not in {item.path for item in plan.edit}
    assert "Edit(pyproject.toml)" in deny_rules(plan)
    assert plan_notes(request, plan) == ()


def test_a_prohibition_under_a_heading_inside_a_phase_stays_local_to_it() -> None:
    why = (
        "## Fase 1 — Auditoría\nLeer el flujo.\n\n### Reglas de la fase\n"
        "No toques app/routers/consult.py.\n\n## Fase 2 — Corrección\n"
        "Corrige app/routers/consult.py:67.\n"
    )
    plan = _backend(MandateRequest("bug", "Corregir el endpoint", why=why))
    assert plan.released == ("app/routers/consult.py",)
    assert "app/routers/consult.py" in {item.path for item in plan.edit}


TRAILING_READ_ONLY = (
    "\n## Reglas generales\n\nEste mandato es de solo lectura: no cambies nada del código.\n"
)


@pytest.mark.parametrize("kind", ["feature", "bug", "refactor"])
def test_read_only_words_after_the_phases_under_their_own_heading_freeze_the_plan(
    kind: str,
) -> None:
    request = MandateRequest(kind, "Revisar el intérprete", phased_mandate() + TRAILING_READ_ONLY)
    plan = _backend(request)
    assert plan.read_only and not plan.edit
    assert [english(message) for message in read_only_notes(request, plan)] == [
        'Change plan: read-only, because the request says "solo lectura"'
    ]
    inside = phased_mandate().replace(
        "## Cómo trabajar", TRAILING_READ_ONLY.strip().replace("## Reglas", "### Reglas")
    )
    assert not _backend(MandateRequest(kind, "Revisar el intérprete", inside)).read_only


@pytest.mark.parametrize("kind", ["feature", "bug", "refactor"])
def test_the_real_run_mandate_keeps_its_plan_once_the_phases_are_bounded(kind: str) -> None:
    request = MandateRequest(
        kind,
        "Ejecuta el mandato por fases",
        phased_mandate(),
        tests="pytest en verde",
        out_of_scope="docs del cliente",
    )
    plan = _backend(request, ("pyproject.toml",))
    assert (plan.guard, plan.released, plan.read_only) == ((), (), False)
    assert plan_notes(request, plan) == ()


@pytest.mark.parametrize("field", ["what", "where", "constraints", "out_of_scope"])
def test_read_only_words_inside_a_phase_of_any_field_leave_the_request_writable(
    field: str,
) -> None:
    values = {"what": "Implementar el mandato", "where": "", "constraints": "", "out_of_scope": ""}
    values[field] = f"{values[field]}\n{PHASES}".strip()
    request = MandateRequest(
        "feature",
        values["what"],
        "Contexto.",
        values["where"],
        values["constraints"],
        "pytest en verde",
        values["out_of_scope"] or "z",
    )
    plan = _backend(request)
    assert not plan.read_only
    assert read_only_notes(request, plan) == ()
    frozen = MandateRequest("feature", "Implementar", "Contexto.", constraints="read-only")
    assert _backend(frozen).read_only


TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/usr/lib/python3.12/json/decoder.py", line 337, in decode\n'
    '  File "/srv/proj/.venv/lib/python3.12/site-packages/starlette/routing.py", line 74\n'
    '  File "D:\\work\\proj\\.venv\\Lib\\site-packages\\fastapi\\routing.py", line 301\n'
    '  File "/srv/proj/app/routers/consult.py", line 67, in consult\n'
    "    payload = json.loads(err.value)\n"
    "Warning in .venv/lib/python3.12/site-packages/pydantic/fields.py and node_modules/x/y.js\n"
    "See https://example.invalid/app/notes.md and ~/proj/app/local.py\n"
    "json.decoder.JSONDecodeError: Expecting value\n"
)


def test_a_pasted_traceback_never_adds_absolute_or_dependency_paths_as_new_files() -> None:
    plan = _backend(MandateRequest("bug", "Corregir el error del endpoint", why=TRACEBACK))
    assert not [item.path for item in plan.edit if item.reason == "explicit new file path"]
    named = MandateRequest("feature", "Crear app/new_module.py y app/widgets/badge.tsx", "y")
    assert {"app/new_module.py", "app/widgets/badge.tsx"} <= {
        item.path for item in _backend(named).edit
    }


@pytest.mark.parametrize(
    "why",
    [
        "My local settings live in `~/.config/consult/settings.toml`.",
        "The log is `$HOME/consult/run.log` and `$HOME/proj/x.py`.",
        "Settings: `%APPDATA%/consult/settings.toml`.",
        "Open $HOME/consult/run.log and ~user/consult/notes.md after the crash.",
        "See `~user/consult/notes.md`.",
    ],
)
def test_home_relative_paths_never_become_new_file_targets(why: str) -> None:
    request = MandateRequest("bug", "Fix the consult endpoint crash", why, out_of_scope="none")
    assert not [item.path for item in _backend(request).edit if item.reason.endswith("file path")]
    assert mentioned_paths(why, BACKEND, keep_unknown=True, guess=True) == ()
    named = replace(request, why=f"{why} Create `app/settings/local.toml`.")
    assert "app/settings/local.toml" in {item.path for item in _backend(named).edit}


SCAN_BYTES = 200_000
SCAN_BUDGET_S = 0.5
PLAN_BUDGET_S = 1.0
SCAN_PATHS = tuple(
    sorted(f"pkg{index // 50}/mod{index % 50}/file{index}.py" for index in range(2000))
)
SCAN_FILES = tuple(_file(path) for path in SCAN_PATHS)
SCAN_ALPHABETS = {
    "hex": string.hexdigits,
    "base64": string.ascii_letters + string.digits + "+/",
    "base64url": string.ascii_letters + string.digits + "-_",
}
SCAN_UNITS = {"dotted": "a.", "dashed": "a-b.", "camel": "Ab", "slashed": "a/"}


def _scan_text(name: str) -> str:
    if name in SCAN_ALPHABETS:
        return "".join(Random(7).choices(SCAN_ALPHABETS[name], k=SCAN_BYTES))
    unit = SCAN_UNITS[name]
    return (unit * (SCAN_BYTES // len(unit) + 1))[:SCAN_BYTES]


@pytest.mark.perf
@pytest.mark.parametrize("name", sorted({*SCAN_ALPHABETS, *SCAN_UNITS}))
def test_path_scans_of_a_200_kb_evidence_stay_within_budget(name: str) -> None:
    text = _scan_text(name)
    for guess in (False, True):
        started = time.perf_counter()
        mentioned_paths(text, SCAN_PATHS, keep_unknown=True, guess=guess)
        assert time.perf_counter() - started < SCAN_BUDGET_S
    request = MandateRequest("bug", "Corregir el error", why=text, out_of_scope="z")
    started = time.perf_counter()
    compile_change_plan(request, SCAN_FILES, (), (), (), (), (), ())
    assert time.perf_counter() - started < PLAN_BUDGET_S


def test_relative_anchors_make_the_deeper_files_they_name_the_anchored_targets() -> None:
    rooted = ("main.py", "conftest.py", "backend/app/main.py", "backend/tests/conftest.py")
    request = MandateRequest(
        "bug", "Corregir el arranque", why="Falla en app/main.py:42 y tests/conftest.py:7"
    )
    plan = compile_change_plan(
        request, tuple(_file(path) for path in rooted), (), (), (), (), (), ()
    )
    reasons = {item.path: item.reason for item in plan.edit}
    assert reasons["backend/app/main.py"] == "explicit request anchor backend/app/main.py:42"
    assert reasons["backend/tests/conftest.py"] == (
        "explicit request anchor backend/tests/conftest.py:7"
    )
    assert not any(
        reasons.get(path, "").startswith("explicit request anchor")
        for path in ("main.py", "conftest.py")
    )
