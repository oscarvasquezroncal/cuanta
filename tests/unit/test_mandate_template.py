from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from cuanta.adapters.forge.installer import VendoredForgeKit
from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.doctor import DoctorReport, result, template_check
from cuanta.application.home import HomeSnapshot, next_step
from cuanta.application.instinct import DecisionMaker
from cuanta.application.mandate import MandateService
from cuanta.application.mandate_template import MandateTemplates
from cuanta.domain.errors import DomainFailure
from cuanta.domain.fixes import FixAction, FixKind, classify
from cuanta.domain.forge_template import (
    MANDATE_TEMPLATE,
    TEMPLATE_FIX,
    TemplateState,
    filled_template,
)
from cuanta.domain.mandate import MandateRequest, Shape
from cuanta.domain.messages import english, msg
from cuanta.domain.progress import Status
from tests.tui.fakes import DETECTION

VENDORED = (
    Path(__file__).parents[2]
    / "src/cuanta/assets/forge/skills/agent-system-init/templates/MANDATE_TEMPLATE.template.md"
)
TEAM = ("architecture-analyst", "backend-senior", "tester", "docs-updater")
AGENT = "---\nname: {name}\ndescription: d\ntools: Read\nmodel: sonnet\n---\nBody of {name}.\n"
FEATURE = MandateRequest(type="feature", what="Split the cart total", tests="t", out_of_scope="o")
VERSION = VendoredForgeKit(Path(".")).vendored_version()
WRITTEN = f"docs/MANDATE_TEMPLATE.md was missing: wrote it from the vendored Forge {VERSION}"
PLANNED = f"write: docs/MANDATE_TEMPLATE.md from the vendored Forge {VERSION}"
BROKEN = "docs/MANDATE_TEMPLATE.md has no fenced === REQUEST === block"
UNREADABLE = "docs/MANDATE_TEMPLATE.md cannot be read as a plain file inside the project"
UNUSABLE_HINT = "fix it, or delete it and run cuanta init --template"


class ReadOnlyWorkspace(LocalWorkspace):
    def write_text(self, relative: str, content: str) -> None:
        raise PermissionError(13, "Permission denied", relative)


def team(root: Path, names: tuple[str, ...] = TEAM) -> Path:
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    for name in names:
        (agents / f"{name}.md").write_text(AGENT.format(name=name), encoding="utf-8")
    return root


def keeper(root: Path) -> MandateTemplates:
    return MandateTemplates(LocalWorkspace(root), VendoredForgeKit(root))


def expected(root: Path, senior: str = "backend-senior", project: str = "") -> str:
    return filled_template(VENDORED.read_text(encoding="utf-8"), project or root.name, senior)


def service(root: Path, templates: MandateTemplates | None) -> MandateService:
    ledger = MemoryLedger()
    clock = FixedClock()
    decisions = DecisionMaker(HeuristicInstinct(), ledger, clock.now_iso)
    return MandateService(
        LocalWorkspace(root), ledger, decisions, clock.now_iso, templates=templates
    )


def spoiled(root: Path, content: str | None) -> Path:
    target = root / MANDATE_TEMPLATE
    target.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        target.mkdir()
    else:
        target.write_text(content, encoding="utf-8")
    return root


def test_compose_uses_the_vendored_template_without_writing_it(tmp_path: Path) -> None:
    root = team(tmp_path)
    mandates = service(root, keeper(root))
    composed = mandates.compose(FEATURE, 0, lambda prompt: ["claude", prompt])
    assert not (root / MANDATE_TEMPLATE).exists()
    assert f"# MANDATE — {root.name}" in composed.prompt
    assert "2. **backend-senior** — implements ONE phase per invocation" in composed.prompt
    assert f"{'WHAT:':<16} Split the cart total" in composed.prompt
    pending = composed.template
    assert pending is not None and pending.text == expected(root)
    assert english(pending.planned) == PLANNED
    again = mandates.compose(FEATURE, 0, lambda prompt: ["claude", prompt])
    assert again.template == pending
    assert again.prompt == composed.prompt
    assert not (root / MANDATE_TEMPLATE).exists()


