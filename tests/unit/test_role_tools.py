from __future__ import annotations

from pathlib import Path

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.application.mandate_flow import MandateOptions
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import english
from cuanta.domain.role_tools import SESSION_TOOLS, cache_message, cache_use, role_tools
from cuanta.domain.routing import Role
from cuanta.tui.i18n import Catalog
from tests.fakes import FakeRunner
from tests.unit.test_engine_profiles import REQUEST
from tests.unit.test_engine_profiles import flow as profile_flow


def test_each_role_sees_only_the_tools_it_needs() -> None:
    assert role_tools(Role.ANALYST) == ("Read", "Grep", "Glob")
    assert role_tools(Role.SENIOR) == ("Read", "Grep", "Glob", "Edit", "Write", "Bash")
    assert "Bash" not in (role_tools(Role.DOCS) or ())
    assert role_tools(Role.ORCHESTRATOR) is None


def test_a_single_writing_session_runs_with_the_session_tools(tmp_path: Path) -> None:
    engine = ClaudeCodeEngine(FakeRunner())
    prepared = profile_flow(tmp_path, engine).prepare(
        REQUEST, 0, MandateOptions(profile="fast", model="claude-sonnet-5-5")
    )
    assert prepared.spec.tools == SESSION_TOOLS


def test_cache_use_is_measured_per_role_with_its_first_request() -> None:
    events = (
        LedgerEvent(kind="api_request", agent="analyst", input_tokens=10, cache_write_tokens=8_000),
        LedgerEvent(kind="api_request", agent="analyst", input_tokens=10, cache_read_tokens=8_000),
        LedgerEvent(kind="api_request", agent="analyst", input_tokens=500, cache_write_tokens=900),
        LedgerEvent(kind="api_request", input_tokens=20, cache_read_tokens=3_000),
    )
    found = cache_use(events)
    analyst = found["analyst"]
    assert (analyst.requests, analyst.first_tokens, analyst.misses) == (3, 8_010, 1)
    assert round(analyst.read_share, 2) == round(8_000 / 17_420, 2)
    shown = cache_message("analyst", analyst)
    assert english(shown) == (
        "cache analyst: 3 requests, 46% read from cache, 1 misses, first request 8,010 tokens"
    )
    assert "caché" in Catalog("es").message(shown)
