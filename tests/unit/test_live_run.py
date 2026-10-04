from cuanta.domain.engine import ModelUsage, StepUsage, ToolCall
from cuanta.domain.live_run import LiveRun
from cuanta.domain.messages import parse_english


def test_streamed_tokens_are_deduplicated_and_updates_are_bounded() -> None:
    live = LiveRun(10.0)
    usage = StepUsage(ModelUsage("model", input_tokens=12, output_tokens=8), message_id="one")
    live.observe(usage)
    live.observe(usage)
    live.observe(ToolCall("Read", "read", {"file_path": "src/cart.py"}))
    first = live.take(10.0)
    assert first is not None
    assert first.tokens == 20
    assert first.file == "src/cart.py"
    assert first.tool == "Read"
    assert live.take(10.49) is None
    second = live.take(10.5)
    assert second is not None
    assert second.seconds == 0.5


def test_live_role_follows_delegation() -> None:
    live = LiveRun(0.0)
    live.observe(ToolCall("Agent", "senior", {"subagent_type": "python-senior"}))
    live.observe(ToolCall("Edit", "edit", {"file_path": "src/cart.py"}, "senior"))
    state = live.take(1.0)
    assert state is not None
    assert state.role == "python-senior"


def test_unknown_legacy_message_does_not_crash_translation() -> None:
    assert parse_english("unrecognized engine detail", "") is None