def test_keep_writes_the_pending_template_once_and_says_so(tmp_path: Path) -> None:
    root = team(tmp_path)
    templates = keeper(root)
    assert templates.status().state is TemplateState.RESTORABLE
    text, pending = templates.load()
    assert pending is not None and text == pending.text == expected(root)
    assert not (root / MANDATE_TEMPLATE).exists()
    told = templates.keep(pending)
    assert told is not None
    assert (told.status, told.text) == (Status.INFO, WRITTEN)
    assert told.message == msg("mandate.template_written", path=MANDATE_TEMPLATE, version=VERSION)
    written = (root / MANDATE_TEMPLATE).read_text(encoding="utf-8")
    assert written == expected(root)
    assert templates.status().state is TemplateState.READY
    assert templates.load() == (written, None)
    assert templates.keep(pending) is None
    assert (root / MANDATE_TEMPLATE).read_text(encoding="utf-8") == written


def test_keep_never_writes_for_an_incomplete_team_or_over_an_existing_path(
    tmp_path: Path,
) -> None:
    _, pending = keeper(team(tmp_path / "full")).load()
    assert pending is not None
    partial = team(tmp_path / "partial", ("architecture-analyst", "backend-senior"))
    assert keeper(partial).keep(pending) is None
    assert not (partial / MANDATE_TEMPLATE).exists()
    blocked = spoiled(team(tmp_path / "blocked"), None)
    assert keeper(blocked).status().state is TemplateState.UNREADABLE
    assert keeper(blocked).keep(pending) is None
    assert (blocked / MANDATE_TEMPLATE).is_dir()
    own = spoiled(team(tmp_path / "own"), "# mine\n")
    assert keeper(own).keep(pending) is None
    assert (own / MANDATE_TEMPLATE).read_text(encoding="utf-8") == "# mine\n"


def test_an_unwritable_folder_still_hands_the_run_its_template(tmp_path: Path) -> None:
    templates = MandateTemplates(ReadOnlyWorkspace(team(tmp_path)), VendoredForgeKit(tmp_path))
    text, pending = templates.load()
    assert pending is not None and text == expected(tmp_path)
    told = templates.keep(pending)
    assert told is not None and told.status is Status.WARN
    assert told.message is not None and told.message.key == "mandate.template_unwritable"
    assert "docs/MANDATE_TEMPLATE.md is missing and could not be written" in told.text
    assert "Permission denied" in told.text and VERSION in told.text
    assert not (tmp_path / MANDATE_TEMPLATE).exists()


def test_an_explicit_write_plans_writes_or_explains_why_it_cannot(tmp_path: Path) -> None:
    root = team(tmp_path / "team")
    planned = keeper(root).write(dry_run=True)
    assert (planned.state, planned.written) == (TemplateState.RESTORABLE, False)
    assert english(planned.message) == PLANNED
    assert not (root / MANDATE_TEMPLATE).exists()
    done = keeper(root).write()
    assert (done.state, done.written, english(done.message)) == (
        TemplateState.RESTORABLE,
        True,
        WRITTEN,
    )
    again = keeper(root).write()
    assert (again.state, again.written) == (TemplateState.READY, False)
    assert english(again.message) == "docs/MANDATE_TEMPLATE.md ready"
    partial = team(tmp_path / "partial", ("architecture-analyst", "python-senior"))
    with pytest.raises(DomainFailure) as refused:
        keeper(partial).write()
    assert refused.value.message == (
        "docs/MANDATE_TEMPLATE.md needs the four Forge agents; missing tester.md, docs-updater.md"
    )
    assert refused.value.hint == "run cuanta init"
    unwritable = MandateTemplates(
        ReadOnlyWorkspace(team(tmp_path / "locked")), VendoredForgeKit(tmp_path)
    )
    with pytest.raises(DomainFailure) as locked:
        unwritable.write()
    assert locked.value.message == (
        "could not write docs/MANDATE_TEMPLATE.md "
        "([Errno 13] Permission denied: 'docs/MANDATE_TEMPLATE.md')"
    )
    assert locked.value.hint == "make sure docs is a folder you can write to"


