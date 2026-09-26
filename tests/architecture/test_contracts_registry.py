from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from cuanta.adapters.engines.claude_code import REQUIRED_CHOICES, REQUIRED_FLAGS
from cuanta.adapters.engines.codex import CodexEngine
from cuanta.adapters.engines.opencode import OpenCodeEngine

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs" / "CONTRACTS.md"
CONTRACT_ID = re.compile(r"[A-Z]{2,3}-\d{2}\Z")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
CODE_SPAN = re.compile(r"`([^`]+)`")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
LOCAL_PATH = re.compile(r"(?:[A-Za-z]:[\\/]Users|/Users/|/home/)")
STATUSES = {"verified", "unverified", "broken"}


def _contract_rows() -> list[list[str]]:
    return [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        if line.startswith("| ") and CONTRACT_ID.fullmatch(line.split("|", maxsplit=2)[1].strip())
    ]


def test_contract_rows_use_a_known_status_and_an_iso_date() -> None:
    rows = _contract_rows()
    ids = [row[0] for row in rows]
    assert ids
    assert len(ids) == len(set(ids))
    for row in rows:
        assert len(row) == 6
        assert row[3] in STATUSES
        assert ISO_DATE.fullmatch(row[4])
        assert date.fromisoformat(row[4]).isoformat() == row[4]


def test_every_engine_required_token_has_a_contract_row() -> None:
    spans = CODE_SPAN.findall("\n".join("|".join(row) for row in _contract_rows()))
    documented = {token.strip("[](),;") for span in spans for token in span.split()}
    required = {
        *REQUIRED_FLAGS,
        *REQUIRED_CHOICES,
        *CodexEngine.required_tokens,
        *OpenCodeEngine.required_tokens,
    }
    assert required <= documented


def test_contracts_doc_has_no_local_paths_or_emails() -> None:
    text = REGISTRY.read_text(encoding="utf-8")
    assert LOCAL_PATH.search(text) is None
    assert EMAIL.search(text) is None
