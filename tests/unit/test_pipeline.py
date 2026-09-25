from __future__ import annotations

from cuanta.domain.engine import (
    AssistantText,
    ModelUsage,
    RunResult,
    SessionStarted,
    StepUsage,
    ToolCall,
)
from cuanta.domain.pipeline import CardState, Pipeline, stage_of
from tests.tui.fakes import AGENTS, pipeline_events


def test_stage_of_matches_forge_agent_names() -> None:
    assert [stage_of(name) for name in AGENTS] == [0, 1, 2, 3]
    assert stage_of("typescript-senior") == 1
    assert stage_of("general-purpose") is None


def test_cards_light_up_in_order_and_finish() -> None:
    pipeline = Pipeline()
    states: list[list[CardState]] = []
    for event in pipeline_events():
        pipeline.apply(event)
        states.append([card.state for card in pipeline.cards])
    assert pipeline.order == [0, 1, 2, 3]
    assert [card.state for card in pipeline.cards] == [CardState.DONE] * 4
    active = [row.index(CardState.ACTIVE) for row in states if CardState.ACTIVE in row]
    assert active == sorted(active)
    assert [card.tools for card in pipeline.cards] == [1, 1, 1, 1]
    assert [card.tokens for card in pipeline.cards] == [1100, 2100, 3100, 4100]
    assert pipeline.finished and pipeline.ok


def test_usage_dedupes_split_messages_and_main_attribution() -> None:
    pipeline = Pipeline()
    usage = StepUsage(ModelUsage("m", 10, 5), "", "msg-1")
    pipeline.apply(usage)
    pipeline.apply(usage)
    pipeline.apply(ToolCall("Grep", "g1"))
    assert (pipeline.main_tokens, pipeline.main_tools) == (15, 1)


def test_failed_run_marks_active_card_failed() -> None:
    pipeline = Pipeline()
    pipeline.apply(ToolCall("Agent", "s", {"subagent_type": "python-senior"}))
    pipeline.apply(AssistantText("halting:   budget   reached"))
    pipeline.apply(RunResult(False, "error_max_budget_usd", 5.0, 3, "x"))
    assert pipeline.cards[1].state is CardState.FAILED
    assert "halting: budget reached" in pipeline.feed
    assert pipeline.feed[-1] == "run finished: error_max_budget_usd"


def test_unknown_agents_are_logged_not_carded() -> None:
    pipeline = Pipeline()
    pipeline.apply(ToolCall("Task", "s", {"subagent_type": "general-purpose"}))
    assert all(card.state is CardState.WAITING for card in pipeline.cards)
    assert pipeline.feed == ["handoff to general-purpose"]


def test_feed_is_bounded() -> None:
    pipeline = Pipeline()
    for index in range(260):
        pipeline.apply(AssistantText(f"line {index}"))
    assert len(pipeline.feed) == 200
    assert pipeline.feed[-1] == "line 259"


def test_single_context_attributes_main_work_to_one_analyst_card() -> None:
    pipeline = Pipeline(stages=("analyst",), main_context=True)
    pipeline.apply(SessionStarted("s1", "model"))
    pipeline.apply(ToolCall("Read", "read-1"))
    pipeline.apply(StepUsage(ModelUsage("model", 10, 5), "", "m1"))
    pipeline.apply(RunResult(True, "success", 0.1, 1, "s1"))
    assert len(pipeline.cards) == 1
    assert pipeline.order == [0]
    assert pipeline.cards[0].agent == "main"
    assert pipeline.cards[0].state is CardState.DONE
    assert (pipeline.cards[0].tokens, pipeline.cards[0].tools) == (15, 1)
    assert (pipeline.main_tokens, pipeline.main_tools) == (15, 1)


def test_delegated_investigation_has_only_its_analyst_card() -> None:
    pipeline = Pipeline(stages=("analyst",))
    for event in pipeline_events((AGENTS[0],)):
        pipeline.apply(event)
    assert [card.stage for card in pipeline.cards] == ["analyst"]
    assert pipeline.cards[0].agent == AGENTS[0]
    assert pipeline.cards[0].state is CardState.DONE
    assert pipeline.order == [0]