@pytest.mark.parametrize(
    ("content", "problem"),
    [("# my own template\n", BROKEN), ("=== REQUEST ===\nTYPE: x\n", BROKEN), (None, UNREADABLE)],
    ids=["no_block", "unfenced", "folder"],
)
def test_a_template_that_cannot_be_used_fails_with_the_doctor_text_and_hint(
    tmp_path: Path, content: str | None, problem: str
) -> None:
    root = spoiled(team(tmp_path), content)
    templates = keeper(root)
    for attempt in (
        lambda: service(root, templates).compose(FEATURE, 0, lambda prompt: [prompt]),
        templates.load,
        templates.write,
        lambda: templates.write(dry_run=True),
    ):
        with pytest.raises(DomainFailure) as failure:
            attempt()
        assert (failure.value.message, failure.value.hint) == (problem, UNUSABLE_HINT)
    rows = template_check(templates)(DETECTION)
    assert [(row.status, row.detail, row.fix) for row in rows] == [
        (Status.WARN, f"{problem}: {UNUSABLE_HINT}", "")
    ]
    if content is not None:
        with pytest.raises(DomainFailure) as plain:
            service(root, None).compose(FEATURE, 0, lambda prompt: [prompt])
        assert (plain.value.message, plain.value.hint) == (BROKEN, UNUSABLE_HINT)
        assert (root / MANDATE_TEMPLATE).read_text(encoding="utf-8") == content


def link_or_skip(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link)
    except OSError as error:
        pytest.skip(f"symbolic links are not available here: {error}")


