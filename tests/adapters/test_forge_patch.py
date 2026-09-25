from __future__ import annotations

import json
from pathlib import Path

from cuanta.adapters.forge.assets import vendored_version

FORGE = Path(__file__).parents[2] / "src" / "cuanta" / "assets" / "forge"
SKILL = FORGE / "skills" / "agent-system-init"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_plugin_and_version_are_bumped() -> None:
    plugin = json.loads(_read(FORGE / ".claude-plugin" / "plugin.json"))
    assert plugin["version"] == "0.4.0"
    version = vendored_version()
    assert version["version"] == "0.4.0"
    assert version["base"] == "0.3.1"
    assert "## [0.4.0]" in _read(FORGE / "CHANGELOG.md")


def test_phase_zero_adopts_cuanta_handoff_conditionally() -> None:
    text = _read(SKILL / "SKILL.md")
    assert '"producer": "cuanta"' in text
    assert "resuming from Phase 1 (handed off by cuanta)" in text
    assert "Without that\n   `producer` field this paragraph does not apply" in text


def test_tester_fill_uses_gateway_only_with_cuanta_dir() -> None:
    text = _read(SKILL / "SKILL.md")
    assert "When `.cuanta/` exists at the repo root" in text
    assert "`cuanta test --json`" in text
    assert "the gateway output is already\n   bounded; never pipe it." in text


def test_forge_tier_rule_matches_cuanta() -> None:
    from cuanta.domain.detection import NO_TYPECHECK, VerifySignals, VerifyTier, verify_tier

    text = _read(SKILL / "SKILL.md")
    assert "a **test runner**, with or without a typecheck or build step" in text
    assert f"`pytest, {NO_TYPECHECK}`" in text
    assert "and a typecheck or build step (`pytest` + `mypy`" not in text
    assert "When the evidence\n     says `no typecheck`" in text
    decision = verify_tier(VerifySignals(test_runners=("pytest",)))
    assert (decision.tier, decision.evidence) == (VerifyTier.STRONG, f"pytest, {NO_TYPECHECK}")
    guide = _read(SKILL / "templates" / "AGENTS_GUIDE.template.md")
    assert "recorded as `no typecheck`" in guide


def test_mandate_and_run_log_templates() -> None:
    mandate = _read(SKILL / "templates" / "MANDATE_TEMPLATE.template.md")
    assert "cuanta cat <capsule> --level L2" in mandate
    assert "(`| tail -n 40`, `-q`)" in mandate
    run_log = _read(SKILL / "templates" / "RUN_LOG.template.md")
    assert "- Consumption:      run $CUANTA_RUN_ID → cuanta spectrum $CUANTA_RUN_ID" in run_log
    assert "only when `CUANTA_RUN_ID` is set" in run_log
