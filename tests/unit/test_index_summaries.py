from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_index import SqliteIndex
from cuanta.adapters.system.index_inventory import LocalIndexInventory
from cuanta.application.code_index import IndexService
from cuanta.application.index_summaries import (
    MAX_BATCH_FILES,
    MAX_CAP_USD,
    MAX_RESPONSE_CHARS,
    OUTPUT_TOKENS_PER_FILE,
    SNIPPET_CHARS,
    SUMMARY_CHARS,
    IndexSummaries,
    parse_summaries,
)
from cuanta.application.models import CatalogView, ModelService
from cuanta.bootstrap import Container
from cuanta.domain.config import Config
from cuanta.domain.errors import NotAvailable
from cuanta.domain.index_facts import anchor_hash
from cuanta.domain.models import ModelEntry, Tier
from tests.fakes import FakeRunner, FakeStream


@pytest.fixture
def service(tmp_path: Path) -> Iterator[IndexService]:
    index = SqliteIndex(tmp_path / ".cuanta" / "index.db")
    selected = IndexService(index, LocalIndexInventory(tmp_path), lambda: "2026-09-27")
    yield selected
    selected.close()


class Model:
    def __init__(self, estimate: float | None = 0.01) -> None:
        self.price = estimate
        self.estimates: list[tuple[str, int]] = []
        self.launches: list[tuple[str, float]] = []
        self.response: str | None = None

    def estimate(self, prompt: str, output_tokens: int) -> float | None:
        self.estimates.append((prompt, output_tokens))
        return self.price

    def launch(self, prompt: str, cap: float) -> tuple[str, float | None]:
        self.launches.append((prompt, cap))
        paths = json.loads(prompt.split("\n", 1)[1])["files"]
        response = self.response
        if response is None:
            response = json.dumps(dict.fromkeys(paths, "Calculates local totals."))
        return response, 0.004

    def summaries(self, service: IndexService) -> IndexSummaries:
        return IndexSummaries(service, "engine:economy", self.estimate, self.launch)


def test_preview_has_estimate_and_candidates_without_launch(
    service: IndexService, tmp_path: Path
) -> None:
    (tmp_path / "cart.py").write_bytes(b"total = 1\n")
    (tmp_path / "README.md").write_bytes(b"# Instructions\n")
    (tmp_path / ".env.py").write_bytes(b"secret = 123\n")
    (tmp_path / "cart.min.js").write_bytes(b"generated\n")
    model = Model()
    result = model.summaries(service).run()
    assert result.paths == ("cart.py",)
    assert result.model == "engine:economy"
    assert result.estimate_usd == 0.01 and result.batches == 1
    assert 0.01 < result.cap_usd <= MAX_CAP_USD
    assert not result.ran and result.generated == 0 and result.cost_usd is None
    assert model.launches == [] and service.index.rows("notes") == ()
    assert model.estimates[0][1] == OUTPUT_TOKENS_PER_FILE


def test_confirmed_batch_persists_hash_cache_without_human_note_relation(
    service: IndexService, tmp_path: Path
) -> None:
    for name in ("a.py", "b.ts"):
        (tmp_path / name).write_bytes(b"value = 1\n")
    model = Model()
    summaries = model.summaries(service)
    first = summaries.run(confirmed=True)
    assert first.ran and first.generated == 2 and first.cost_usd == 0.004
    assert len(model.launches) == 1
    assert model.launches[0][1] == first.cap_usd
    rows = service.index.rows("notes")
    assert len(rows) == 2
    assert all(row.relation == "summary" and not row.stale for row in rows)
    assert all(row.id.startswith("summary:") for row in rows)
    assert all(row.provenance == "economy-summary:engine:economy" for row in rows)
    assert all(row.target == "anchor:" + anchor_hash("value = 1\n") for row in rows)
    second = summaries.run(confirmed=True)
    assert not second.ran and second.paths == () and second.batches == 0
    assert second.estimate_usd == 0.0
    assert len(model.launches) == 1 and service.index.rows("notes") == rows