def test_a_linked_template_counts_as_present_but_unusable(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    dangling = team(tmp_path / "dangling")
    link_or_skip(dangling / MANDATE_TEMPLATE, outside / "escaped.md")
    inner = team(tmp_path / "inner")
    (inner / "templates").mkdir()
    (inner / "templates" / "mandate.md").write_text("```\n=== REQUEST ===\n```\n", encoding="utf-8")
    link_or_skip(inner / MANDATE_TEMPLATE, inner / "templates" / "mandate.md")
    for root in (dangling, inner):
        templates = keeper(root)
        assert templates.status().state is TemplateState.UNREADABLE
        for attempt in (templates.load, templates.write):
            with pytest.raises(DomainFailure) as refused:
                attempt()
            assert (refused.value.message, refused.value.hint) == (UNREADABLE, UNUSABLE_HINT)
        assert (root / MANDATE_TEMPLATE).is_symlink()
    assert list(outside.iterdir()) == []


def docs_leading_outside(root: Path, outside: Path) -> None:
    docs = root / "docs"
    if sys.platform == "win32":
        winapi: Any = importlib.import_module("_winapi")
        winapi.CreateJunction(str(outside), str(docs))
    else:
        docs.symlink_to(outside, target_is_directory=True)


def test_a_docs_folder_that_leads_outside_the_project_is_never_written_through(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = team(tmp_path / "project")
    docs_leading_outside(root, outside)
    templates = keeper(root)
    assert templates.status().state is TemplateState.UNREADABLE
    with pytest.raises(DomainFailure):
        templates.write()
    with pytest.raises(DomainFailure):
        service(root, templates).compose(FEATURE, 0, lambda prompt: [prompt])
    assert list(outside.iterdir()) == []


def test_a_docs_link_without_forge_agents_leaves_a_missing_template_absent(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    docs_leading_outside(root, outside)
    templates = keeper(root)
    assert templates.status().state is TemplateState.ABSENT
    assert rows(root) == []
    with pytest.raises(DomainFailure) as refused:
        templates.write()
    assert refused.value.hint == "run cuanta init"
    assert list(outside.iterdir()) == []
    (outside / "MANDATE_TEMPLATE.md").write_text("```\n=== REQUEST ===\n```\n", "utf-8")
    assert keeper(root).status().state is TemplateState.UNREADABLE
    assert sorted(path.name for path in outside.iterdir()) == ["MANDATE_TEMPLATE.md"]


def test_the_template_takes_the_project_name_from_the_manifests(tmp_path: Path) -> None:
    named = team(tmp_path / "dry")
    (named / "pyproject.toml").write_text('[project]\nname = "consult-service"\n', "utf-8")
    keeper(named).write()
    text = (named / MANDATE_TEMPLATE).read_text(encoding="utf-8")
    assert text == expected(named, project="consult-service")
    assert text.startswith("# Mandate template — consult-service\n")
    web = team(tmp_path / "web")
    (web / "package.json").write_text('{"name": "shop-web", "version": "1.0.0"}', "utf-8")
    _, pending = keeper(web).load()
    assert pending is not None and pending.text == expected(web, project="shop-web")
    bare = team(tmp_path / "bare")
    (bare / "pyproject.toml").write_text("not = [valid toml", "utf-8")
    _, fallback = keeper(bare).load()
    assert fallback is not None and fallback.text == expected(bare)


def test_compose_keeps_the_init_hint_when_the_team_is_incomplete(tmp_path: Path) -> None:
    root = team(tmp_path, ("architecture-analyst", "backend-senior", "tester"))
    with pytest.raises(DomainFailure) as failure:
        service(root, keeper(root)).compose(FEATURE, 0, lambda prompt: [prompt])
    assert (failure.value.message, failure.value.hint) == (
        "docs/MANDATE_TEMPLATE.md not found",
        "run cuanta init first",
    )
    full = team(tmp_path / "full")
    with pytest.raises(DomainFailure):
        service(full, None).compose(FEATURE, 0, lambda prompt: [prompt])
    assert not (full / MANDATE_TEMPLATE).exists()


def test_runs_that_never_read_the_template_never_write_it(tmp_path: Path) -> None:
    root = team(tmp_path)
    mandates = service(root, keeper(root))
    simple = mandates.compose(FEATURE, 0, lambda prompt: [prompt], simple=True)
    fast = mandates.compose(FEATURE, 0, lambda prompt: [prompt], implementation=True)
    question = MandateRequest(type="investigation", what="Explain", why="How?", out_of_scope="o")
    audit = mandates.compose(question, 0, lambda prompt: [prompt], shape=Shape.SINGLE)
    assert (simple.template, fast.template, audit.template) == (None, None, None)
    assert not (root / MANDATE_TEMPLATE).exists()


def rows(root: Path) -> list[tuple[str, Status, str, str]]:
    return [
        (row.name, row.status, row.detail, row.fix)
        for row in template_check(keeper(root))(DETECTION)
    ]


def test_doctor_reports_the_template_as_part_of_the_forge_install(tmp_path: Path) -> None:
    root = team(tmp_path / "real")
    missing = (
        "docs/MANDATE_TEMPLATE.md missing: cuanta writes it from the vendored Forge "
        f"{VERSION} when a mandate needs it"
    )
    assert rows(root) == [("template", Status.WARN, missing, TEMPLATE_FIX)]
    keeper(root).write()
    assert rows(root) == [("template", Status.OK, "docs/MANDATE_TEMPLATE.md ready", "")]
    (root / MANDATE_TEMPLATE).write_text("# my own template\n", encoding="utf-8")
    assert rows(root) == [("template", Status.WARN, f"{BROKEN}: {UNUSABLE_HINT}", "")]
    partial = team(tmp_path / "partial", ("architecture-analyst", "backend-senior", "tester"))
    assert rows(partial) == [
        (
            "template",
            Status.WARN,
            "docs/MANDATE_TEMPLATE.md missing and the Forge team is incomplete "
            "(missing docs-updater.md)",
            "cuanta init",
        )
    ]
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert rows(fresh) == []


def test_home_never_sends_a_missing_template_to_a_full_init(tmp_path: Path) -> None:
    check = template_check(keeper(team(tmp_path)))(DETECTION)
    ok = result("python", Status.OK, msg("doctor.python.ok", version="3.12.9"))
    report = DoctorReport(DETECTION, (ok, *check))
    step = next_step(report)
    assert step is not None and step.name == "template"
    fix = classify(step.fix)
    assert (fix.kind, fix.action) == (FixKind.RUN, FixAction.TEMPLATE)
    snapshot = HomeSnapshot(report, (), (0,) * 7, step, VERSION)
    assert snapshot.initialized and snapshot.template_missing
    assert not snapshot.template_unusable
    ready = HomeSnapshot(DoctorReport(DETECTION, (ok,)), (), (0,) * 7, None, VERSION)
    assert not ready.template_missing and not ready.template_unusable


def test_home_names_a_template_that_cannot_be_used_after_anything_it_can_fix(
    tmp_path: Path,
) -> None:
    root = spoiled(team(tmp_path), "# my own template\n")
    ok = result("python", Status.OK, msg("doctor.python.ok", version="3.12.9"))
    unusable = template_check(keeper(root))(DETECTION)
    report = DoctorReport(DETECTION, (ok, *unusable))
    step = next_step(report)
    assert step is not None and step.name == "template" and step.fix == ""
    assert step.detail == f"{BROKEN}: {UNUSABLE_HINT}"
    snapshot = HomeSnapshot(report, (), (0,) * 7, step, VERSION)
    assert snapshot.template_unusable and not snapshot.template_missing
    codex = result(
        "engine codex",
        Status.WARN,
        msg("doctor.engine.missing"),
        "npm install -g @openai/codex",
    )
    first = next_step(DoctorReport(DETECTION, (ok, *unusable, codex)))
    assert first is not None and first.name == "engine codex"
