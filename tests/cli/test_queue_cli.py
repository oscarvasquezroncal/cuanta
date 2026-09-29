from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import typer
from typer.main import get_command
from typer.testing import Result

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.cli.commands.mandate import mandate_command
from cuanta.cli.commands.queue import MANDATE_PARAMS, parse_mandate
from cuanta.domain.ledger import LedgerEvent, Run
from tests.fakes import FakeRunner, FakeStream
from tests.support import invoke

SUCCESS = (
    '{"type":"result","subtype":"success","total_cost_usd":0.001,"is_error":false,'
    '"result":"## SUMMARY\\nok"}'
)
FAILURE = (
    '{"type":"result","subtype":"error_during_execution","total_cost_usd":0.001,"is_error":true}'
)


def ok_stream() -> FakeStream:
    return FakeStream([SUCCESS])


def failed_stream() -> FakeStream:
    return FakeStream([FAILURE], code=1)


def asked(what: str, *extra: str) -> list[str]:
    return [
        "--simple",
        "--type",
        "investigation",
        "--what",
        what,
        "--why",
        "How does it work?",
        "--out-of-scope",
        "No edits",
        *extra,
    ]


def queue(root: Path, *args: str, pretty: bool = False, answer: str | None = None) -> Result:
    return invoke(["queue", *args, "--project", str(root)], pretty=pretty, input_text=answer)


def payload(result: Result) -> dict[str, object]:
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    return data


