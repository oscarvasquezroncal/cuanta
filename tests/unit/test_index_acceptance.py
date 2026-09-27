from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.engine_run import LaunchSpec
from cuanta.application.run_reports import RunReports
from cuanta.bootstrap import Container
from cuanta.domain.change_plan import EXECUTION, WRITERS, guarded
from cuanta.domain.config import Config
from cuanta.domain.ledger import Run, Snapshot
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.routing import Role, roles_that_run
from tests.fakes import FakeRunner

FIXTURE = Path(__file__).parents[1] / "fixtures" / "repos" / "next_landing"
CSS = "src/app/globals.css"
GSAP = "src/lib/gsap.ts"
HERO = "src/components/hero-nutfall-3d/view.tsx"


def _project(root: Path) -> Container:
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
    return Container(project=root, config=Config(), runner=FakeRunner(), home=root / "empty-home")


def _report(container: Container) -> None:
    ledger = container.shared_ledger()
    ledger.add_run(
        Run(
            "AUDIT",
            "mandate",
            task_type="investigation",
            status="ok",
            ended_at="2026-09-27T00:00:00Z",
        )
    )
    ledger.add_snapshots(
        tuple(
            Snapshot(
                "AUDIT",
                "end",
                path,
                hashlib.sha256((container.project / path).read_bytes()).hexdigest(),
            )
            for path in (CSS, GSAP)
        )
    )
    reports = RunReports(container.state_workspace())
    reports.save_report(
        "AUDIT",
        f"{CSS}:1190-1225 Reveal stays hidden when initialization fails.\n"
        f"{GSAP}:4-13 Initialization failure returns before fallback opacity is restored.\n",
    )
    reports.save_meta("AUDIT", {"task_type": "investigation", "changed_files": []})


def _gsap_request() -> MandateRequest:
    return MandateRequest(
        "bug",
        "Fix GSAP reveal fallback after failed initialization",
        "Leave landing content visible when initialization throws",
        f"{CSS} {GSAP}",
        tests="npm run typecheck npm run test npm run build",
        out_of_scope="Do not touch hero-nutfall-3d/**",
    )


def test_actual_next_index_finds_cart_checkout_top_five_with_reasons(tmp_path: Path) -> None:
    container = _project(tmp_path)
    reader = container.index_reader()
    try:
        reader.update()
        hits = reader.find("carrito checkout", limit=5)
        assert {
            "src/domain/cart.ts",
            "src/stores/cart-store.ts",
            "src/lib/create-checkout.ts",
            "src/components/cart-drawer.tsx",
        } <= {hit.path for hit in hits}
        assert all(hit.reasons for hit in hits)
        assert "src/lib/format-price.ts" not in {hit.path for hit in hits}
        assert reader.card("src/stores/cart-store.ts").role == "store"
    finally:
        reader.close()
        container.close()


def test_report_anchor_at_line_1200_stales_card_and_pack_without_losing_provenance(
    tmp_path: Path,
) -> None:
    container = _project(tmp_path)
    _report(container)
    reader = container.index_reader()
    try:
        reader.update()
        (fact,) = reader.facts(CSS)
        assert fact.line == 1190 and fact.end_line == 1225
        assert "Reveal stays hidden" in reader.card(CSS).text
        before = container.context_pack(_gsap_request(), role="senior")
        assert "Reveal stays hidden" in before.stable_prefix
        report = (tmp_path / ".cuanta/runs/AUDIT/report.md").read_bytes()
        lines = (tmp_path / CSS).read_text(encoding="utf-8").splitlines()
        assert lines[1199] == "    opacity: 0;"
        lines[1199] = "    opacity: 1;"
        (tmp_path / CSS).write_bytes(("\n".join(lines) + "\n").encode())
        reader.update()
        assert reader.facts(CSS) == ()
        (stale,) = reader.facts(CSS, stale=True)
        assert stale.id == fact.id and stale.target == fact.target
        assert stale.provenance == fact.provenance
        assert "Reveal stays hidden" not in reader.card(CSS).text
        assert (
            "Reveal stays hidden" not in container.context_pack(_gsap_request(), role="senior").text
        )
        assert (tmp_path / ".cuanta/runs/AUDIT/report.md").read_bytes() == report
    finally:
        reader.close()
        container.close()


def test_gsap_fixture_plan_enforcement_and_senior_pack_share_current_report_fact(
    tmp_path: Path,
) -> None:
    container = _project(tmp_path)
    _report(container)
    try:
        request = _gsap_request()
        plan = container.change_plan(request)
        edits = {item.path for item in plan.edit}
        assert {CSS, GSAP} <= edits
        assert HERO not in edits and guarded(HERO, plan)
        assert "src/components/hero-nutfall-3d/**" in plan.guard
        assert Role.SENIOR in roles_that_run(request.type)
        assert {"npm run typecheck", "npm run build", "npm test"} <= set(plan.verify)
        pack = container.context_pack(request, role="senior", plan=plan)
        assert f"{GSAP} | code" in pack.stable_prefix
        assert (
            "Initialization failure returns before fallback opacity is restored"
            in pack.stable_prefix
        )
        assert all(item.path != HERO for item in pack.items)
        assert all(item.path in edits for item in pack.items if item.path)
        engine = ClaudeCodeEngine(container.runner)
        launch = container.launcher(engine, MemoryLedger(), telemetry=False).request(
            LaunchSpec(
                "mandate",
                pack.text,
                str(tmp_path),
                ("Read", "Edit", "Write", "MultiEdit", "Bash", "Agent"),
                change_plan=plan,
            ),
            "FIX",
            "trace",
            None,
        )
        command = engine.command(launch)
        assert launch.strict_guard
        assert not set(EXECUTION).intersection(launch.allowed_tools)
        required = {f"{writer}(src/components/hero-nutfall-3d/**)" for writer in WRITERS}
        assert required <= set(launch.disallowed_tools)
        settings = json.loads(Path(launch.settings_file).read_text(encoding="utf-8"))
        assert required <= set(settings["permissions"]["deny"])
        assert "--disallowedTools" in command
    finally:
        container.close()