@pytest.mark.parametrize("estimate", [None, float("nan"), float("inf"), -1.0, 0.25])
def test_unknown_invalid_or_unaffordable_estimate_refuses_spend(
    service: IndexService, tmp_path: Path, estimate: float | None
) -> None:
    (tmp_path / "cart.py").write_bytes(b"total = 1\n")
    model = Model(estimate)
    result = model.summaries(service).run(confirmed=True)
    assert result.paths == ("cart.py",) and not result.ran
    assert model.launches == [] and service.index.rows("notes") == ()
    assert 0 <= result.cap_usd <= MAX_CAP_USD


@pytest.mark.parametrize(
    "response",
    [
        "invalid",
        "[]",
        "{}",
        '{"other.py":"Unselected file."}',
        '{"cart.py":42}',
        '{"cart.py":"   "}',
        '{"cart.py":"One line.\\nAnother line."}',
        '{"cart.py":"First.","cart.py":"Second."}',
        '{"cart.py":"Hidden\\u0000data."}',
        json.dumps({"cart.py": "x" * (SUMMARY_CHARS + 1)}),
        "x" * (MAX_RESPONSE_CHARS + 1),
    ],
    ids=(
        "invalid-json",
        "list",
        "missing-path",
        "unknown-path",
        "number",
        "empty",
        "multiline",
        "duplicate-path",
        "control-character",
        "long-summary",
        "oversized-response",
    ),
)
def test_invalid_batch_never_persists_any_summary(
    service: IndexService, tmp_path: Path, response: str
) -> None:
    (tmp_path / "cart.py").write_bytes(b"total = 1\n")
    model = Model()
    model.response = response
    result = model.summaries(service).run(confirmed=True)
    assert result.ran and result.generated == 0 and result.cost_usd == 0.004
    assert len(model.launches) == 1 and service.index.rows("notes") == ()


def test_fresh_notes_and_findings_take_priority_and_stale_notes_do_not(
    service: IndexService, tmp_path: Path
) -> None:
    for name in ("noted.py", "finding.py", "stale.py"):
        (tmp_path / name).write_bytes(b"value = 1\n")
    service.update()
    note = service.note("noted.py", "Owns the total calculation.")
    finding = replace(service.note("finding.py", "Totals are rounded here."), relation="finding")
    service.index.put_rows("notes", (finding,))
    service.note("stale.py", "Previously owned the calculation.")
    (tmp_path / "stale.py").write_bytes(b"different = 2\n")
    model = Model()
    result = model.summaries(service).run(confirmed=True)
    assert result.paths == ("stale.py",) and result.generated == 1
    assert note in service.index.rows("notes", "noted.py")
    assert finding in service.index.rows("notes", "finding.py")
    assert any(row.stale for row in service.index.rows("notes", "stale.py"))


def test_changed_source_invalidates_cached_summary_and_gets_new_identity(
    service: IndexService, tmp_path: Path
) -> None:
    path = tmp_path / "cart.py"
    path.write_bytes(b"total = 1\n")
    model = Model()
    summaries = model.summaries(service)
    summaries.run(confirmed=True)
    (original,) = service.index.rows("notes")
    path.write_bytes(b"total = 2\n")
    result = summaries.run(confirmed=True)
    rows = service.index.rows("notes")
    assert result.generated == 1 and len(model.launches) == 2
    assert len(rows) == 2
    assert any(row.id == original.id and row.stale for row in rows)
    assert any(row.id != original.id and not row.stale for row in rows)


@pytest.mark.parametrize("update_index", [False, True])
def test_source_changing_during_generation_is_not_saved(
    service: IndexService, tmp_path: Path, update_index: bool
) -> None:
    path = tmp_path / "cart.py"
    path.write_bytes(b"total = 1\n")
    model = Model()

    def launch(prompt: str, cap: float) -> tuple[str, float | None]:
        response = model.launch(prompt, cap)
        path.write_bytes(b"total = 2\n")
        if update_index:
            service.update()
        return response

    result = IndexSummaries(service, "engine:economy", model.estimate, launch).run(True)
    assert result.ran and result.generated == 0
    assert service.index.rows("notes") == ()


