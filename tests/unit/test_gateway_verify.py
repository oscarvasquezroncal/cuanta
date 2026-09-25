from __future__ import annotations

from cuanta.domain.forge_verify import ForgeTree, check_gateway, gateway_gaps
from cuanta.domain.progress import Status

GOOD = (
    "Run the suite with `cuanta test --json`; the output is bounded, never pipe it.\n"
    "Read failure detail with `cuanta cat <capsule> --level L2`.\n"
)
STANDALONE = "Run `uv run pytest -q 2>&1 | tail -n 40` and read the last lines.\n"


def tree(gateway: bool, texts: dict[str, str]) -> ForgeTree:
    return ForgeTree(
        texts={},
        agent_names=(),
        docs_names=(),
        per_directory={},
        graph_wired=False,
        phases_completed=(),
        new_files=(),
        gateway=gateway,
        gateway_texts=texts,
    )


def test_gateway_gaps_name_what_is_missing() -> None:
    assert gateway_gaps(GOOD) == ()
    assert gateway_gaps(STANDALONE) == ("run", "read")
    assert gateway_gaps(None) == ("missing",)
    piped = GOOD + "then `cuanta test --json | tail -n 40`\n"
    assert gateway_gaps(piped) == ("piped",)


def test_check_fails_only_when_cuanta_is_present() -> None:
    texts = {"docs/MANDATE_TEMPLATE.md": STANDALONE, ".claude/agents/tester.md": GOOD}
    assert check_gateway(tree(False, texts)) == []
    findings = check_gateway(tree(True, texts))
    assert [finding.status for finding in findings] == [Status.FAIL, Status.OK]
    assert "docs/MANDATE_TEMPLATE.md lacks the gateway instructions" in findings[0].text
    missing = check_gateway(tree(True, {"docs/MANDATE_TEMPLATE.md": GOOD}))
    assert missing[1].status is Status.FAIL
    assert "file missing" in missing[1].text
