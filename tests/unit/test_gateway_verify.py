from __future__ import annotations

from cuanta.domain.forge_verify import ForgeTree, check_gateway, gateway_gaps, passed
from cuanta.domain.messages import english
from cuanta.domain.progress import Status
from cuanta.tui.i18n import Catalog

GOOD = (
    "Run the suite with `cuanta test --json`; the output is bounded, never pipe it.\n"
    "Read failure detail with `cuanta cat <capsule> --level L2`.\n"
)
STANDALONE = "Run `uv run pytest -q 2>&1 | tail -n 40` and read the last lines.\n"
TESTER = ".claude/agents/tester.md"
TEMPLATE = "docs/MANDATE_TEMPLATE.md"


def tree(
    gateway: bool, texts: dict[str, str], suggested: dict[str, str] | None = None
) -> ForgeTree:
    return ForgeTree(
        texts={},
        agent_names=(),
        docs_names=(),
        per_directory={},
        graph_wired=False,
        phases_completed=(),
        gateway=gateway,
        gateway_texts=texts,
        suggested_texts=suggested or {},
    )


def test_gateway_gaps_name_what_is_missing() -> None:
    assert gateway_gaps(GOOD) == ()
    assert gateway_gaps(STANDALONE) == ("run", "read")
    assert gateway_gaps(None) == ("missing",)
    piped = GOOD + "then `cuanta test --json | tail -n 40`\n"
    assert gateway_gaps(piped) == ("piped",)


def test_gateway_gaps_warn_with_their_fix_and_never_fail_init() -> None:
    texts = {TEMPLATE: STANDALONE, TESTER: GOOD}
    assert check_gateway(tree(False, texts)) == []
    findings = check_gateway(tree(True, texts))
    assert [finding.status for finding in findings] == [Status.WARN, Status.OK]
    assert f"{TEMPLATE} lacks the gateway instructions" in findings[0].text
    fix = findings[0].fix
    assert fix is not None
    assert english(fix) == (
        f"add cuanta test --json, cuanta cat <capsule> --level L2 to the test step in {TEMPLATE}"
    )
    assert "cuanta test --json" in Catalog("es").message(fix)
    assert findings[1].fix is None
    assert passed(findings)
    missing = check_gateway(tree(True, {TEMPLATE: GOOD}))
    assert missing[1].status is Status.WARN
    assert "file missing" in missing[1].text
    assert missing[1].fix is not None
    assert passed(missing)


def test_a_piped_gateway_warns_with_the_pipe_to_remove() -> None:
    piped = GOOD + "then `cuanta test --json | tail -n 40`\n"
    findings = check_gateway(tree(True, {TEMPLATE: GOOD, TESTER: piped}))
    assert [finding.status for finding in findings] == [Status.OK, Status.WARN]
    fix = findings[1].fix
    assert fix is not None
    assert "| tail" in english(fix)
    assert TESTER in english(fix)


def test_the_fix_adopts_a_forge_suggestion_that_has_the_gateway_lines() -> None:
    texts = {TEMPLATE: GOOD, TESTER: STANDALONE}
    findings = check_gateway(tree(True, texts, {TESTER: GOOD}))
    fix = findings[1].fix
    assert findings[1].status is Status.WARN
    assert fix is not None
    assert ".cuanta/forge-suggested/claude/agents/tester.md" in english(fix)
    assert "Use new" in english(fix)
    plain = check_gateway(tree(True, texts, {TESTER: STANDALONE}))[1].fix
    assert plain is not None
    assert "forge-suggested" not in english(plain)
