from __future__ import annotations

INDEX_TOOL_NAMES = ("find", "card", "impact", "facts", "page", "tests_for", "note")
INDEX_TOOLS = tuple("mcp__cuanta__" + name for name in INDEX_TOOL_NAMES)
INDEX_CONTRACT = (
    "INDEX FIRST: use Cuanta find, card, impact and facts before Grep, Glob or Read. "
    "Open only needed numbered lines or a symbol with Cuanta page. "
    "Write a concise anchored Cuanta note for each file you understand. "
    "A note updates local index knowledge; it never edits source. "
    "Stale facts are not evidence. Keep protected source paths readonly."
)
