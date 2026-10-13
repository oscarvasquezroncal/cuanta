from __future__ import annotations

from pathlib import Path

from tests.cli.test_engine_guarantees import forge
from tests.cli.test_mandate_from_cli import dry, launches, listed, refusal, run_cli, written
from tests.fakes import FakeRunner

CONFLICT = (
    'Stopped before launch: CONSTRAINTS says "read-only", which would turn this feature mandate '
    "into a run that writes nothing · "
    "use TYPE: investigation for a read-only run, or remove the phrase"
)
FROZEN = 'Change plan: read-only, because the request says "No cambies nada"'


def typed(constraints: str) -> str:
    return (
        "TYPE: feature\n"
        "WHAT: Add a cart badge\n"
        'WHY / EVIDENCE: The last run was told "read-only"; no changes, it ran read-only.\n'
        "WHERE: src/cart.py\n"
        f"CONSTRAINTS: {constraints}\n"
        "OUT OF SCOPE: billing\n"
    )


def test_a_typed_writing_mandate_with_an_unscoped_read_only_constraint_stops_before_launch(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    path = written(tmp_path, typed("read-only"))
    assert refusal(tmp_path, "mandate", "-f", str(path)) == CONFLICT
    result = run_cli(tmp_path, "run", str(path), "--lang", "en")
    assert result.exit_code == 1, result.stdout
    output = " ".join((result.stdout + result.stderr).split())
    assert 'CONSTRAINTS says "read-only"' in output
    assert "use TYPE: investigation for a read-only run, or remove the phrase" in output
    assert launches(fake_runner) == []
    assert not fake_runner.stdins


def test_the_stop_reads_in_the_session_language(tmp_path: Path, fake_runner: FakeRunner) -> None:
    forge(tmp_path)
    path = written(tmp_path, typed("solo lectura"))
    result = run_cli(tmp_path, "run", str(path), "--dry-run", "--lang", "es")
    assert result.exit_code == 1, result.stdout
    output = " ".join((result.stdout + result.stderr).split())
    assert "Detenido antes de lanzar: CONSTRAINTS dice «solo lectura»" in output
    assert "usa TYPE: investigation para una corrida de solo lectura, o quita la frase" in output
    assert not fake_runner.stdins


def test_a_scoped_phrase_and_read_only_words_in_the_evidence_keep_the_run_writable(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    data = dry(tmp_path, "run", str(written(tmp_path, typed("read-only outside src/cart.py"))))
    assert not any("read-only, because" in line for line in listed(data["pack_notes"]))
    quoted = dry(tmp_path, "run", str(written(tmp_path, typed("keep `read-only` as the label"))))
    assert not any("read-only, because" in line for line in listed(quoted["pack_notes"]))


def test_a_file_without_a_type_still_freezes_on_its_text_but_not_on_quotes(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    body = "Revisa el total del carrito\n\nNo cambies nada, solo dime por qué suma dos veces.\n"
    data = dry(tmp_path, "run", str(written(tmp_path, body)))
    assert FROZEN in listed(data["pack_notes"])
    quoted = "Revisa el total del carrito\n\nEl aviso dice «No cambies nada» y confunde.\n"
    shown = dry(tmp_path, "run", str(written(tmp_path, quoted, "quoted.md")))
    assert not any("read-only" in line for line in listed(shown["pack_notes"]))
