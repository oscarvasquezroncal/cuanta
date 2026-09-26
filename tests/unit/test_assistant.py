from __future__ import annotations

import json
from collections.abc import Sequence

from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.assistant import (
    PromptAssistant,
    changes,
    parse_improvement,
    suggest,
)
from cuanta.application.instinct import DecisionMaker
from cuanta.domain.assistant import (
    ChipAction,
    Gap,
    content_key,
    do_not_break,
    heuristic_clarity,
    heuristic_gaps,
)
from cuanta.domain.instinct import Choice, Context, Noul, Receipt, Score
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import Message, msg

NOW = "2026-09-24T00:00:00Z"
VAGUE = MandateRequest(type="bug", what="make it work")
CLEAR = MandateRequest(
    type="bug",
    what="fix the cart total so discounts apply before tax",
    why="tests/test_cart.py::test_total fails: expected 10.80, got 11.88",
    where="src/shop/cart.py",
    tests="test_total passes",
)
RULEBOOK = """# Rules

## 6. Do not break

- The layer `ALLOWED` map (`tests/architecture/test_layers.py`).
- No comments / no docstrings.

## 7. Flags
- not this one
"""


class RecordingRemote:
    name = "jev"
    remote = True

    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    def available(self) -> tuple[bool, Message]:
        return True, msg("instinct.heuristic_ready")

    def choose(
        self, question: str, options: Sequence[str], context: Context
    ) -> tuple[Choice, Receipt]:
        self.contexts.append(dict(context))
        return Choice(options[0], 0.9), Receipt(self.name, 1, 0.0001)

    def score(
        self, question: str, low: float, high: float, context: Context
    ) -> tuple[Score, Receipt]:
        self.contexts.append(dict(context))
        return Score(1.5, 0.7), Receipt(self.name, 1, 0.0001)

    def noul(self, question: str, context: Context) -> tuple[Noul, Receipt]:
        self.contexts.append(dict(context))
        return Noul(0.2), Receipt(self.name, 1, 0.0001)


def test_heuristic_gaps_find_what_a_vague_request_lacks() -> None:
    gaps = heuristic_gaps(VAGUE)
    assert gaps[Gap.EVIDENCE] > 0.5 and gaps[Gap.PLACE] > 0.5 and gaps[Gap.EXPECTED] > 0.5
    assert heuristic_clarity(VAGUE) < heuristic_clarity(CLEAR)
    assert all(value < 0.5 for value in heuristic_gaps(CLEAR).values())
    bundled = MandateRequest(type="feature", what="add export; also rename the orders page")
    assert heuristic_gaps(bundled)[Gap.SPLIT] > 0.5
    assert content_key(CLEAR) != content_key(VAGUE)


def test_offline_check_returns_chips_and_is_cached() -> None:
    ledger = MemoryLedger()
    assistant = PromptAssistant(DecisionMaker(HeuristicInstinct(), ledger, lambda: NOW))
    clarity = assistant.check(VAGUE)
    actions = {chip.action for chip in clarity.chips}
    assert {ChipAction.LAST_FAILURE, ChipAction.PICK_FILE, ChipAction.EXAMPLE} <= actions
    assert clarity.level == "unclear"
    asked = len(ledger.decisions())
    assert assistant.check(VAGUE) is clarity
    assert len(ledger.decisions()) == asked
    assert assistant.check(CLEAR).chips == ()


def test_the_preview_is_exactly_what_a_remote_backend_receives() -> None:
    remote = RecordingRemote()
    assistant = PromptAssistant(DecisionMaker(remote, MemoryLedger(), lambda: NOW))
    secret = MandateRequest(type="bug", what="fix login", why="token=sk-abcdef1234567890 fails")
    preview = json.loads(assistant.preview(secret))
    clarity = assistant.check(secret)
    sent = remote.contexts[0]
    assert {key: sent[key] for key in preview} == preview
    assert "sk-abcdef1234567890" not in json.dumps(remote.contexts)
    assert clarity.cost_usd is not None and clarity.cost_usd > 0
    assert len(remote.contexts) == 5


def test_suggestions_use_names_graph_and_rulebook() -> None:
    files = ("src/shop/cart.py", "src/shop/tax.py", "tests/test_cart.py", "README.md")
    found = suggest(CLEAR, files, {"Cart": "src/shop/cart.py"}, {}, RULEBOOK)
    assert found.files[0] == "src/shop/cart.py"
    assert found.tests == ("tests/test_cart.py",)
    assert found.out_of_scope == ("The layer ALLOWED map", "No comments / no docstrings")
    assert found.example is not None and found.example.type == "bug"
    assert do_not_break("no such section") == ()


def test_improvement_parsing_and_diff() -> None:
    reply = 'Sure:\n{"what": "Fix the cart total", "tests": "test_total passes", "extra": 1}'
    proposal = parse_improvement(VAGUE, reply)
    assert proposal is not None
    assert proposal.what == "Fix the cart total"
    diff = {change.field: change.after for change in changes(VAGUE, proposal)}
    assert diff == {"what": "Fix the cart total", "tests": "test_total passes"}
    assert parse_improvement(VAGUE, "no json here") is None