def test_batch_bounds_snippets_and_redacts_inline_secrets(
    service: IndexService, tmp_path: Path
) -> None:
    secret = "sk-" + "x" * 24
    for number in range(MAX_BATCH_FILES + 1):
        (tmp_path / f"module_{number:02}.py").write_text(
            f"key = '{secret}'\n" + "x" * (SNIPPET_CHARS + 100), encoding="utf-8"
        )
    model = Model()
    result = model.summaries(service).run(confirmed=True, limit=100)
    assert result.generated == len(result.paths) == MAX_BATCH_FILES
    assert len(model.launches) == 1
    prompt = model.launches[0][0]
    assert secret not in prompt and "[redacted]" in prompt
    files = json.loads(prompt.split("\n", 1)[1])["files"]
    assert all(len(item["source"]) <= SNIPPET_CHARS for item in files.values())
    assert parse_summaries('{"cart.py":" sk-abcdefghijklmnop "}', ("cart.py",)) == {
        "cart.py": "[redacted]"
    }


def test_zero_limit_returns_without_estimate_or_launch(
    service: IndexService, tmp_path: Path
) -> None:
    (tmp_path / "cart.py").write_bytes(b"total = 1\n")
    model = Model()
    result = model.summaries(service).run(confirmed=True, limit=0)
    assert result.paths == () and not result.ran
    assert model.estimates == model.launches == []


def _catalog(monkeypatch: pytest.MonkeyPatch, entries: tuple[ModelEntry, ...]) -> None:
    def view(_: ModelService) -> CatalogView:
        return CatalogView(entries, "2026-09-27", 1, "2026-09-27", ("claude", "codex"), True)

    monkeypatch.setattr(ModelService, "view", view)


def test_composed_summaries_choose_capped_claude_without_tools_when_codex_is_default(
    service: IndexService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "cart.py").write_bytes(b"total = 1\n")
    _catalog(
        monkeypatch,
        (
            ModelEntry("codex", "luna", "Luna", "openai", tier=Tier.ECONOMY),
            ModelEntry(
                "claude",
                "haiku",
                "Haiku",
                "anthropic",
                input_price=0.25,
                output_price=1.25,
                resolved="claude-haiku-test",
                tier=Tier.ECONOMY,
            ),
        ),
    )
    monkeypatch.delenv("CUANTA_CLAUDE_BIN", raising=False)
    runner = FakeRunner(
        binaries={"claude": "/bin/claude", "codex": "/bin/codex"},
        streams={
            "claude": FakeStream(
                [
                    json.dumps(
                        {
                            "type": "result",
                            "subtype": "success",
                            "is_error": False,
                            "total_cost_usd": 0.004,
                            "result": json.dumps({"cart.py": "Calculates local totals."}),
                        }
                    )
                ]
            )
        },
    )
    container = Container(tmp_path, Config(engine="codex"), runner=runner, home=tmp_path / "home")
    try:
        result = container.index_summaries(service, confirmed=True)
        assert result.model == "claude:haiku" and result.ran and result.generated == 1
        (command,) = runner.calls
        assert command[0] == "claude"
        assert command[command.index("--model") + 1] == "claude-haiku-test"
        assert command[command.index("--tools") + 1] == ""
        assert "--allowedTools" not in command
        assert command[command.index("--max-turns") + 1] == "1"
        assert float(command[command.index("--max-budget-usd") + 1]) == round(result.cap_usd, 2)
        assert 0 < result.cap_usd <= MAX_CAP_USD
        assert "--no-session-persistence" in command
    finally:
        container.close()


def test_composed_summaries_refuse_codex_only_economy_catalog_without_launch(
    service: IndexService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "cart.py").write_bytes(b"total = 1\n")
    _catalog(
        monkeypatch,
        (ModelEntry("codex", "luna", "Luna", "openai", tier=Tier.ECONOMY),),
    )
    runner = FakeRunner(binaries={"codex": "/bin/codex"})
    container = Container(tmp_path, Config(engine="codex"), runner=runner, home=tmp_path / "home")
    try:
        with pytest.raises(NotAvailable, match="no economy-tier model"):
            container.index_summaries(service, confirmed=True)
        assert runner.calls == []
        assert service.index.rows("notes") == ()
        assert not (tmp_path / ".cuanta" / "ledger.db").exists()
    finally:
        container.close()
