from __future__ import annotations

import hashlib
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cross_engine import CrossEnginePipeline, DocsBeside, RoleTuning
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.routing import RoutePlan
from cuanta.domain.agents import parse_agent
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest, RunResult
from cuanta.domain.gitignore import IgnoreRules
from cuanta.domain.messages import msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.routing import ROLES, Role, RoleRoute, RoutingPolicy
from cuanta.domain.team import runs_per_role
from cuanta.ports.sandbox import FileStat, SandboxCopy
from tests.unit.test_cross_engine import REQUEST
from tests.unit.test_steering import Recorder


def test_claude_and_codex_teams_run_one_launch_per_role() -> None:
    assert runs_per_role("claude", True) and runs_per_role("codex", True)
    assert not runs_per_role("opencode", True) and not runs_per_role("claude", False)


def hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


class CopySandbox:
    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.removed = False

    def create(self, origin: Path) -> SandboxCopy:
        root = self.scratch / "copy"
        shutil.copytree(origin, root)
        base = {path: FileStat(digest, 0, 0) for path, digest in hashes(root).items()}
        return SandboxCopy(origin, root, root, base, IgnoreRules())

    def manifest(self, copy: SandboxCopy) -> dict[str, str]:
        return hashes(copy.root)

    def read(self, copy: SandboxCopy, relative: str) -> bytes | None:
        target = copy.root / relative
        return target.read_bytes() if target.is_file() else None

    def remove(self, copy: SandboxCopy) -> bool:
        shutil.rmtree(copy.root)
        self.removed = True
        return True


class RoleEngine:
    def __init__(self, overlap: threading.Barrier, cwds: list[str]) -> None:
        self._overlap = overlap
        self._cwds = cwds

    @property
    def name(self) -> str:
        return "claude"

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "x"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return ["claude"]

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        role = next(
            (item.value for item in ROLES if f"You are the {item.value} of" in request.prompt), ""
        )
        self._cwds.append(f"{role}:{request.cwd}")
        root = Path(request.cwd)
        if role in {"tester", "docs"}:
            self._overlap.wait(timeout=5)
        if role == "docs":
            (root / "docs").mkdir(exist_ok=True)
            (root / "docs" / "cart.md").write_text("# Cart\n", encoding="utf-8")
            (root / "src" / "sneaky.py").write_text("x = 1\n", encoding="utf-8")
        if role == "tester":
            (root / "tests" / "test_cart.py").write_text("def test(): pass\n", encoding="utf-8")
        result = RunResult(True, "success", 0.1, 1, "s", (), '{"status": "done"}')
        on_event(result)
        return EngineOutcome(0, result, 0)


def team() -> RoutePlan:
    roles = (Role.ANALYST, Role.SENIOR, Role.TESTER, Role.DOCS)
    routes = tuple(
        RoleRoute(
            role,
            Tier.STANDARD,
            Tier.STANDARD if role in roles else None,
            ModelEntry("claude", f"model-{role.value}", "m", "p") if role in roles else None,
            msg("route.policy", tier="standard"),
        )
        for role in ROLES
    )
    return RoutePlan(RoutingPolicy(), None, None, (), routes, "heuristic")


def test_docs_runs_beside_the_tester_in_a_copy_and_brings_back_only_documentation(
    tmp_path: Path,
) -> None:
    origin = tmp_path / "project"
    (origin / "src").mkdir(parents=True)
    (origin / "tests").mkdir()
    (origin / "src" / "cart.py").write_text("def total(): return 1\n", encoding="utf-8")
    ledger = MemoryLedger()
    counter = iter(range(100))
    overlap = threading.Barrier(2)
    cwds: list[str] = []

    def launcher(name: str) -> EngineLauncher:
        return EngineLauncher(
            RoleEngine(overlap, cwds),
            ledger,
            FixedClock(),
            lambda: f"RUN{next(counter)}",
            lambda size: b"\x01" * size,
            "shop",
            4318,
            None,
        )

    sandbox = CopySandbox(tmp_path)
    senior = parse_agent("---\nname: python-senior\ndescription: d\n---\nYou are the SENIOR.\n")
    assert senior is not None

    def write(relative: str, data: bytes) -> None:
        target = origin / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    pipeline = CrossEnginePipeline(
        launcher,
        lambda: (senior,),
        FileCapsuleStore(tmp_path / "capsules"),
        str(origin),
        0.0,
        snapshot=lambda: hashes(origin),
        tuning=RoleTuning(beside=DocsBeside(sandbox, origin, write)),
    )
    recorder = Recorder()
    report = pipeline.run(REQUEST, team(), recorder)
    assert [step.role for step in report.steps] == [
        Role.ANALYST,
        Role.SENIOR,
        Role.TESTER,
        Role.DOCS,
    ]
    assert (origin / "docs" / "cart.md").read_text(encoding="utf-8") == "# Cart\n"
    assert not (origin / "src" / "sneaky.py").exists()
    assert (origin / "tests" / "test_cart.py").is_file()
    docs = next(step for step in report.steps if step.role is Role.DOCS)
    assert docs.changed_files == ("docs/cart.md",)
    assert sandbox.removed
    assert any(item.startswith("docs:") and "copy" in item for item in cwds)
    assert any(item.startswith("tester:") and str(origin) in item for item in cwds)
    assert any("src/sneaky.py" in text for text in recorder.texts())