def items(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [item for item in value if isinstance(item, dict)]


def stored(root: Path) -> list[dict[str, object]]:
    data = json.loads((root / ".cuanta" / "queue.json").read_text(encoding="utf-8"))
    return items(data["items"])


def launches(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[:2] == ("claude", "-p")]


def model_of(call: tuple[str, ...]) -> str:
    return call[call.index("--model") + 1] if "--model" in call else ""


def test_queue_add_stores_the_mandate_options_and_refuses_bad_ones(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    added = queue(tmp_path, "add", *asked("Explain the cart", "--model", "opus"), "--json")
    assert added.exit_code == 0, added.stdout
    data = payload(added)
    entry = data["added"]
    assert isinstance(entry, dict)
    assert (entry["id"], entry["type"], entry["engine"], entry["model"]) == (
        "q1",
        "investigation",
        "claude",
        "opus",
    )
    assert (data["position"], data["queued"]) == (1, 1)
    assert stored(tmp_path)[0]["args"] == asked("Explain the cart", "--model", "opus")
    for bad, message in (
        (["--bogus"], "No such option: --bogus"),
        (["--type", "bug", "--what", "Fix it"], "missing required fields: --why, --out-of-scope"),
        ([*asked("Keep"), "--keep"], "--keep only applies to --sandbox runs"),
        ([*asked("Shape"), "--shape", "round"], "--shape"),
        ([*asked("Evidence"), "--evidence", str(tmp_path / "absent.log")], "evidence file"),
        (["--type", "chore", "--what", "x", "--why", "y", "--out-of-scope", "z"], "unknown type"),
        ([*asked("Scout")[1:], "--shape", "scout"], "--shape scout applies to features"),
        ([*bug("Scout"), "--shape", "scout"], "--shape scout needs a team"),
        ([*bug("Scout")[1:], "--shape", "scout", "--engine", "opencode"], "Claude or GPT team"),
        ([*bug("Scout")[1:], "--shape", "scout", "--route", "off"], "needs routing"),
    ):
        refused = queue(tmp_path, "add", *bad, "--json")
        assert refused.exit_code == 1, bad
        error = payload(refused)["error"]
        assert isinstance(error, dict) and message in str(error["message"]), bad
    assert [item["id"] for item in stored(tmp_path)] == ["q1"]
    assert not launches(fake_runner)


def test_every_mandate_option_is_mapped_for_the_queue(tmp_path: Path) -> None:
    holder = typer.Typer(add_completion=False)
    holder.command("mandate")(mandate_command)
    names = {param.name for param in get_command(holder).params}
    assert names == MANDATE_PARAMS
    evidence = tmp_path / "error.log"
    evidence.write_text("boom", encoding="utf-8")
    args = parse_mandate(
        [
            "--type",
            "feature",
            "--evidence",
            str(evidence),
            "--max-budget-usd",
            "0.5",
            "--cross-budget-usd",
            "2",
            "--role-model",
            "senior=opus",
            "--role-model",
            "scout=haiku",
            "--override-env-model",
            "--max-turns",
            "12",
            "--sandbox",
            "--keep",
        ]
    )
    assert args.type == "feature" and args.evidence == evidence
    assert (args.budget, args.cross_budget, args.max_turns) == (0.5, 2.0, 12)
    assert args.role_models == ("senior=opus", "scout=haiku")
    assert args.keep_env_model is False and args.sandbox and args.keep


def test_queue_list_puts_the_same_engine_and_model_back_to_back(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    for what, extra in (
        ("one", ("--engine", "codex")),
        ("two", ("--model", "opus")),
        ("three", ("--engine", "codex")),
        ("four", ("--model", "Opus")),
        ("five", ()),
        ("six", ("--engine", "codex", "--model", "gpt-6-sol")),
    ):
        assert queue(tmp_path, "add", *asked(what, *extra)).exit_code == 0
    listed = payload(queue(tmp_path, "list", "--json"))
    order = [(item["id"], item["engine"], item["model"]) for item in items(listed["queue"])]
    assert order == [
        ("q1", "codex", None),
        ("q3", "codex", None),
        ("q2", "claude", "opus"),
        ("q4", "claude", "Opus"),
        ("q5", "claude", None),
        ("q6", "codex", "gpt-6-sol"),
    ]
    text = " ".join(queue(tmp_path, "list", "--plain").stdout.split())
    assert text.index("q1") < text.index("q3") < text.index("q2") < text.index("q4")
    assert "warm prefix: unknown" in text


def seed(root: Path, last: str) -> None:
    (root / ".cuanta").mkdir(exist_ok=True)
    ledger = SqliteLedger(root / ".cuanta" / "ledger.db")
    try:
        ledger.add_run(Run("01JWARM", "mandate", "claude", started_at=last, status="ok"))
        ledger.add_events(
            [
                LedgerEvent(
                    run_id="01JWARM",
                    kind="api_request",
                    agent="main",
                    model="claude-opus",
                    ts=last,
                    input_tokens=10,
                    cache_read_tokens=20_000,
                    cache_write_tokens=3_000,
                )
            ]
        )
    finally:
        ledger.close()


def configure_ttl(root: Path) -> None:
    (root / ".cuanta").mkdir(exist_ok=True)
    (root / ".cuanta" / "config.toml").write_text(
        '[cache]\nttl_s = 3480\nauth = "subscription"\n', encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("last", "state", "text"),
    [
        ("2025-12-31T23:50:00Z", "warm", "warm prefix until"),
        ("2025-12-31T22:00:00Z", "cold", "prefix cold since"),
    ],
)
def test_the_warm_window_is_the_last_request_plus_the_saved_ttl(
    tmp_path: Path,
    fake_runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
    last: str,
    state: str,
    text: str,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert queue(tmp_path, "add", *asked("Explain")).exit_code == 0
    seed(tmp_path, last)
    unknown = payload(queue(tmp_path, "list", "--json"))
    assert unknown["warm"] == {"state": "unknown", "until": None}
    configure_ttl(tmp_path)
    until = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp() + 3480
    moment = datetime.fromtimestamp(until, UTC)
    listed = payload(queue(tmp_path, "list", "--json"))
    assert listed["warm"] == {"state": state, "until": moment.isoformat()}
    plain = " ".join(queue(tmp_path, "list", "--plain").stdout.split())
    assert f"{text} {moment.astimezone().strftime('%H:%M')}" in plain


def test_queue_run_needs_yes_or_a_confirmation(tmp_path: Path, fake_runner: FakeRunner) -> None:
    assert queue(tmp_path, "add", *asked("Explain")).exit_code == 0
    preview = queue(tmp_path, "run", "--plain")
    assert preview.exit_code == 0
    assert "nothing run · add --yes to run 1 queued mandates back to back" in preview.stdout
    declined = queue(tmp_path, "run", pretty=True, answer="n\n")
    assert declined.exit_code == 0
    assert "Run 1 queued mandates back to back?" in declined.stdout
    assert "nothing run" in declined.stdout
    assert not launches(fake_runner)
    assert len(stored(tmp_path)) == 1


def test_queue_run_runs_back_to_back_in_lane_order_and_empties_the_queue(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    fake_runner.queued["claude -p"] = [ok_stream(), ok_stream(), ok_stream()]
    for what, model in (("one", "opus"), ("two", "sonnet"), ("three", "opus")):
        assert queue(tmp_path, "add", *asked(what, "--model", model)).exit_code == 0
    ran = queue(tmp_path, "run", "--yes", "--json")
    assert ran.exit_code == 0, ran.stdout + ran.stderr
    data = payload(ran)
    results = items(data["results"])
    assert [item["id"] for item in results] == ["q1", "q3", "q2"]
    assert [item["status"] for item in results] == ["ok", "ok", "ok"]
    assert all(isinstance(item["result"], dict) for item in results)
    assert (data["ok"], data["left"]) == (True, 0)
    models = [model_of(call) for call in launches(fake_runner)]
    assert len(models) == 3 and models[0] == models[1] != models[2]
    assert stored(tmp_path) == []
    assert "warm prefix" in ran.stderr


def test_queue_run_stops_at_the_first_failure_unless_keep_going(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    for what in ("one", "two", "three"):
        assert queue(tmp_path, "add", *asked(what)).exit_code == 0
    fake_runner.queued["claude -p"] = [failed_stream()]
    stopped = queue(tmp_path, "run", "--yes", "--json")
    assert stopped.exit_code == 1
    data = payload(stopped)
    assert [item["status"] for item in items(data["results"])] == ["failed", "not_run", "not_run"]
    assert (data["ok"], data["left"]) == (False, 3)
    assert len(launches(fake_runner)) == 1
    fake_runner.queued["claude -p"] = [failed_stream(), ok_stream(), ok_stream()]
    kept = queue(tmp_path, "run", "--yes", "--keep-going", "--plain")
    assert kept.exit_code == 1
    text = " ".join(kept.stdout.split())
    assert "1 left in the queue" in text
    assert len(launches(fake_runner)) == 4
    assert [item["id"] for item in stored(tmp_path)] == ["q1"]


def test_an_invalid_queued_mandate_fails_without_a_launch(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    fake_runner.queued["claude -p"] = [ok_stream()]
    assert queue(tmp_path, "add", *asked("fine")).exit_code == 0
    path = tmp_path / ".cuanta" / "queue.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"].insert(0, {"id": "q9", "added_at": "2026-01-01T00:00:00Z", "args": ["--nope"]})
    path.write_text(json.dumps(data), encoding="utf-8")
    listed = " ".join(queue(tmp_path, "list", "--plain").stdout.split())
    assert "invalid: not a mandate: No such option: --nope" in listed
    ran = payload(queue(tmp_path, "run", "--yes", "--keep-going", "--json"))
    assert [(item["id"], item["status"]) for item in items(ran["results"])] == [
        ("q9", "failed"),
        ("q1", "ok"),
    ]
    assert len(launches(fake_runner)) == 1
    assert [item["id"] for item in stored(tmp_path)] == ["q9"]


def test_queue_clear_asks_first_and_removes_the_chosen_ids(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    for what in ("one", "two", "three"):
        assert queue(tmp_path, "add", *asked(what)).exit_code == 0
    preview = queue(tmp_path, "clear", "q2", "--json")
    assert payload(preview) == {"removed": [], "selected": ["q2"]}
    unknown = queue(tmp_path, "clear", "q7", "--yes", "--json")
    assert unknown.exit_code == 1
    removed = payload(queue(tmp_path, "clear", "q2", "--yes", "--json"))
    assert removed == {"removed": ["q2"], "left": 2}
    declined = queue(tmp_path, "clear", pretty=True, answer="n\n")
    assert "nothing removed" in declined.stdout
    everything = payload(queue(tmp_path, "clear", "--yes", "--json"))
    assert everything == {"removed": ["q1", "q3"], "left": 0}
    assert queue(tmp_path, "add", *asked("again")).exit_code == 0
    assert [item["id"] for item in stored(tmp_path)] == ["q4"]


def bug(what: str, *extra: str) -> list[str]:
    return [
        "--simple",
        "--type",
        "bug",
        "--what",
        what,
        "--why",
        "It breaks",
        "--out-of-scope",
        "Nothing else",
        *extra,
    ]


def test_each_queued_mandate_keeps_its_own_cap_and_its_isolated_copy(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "temp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    root = tmp_path / "project"
    root.mkdir()
    source = root / "app.py"
    source.write_bytes(b"print('broken')\n")
    fake_runner.queued["claude -p"] = [ok_stream(), ok_stream(), ok_stream()]
    for what, extra in (
        ("one", ("--max-budget-usd", "0.4")),
        ("two", ("--max-budget-usd", "0.7", "--sandbox")),
        ("three", ("--max-budget-usd", "0.2")),
    ):
        assert queue(root, "add", *bug(what, *extra)).exit_code == 0
    ran = queue(root, "run", "--yes", "--json")
    assert ran.exit_code == 0, ran.stdout + ran.stderr
    results = items(payload(ran)["results"])
    assert [item["id"] for item in results] == ["q1", "q3", "q2"]
    assert [item["cap_usd"] for item in results] == [0.4, 0.2, 0.7]
    runs = [
        (call, cwd)
        for call, cwd in zip(fake_runner.calls, fake_runner.cwds, strict=True)
        if call[:2] == ("claude", "-p")
    ]
    assert [call[call.index("--max-budget-usd") + 1] for call, _ in runs] == [
        "0.40",
        "0.20",
        "0.70",
    ]
    copies = [
        cwd is not None and Path(cwd).resolve().is_relative_to(scratch.resolve()) for _, cwd in runs
    ]
    assert copies == [False, False, True]
    isolated = results[2]["result"]
    assert isinstance(isolated, dict) and isinstance(isolated["sandbox"], dict)
    assert source.read_bytes() == b"print('broken')\n"
    assert stored(root) == []
