from __future__ import annotations


def compact(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if magnitude >= 1_000:
        return f"{value / 1_000:.1f}K"
    if float(value).is_integer():
        return f"{int(value)}"
    return f"{value:.1f}"


def thousands(value: int) -> str:
    return f"{value:,}"


def percent(share: float) -> str:
    return f"{share * 100:.1f}%"


def usd(value: float | None, source: str = "", partial: bool = False) -> str:
    if value is None:
        return "n/a"
    text = f"${value:,.4f}" if value < 1 else f"${value:,.2f}"
    if partial:
        from cuanta.domain.messages import english, msg

        key = "result.partial_estimated_cost" if source == "estimated" else "result.partial_cost"
        return english(msg(key, cost=text))
    return f"{text} (estimated)" if source == "estimated" else text


def turn_count(turns: int, max_turns: int, partial: bool = False) -> str:
    count = f"{turns}/{max_turns}" if max_turns > 0 else str(turns)
    if not partial:
        return count
    from cuanta.domain.messages import english, msg

    return english(msg("result.partial_turns", turns=count))


def duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"
