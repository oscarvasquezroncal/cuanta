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


def usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"


def duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"
