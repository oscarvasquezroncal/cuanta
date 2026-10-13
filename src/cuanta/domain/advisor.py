from __future__ import annotations

from collections.abc import Sequence

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import Message, msg
from cuanta.domain.pricing import PriceTable, dollars, event_cost
from cuanta.domain.spectrum import USAGE_KINDS
from cuanta.domain.versions import at_least

ADVISOR_MODES = ("auto", "opus", "fable", "off")
ADVISOR_MIN = "2.1.260"
SMALL_MAIN_MIN = "2.1.293"
FAMILIES = ("fable", "opus", "sonnet", "haiku")


def family_of(model: str) -> str:
    lowered = model.lower()
    return next((family for family in FAMILIES if family in lowered), "")


def valid_pair(main: str, advisor: str, version: str) -> bool:
    if main == "sonnet":
        return advisor in {"opus", "fable"}
    if main == "haiku":
        return advisor in {"sonnet", "opus", "fable"} and at_least(version, SMALL_MAIN_MIN, False)
    return False


def advisor_for(mode: str, model: str, version: str) -> str:
    if mode == "off" or not at_least(version, ADVISOR_MIN, False):
        return ""
    main = family_of(model)
    wanted = mode if mode in {"opus", "fable"} else "opus" if main == "sonnet" else ""
    return wanted if wanted and valid_pair(main, wanted, version) else ""


def advisor_spend(
    events: Sequence[LedgerEvent], advisor: str, table: PriceTable
) -> tuple[int, float]:
    requests = 0
    total = 0.0
    for event in events:
        if event.kind not in USAGE_KINDS or family_of(event.model) != advisor:
            continue
        requests += 1
        if event.cost_usd is not None:
            total += event.cost_usd
            continue
        price = table.lookup(event.model)
        cost = event_cost(event, price) if price is not None else None
        total += cost or 0.0
    return requests, round(total, 6)


def advisor_message(advisor: str, requests: int, cost: float) -> Message:
    return msg("advisor.spend", advisor=advisor, requests=requests, cost=dollars(cost))
