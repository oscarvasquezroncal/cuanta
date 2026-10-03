from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from cuanta.domain.evidence_pack import PackCheck, SeniorScope
from cuanta.domain.scout import DocsChoice, DocsReason

SCOUT_KEY = "scout"
DOCS_KEY = "docs"


@dataclass(frozen=True, slots=True)
class ScoutSummary:
    mode: str = ""
    run_id: str = ""
    capsule: str = ""
    source: str = ""
    status: str = ""
    tokens: int = 0
    budget: int = 0
    raw_tokens: int = 0
    facts: int = 0
    snippets: int = 0
    risks: int = 0
    tests: tuple[str, ...] = ()
    edit_set: tuple[str, ...] = ()
    edit_from_plan: bool = False
    invalid: int = 0
    dropped_snippets: int = 0
    dropped_facts: int = 0
    leaked: tuple[str, ...] = ()
    leaks_known: bool = False
    outside_named: tuple[str, ...] = ()
    outside_unnamed: tuple[str, ...] = ()
    senior_checked: bool = False
    docs: str = ""
    docs_reason: str = ""
    docs_field: str = ""
    docs_term: str = ""
    dispatched: bool = True

    @property
    def shown(self) -> bool:
        return bool(self.mode) or bool(self.docs)

    @property
    def over_budget(self) -> bool:
        return self.tokens > self.budget > 0

    @property
    def trimmed(self) -> bool:
        return self.dropped_snippets > 0 or self.dropped_facts > 0


def scout_payload(
    mode: str,
    run_id: str,
    capsule: str,
    check: PackCheck,
    senior: SeniorScope | None,
) -> dict[str, object]:
    pack = check.pack
    return {
        "mode": mode,
        "run_id": run_id,
        "capsule": capsule,
        "source": pack.source.value,
        "status": pack.status.value,
        "tokens": check.tokens,
        "budget": check.budget,
        "raw_tokens": check.raw_tokens,
        "facts": len(pack.facts),
        "snippets": len(pack.snippets),
        "risks": len(pack.risks),
        "tests": list(pack.tests),
        "edit_set": list(pack.edit),
        "edit_from_plan": check.edit_from_plan,
        "invalid": check.invalid,
        "limited": check.limited,
        "dropped_snippets": check.dropped_snippets,
        "dropped_facts": check.dropped_facts,
        "over_budget": check.over_budget,
        "senior": (
            {
                "leaked_reads": list(senior.leaked) if senior.leaked is not None else None,
                "outside_named": list(senior.outside.named),
                "outside_unnamed": list(senior.outside.unnamed),
            }
            if senior is not None
            else None
        ),
    }


def docs_payload(choice: DocsChoice) -> dict[str, object]:
    found: dict[str, object] = {"on": choice.on, "reason": choice.reason.value}
    if choice.term:
        found.update(field=choice.field, term=choice.term)
    return found


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def parse_scout(value: object, docs: object = None) -> ScoutSummary:
    data: Mapping[str, object] = value if isinstance(value, dict) else {}
    senior = data.get("senior")
    checked: Mapping[str, object] = senior if isinstance(senior, dict) else {}
    docs_data: Mapping[str, object] = docs if isinstance(docs, dict) else {}
    on = docs_data.get("on")
    return ScoutSummary(
        mode=_text(data.get("mode")),
        run_id=_text(data.get("run_id")),
        capsule=_text(data.get("capsule")),
        source=_text(data.get("source")),
        status=_text(data.get("status")),
        tokens=_int(data.get("tokens")),
        budget=_int(data.get("budget")),
        raw_tokens=_int(data.get("raw_tokens")),
        facts=_int(data.get("facts")),
        snippets=_int(data.get("snippets")),
        risks=_int(data.get("risks")),
        tests=_strings(data.get("tests")),
        edit_set=_strings(data.get("edit_set")),
        edit_from_plan=data.get("edit_from_plan") is True,
        invalid=_int(data.get("invalid")),
        dropped_snippets=_int(data.get("dropped_snippets")),
        dropped_facts=_int(data.get("dropped_facts")),
        leaked=_strings(checked.get("leaked_reads")),
        leaks_known=isinstance(checked.get("leaked_reads"), list),
        outside_named=_strings(checked.get("outside_named")),
        outside_unnamed=_strings(checked.get("outside_unnamed")),
        senior_checked=bool(checked),
        docs="" if not isinstance(on, bool) else "on" if on else "off",
        docs_reason=_text(docs_data.get("reason")),
        docs_field=_text(docs_data.get("field")),
        docs_term=_text(docs_data.get("term")),
        dispatched=data.get("dispatched") is not False,
    )


def summary_docs(summary: ScoutSummary) -> DocsChoice | None:
    if summary.docs not in {"on", "off"}:
        return None
    try:
        reason = DocsReason(summary.docs_reason)
    except ValueError:
        return None
    return DocsChoice(summary.docs == "on", reason, summary.docs_field, summary.docs_term)
