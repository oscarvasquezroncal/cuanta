from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from cuanta.adapters.bench.answer_check import AnswerCheck
from cuanta.adapters.bench.sandbox import LocalBenchSandbox
from cuanta.adapters.bench.tasks import load_tasks, parse_task
from cuanta.application.mandate_flow import validate
from cuanta.domain.answer_check import AnswerSpec
from cuanta.domain.bench import BenchTask, select
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner
from tests.support import FIXTURES

TASKS = Path(__file__).parents[2] / "bench" / "tasks"
SPEC = AnswerSpec(("total", "round"), (r"src/pricing\.py:[1-9][0-9]*",))


def _root(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pricing.py").write_text("total = 1\nround(total, 2)\n", encoding="utf-8")
    return tmp_path


def test_answer_check_verifies_casefold_keywords_and_real_line(tmp_path: Path) -> None:
    root = _root(tmp_path)
    assert AnswerCheck().check(SPEC, "TOTAL uses ROUND: src/pricing.py:2", str(root))[0]


@pytest.mark.parametrize(
    "report",
    [None, "", "   ", "total src/pricing.py:1", "total round", "total round src/pricing.py:0"],
)
def test_answer_check_rejects_absent_keywords_or_citations(
    tmp_path: Path, report: str | None
) -> None:
    assert not AnswerCheck().check(SPEC, report, str(_root(tmp_path)))[0]


@pytest.mark.parametrize(
    "report",
    [
        "total round src/pricing.py:3",
        "total round src/pricing.py:999999",
        "total round src/pricing.py:1 nonexistent.py:1",
        "total round src/pricing.py:1 ../outside.py:1",
        "total round src/pricing.py:1 src/../../outside.py:1",
    ],
)
def test_answer_check_rejects_fabricated_out_of_bounds_or_escape_refs(
    tmp_path: Path, report: str
) -> None:
    root = _root(tmp_path)
    (tmp_path.parent / "outside.py").write_text("total = 1\n", encoding="utf-8")
    assert not AnswerCheck().check(SPEC, report, str(root))[0]


@pytest.mark.parametrize(
    "spec", [AnswerSpec(), AnswerSpec(("total",), ()), AnswerSpec(("",), ("src/pricing.py:1",))]
)
def test_answer_check_rejects_empty_specs(tmp_path: Path, spec: AnswerSpec) -> None:
    assert not AnswerCheck().check(spec, "total round src/pricing.py:1", str(_root(tmp_path)))[0]


@pytest.mark.parametrize("pattern", ["[", "", "total"])
def test_answer_check_rejects_malformed_or_non_citation_patterns(
    tmp_path: Path, pattern: str
) -> None:
    spec = AnswerSpec(("total",), (pattern,))
    assert not AnswerCheck().check(spec, "total src/pricing.py:1", str(_root(tmp_path)))[0]


def test_answer_check_requires_every_citation_pattern(tmp_path: Path) -> None:
    spec = AnswerSpec(("total",), (r"src/pricing\.py:1", r"src/pricing\.py:2"))
    root = _root(tmp_path)
    assert not AnswerCheck().check(spec, "total src/pricing.py:1", str(root))[0]
    assert AnswerCheck().check(spec, "total src/pricing.py:1 and src/pricing.py:2", str(root))[0]


def test_answer_check_cannot_match_a_different_file_name_or_partial_line(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "src" / "other-pricing.py").write_text("total = 1\n", encoding="utf-8")
    spec = AnswerSpec(("total",), (r"pricing\.py:1",))
    assert not AnswerCheck().check(spec, "total src/other-pricing.py:1", str(root))[0]
    spec = AnswerSpec(("total",), (r"src/pricing\.py:1",))
    (root / "src" / "pricing.py").write_text("total = 1\n" * 10, encoding="utf-8")
    assert not AnswerCheck().check(spec, "total src/pricing.py:10", str(root))[0]


def test_answer_check_missing_root_and_unreadable_source_fail_closed(tmp_path: Path) -> None:
    assert not AnswerCheck().check(SPEC, "total round src/pricing.py:1", str(tmp_path / "none"))[0]
    root = _root(tmp_path)
    (root / "src" / "pricing.py").write_bytes(b"\xff\xfe")
    assert not AnswerCheck().check(SPEC, "total round src/pricing.py:1", str(root))[0]


def test_answer_check_rejects_resolved_source_outside_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    outside = tmp_path.parent / "outside.py"
    outside.write_text("total = 1\n", encoding="utf-8")
    original = Path.resolve

    def resolve(path: Path, strict: bool = False) -> Path:
        return outside if path == root / "src" / "pricing.py" else original(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert not AnswerCheck().check(SPEC, "total round src/pricing.py:1", str(root))[0]


def _document(answer: object) -> str:
    prefix = 'name = "x"\nfixture = "python_strong"\n[request]\ntype = "investigation"\n'
    if not isinstance(answer, dict):
        return "answer = " + json.dumps(answer) + "\n" + prefix
    return (
        prefix
        + "[answer]\n"
        + "\n".join(key + " = " + json.dumps(value) for key, value in answer.items())
    )


def test_task_parser_reads_answer_and_preserves_legacy_task() -> None:
    parsed = parse_task(_document({"keywords": ["total"], "citations": [r"src/pricing\.py:1"]}))
    assert parsed is not None
    assert parsed.answer == AnswerSpec(("total",), (r"src/pricing\.py:1",))
    legacy = parse_task('name = "x"\nfixture = "python_strong"\n[request]\ntype = "bug"')
    assert legacy is not None
    assert legacy.answer is None


def test_drive_relative_citation_is_not_reinterpreted_as_local_source(tmp_path: Path) -> None:
    root = _root(tmp_path)
    accepted, reason = AnswerCheck().check(SPEC, "total round C:src/pricing.py:2", str(root))
    assert not accepted
    assert "sandbox-relative" in reason


@pytest.mark.parametrize("extra", ["/outside.py:1", "//server/share/outside.py:1"])
def test_valid_relative_citation_does_not_hide_external_citation(
    tmp_path: Path, extra: str
) -> None:
    root = _root(tmp_path)
    accepted, reason = AnswerCheck().check(SPEC, "total round src/pricing.py:2 " + extra, str(root))
    assert not accepted
    assert "sandbox-relative" in reason


@pytest.mark.parametrize("end", ["999999", "0", "1"])
def test_citation_range_end_must_be_real_and_ordered(tmp_path: Path, end: str) -> None:
    root = _root(tmp_path)
    accepted, reason = AnswerCheck().check(SPEC, "total round src/pricing.py:2-" + end, str(root))
    assert not accepted
    assert "range" in reason


@pytest.mark.parametrize(
    "answer",
    [
        {},
        "invalid",
        {"keywords": [], "citations": ["src/pricing.py:1"]},
        {"keywords": ["total"], "citations": []},
        {"keywords": "total", "citations": ["src/pricing.py:1"]},
        {"keywords": [1], "citations": ["src/pricing.py:1"]},
        {"keywords": [" "], "citations": ["src/pricing.py:1"]},
        {"keywords": ["total"], "citations": [" "]},
        {"keywords": ["total"], "citations": ["["]},
        {"keywords": ["total"], "citations": ["src/pricing.py:1"], "other": "extra"},
    ],
)
def test_malformed_answer_spec_rejects_the_task(answer: object) -> None:
    assert parse_task(_document(answer)) is None


class NoCommands(FakeRunner):
    def run(
        self,
        args: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Completed:
        raise AssertionError(
            f"Answer-only acceptance must not execute {args} in {cwd}: {env}, {timeout}"
        )


def test_answer_only_sandbox_acceptance_checks_delivered_report_without_commands(
    tmp_path: Path,
) -> None:
    task = BenchTask(
        "x", (), "python_strong", select(load_tasks(TASKS), "mini")[0].request, "", {}, answer=SPEC
    )
    sandbox = LocalBenchSandbox(
        FIXTURES / "repos", TASKS.parent / "kit", NoCommands(), sys.executable
    )
    root = str(_root(tmp_path))
    before = (tmp_path / "src" / "pricing.py").read_bytes()
    assert not sandbox.accept(task, root)[0]
    assert sandbox.accept(task, root, "total round src/pricing.py:2")[0]
    assert (tmp_path / "src" / "pricing.py").read_bytes() == before
    assert not (tmp_path / ".cuanta").exists()


def test_two_investigation_tasks_are_complete_and_do_not_change_old_suite_counts() -> None:
    tasks = load_tasks(TASKS)
    assert len(select(tasks, "mini")) == 5
    assert len(select(tasks, "full")) == 7
    investigations = select(tasks, "investigation")
    assert len(investigations) == 2
    assert {task.fixture for task in investigations} == {"next_landing", "python_strong"}
    for task in investigations:
        assert task.request.type == "investigation"
        assert task.answer is not None
        validate(task.request)


@pytest.mark.parametrize(
    ("name", "answer"),
    [
        (
            "next-cart-investigation",
            "cartStore tracks quantity in src/stores/cart-store.ts:5. "
            "CartDrawer calls createCheckout at src/components/cart-drawer.tsx:9. "
            "cartTotal multiplies price by quantity at src/domain/cart.ts:8; "
            "toFixed rounds the URL total in src/lib/create-checkout.ts:5.",
        ),
        (
            "python-pricing-investigation",
            "cart_total sums the cart and applies percent then round at src/shop/pricing.py:3. "
            "invoice_total applies percent and round before adding shipping and a final round "
            "at src/shop/pricing.py:7 and src/shop/pricing.py:8.",
        ),
    ],
)
def test_real_investigation_task_accepts_its_reference_answer_in_an_owned_copy(
    tmp_path: Path, name: str, answer: str
) -> None:
    task = next(task for task in load_tasks(TASKS) if task.name == name)
    sandbox = LocalBenchSandbox(
        FIXTURES / "repos", TASKS.parent / "kit", NoCommands(), sys.executable, tmp_path
    )
    root = sandbox.prepare(task, False, name)
    try:
        assert not sandbox.accept(task, root, "done")[0]
        passed, reason = sandbox.accept(task, root, answer)
        assert passed, reason
        assert not (Path(root) / ".cuanta").exists()
    finally:
        sandbox.discard(root)
    assert not Path(root).exists()
