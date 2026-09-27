from __future__ import annotations

CHIP_VARIABLES = {
    "investigation": "secondary",
    "feature": "success",
    "fix": "warning",
    "refactor": "sky",
    "docs": "text-muted",
}
MIX_KEYS = {
    "claude": "costs.mix_claude",
    "codex": "costs.mix_codex",
    "opencode": "costs.mix_opencode",
    "cross-engine": "costs.mix_cross",
}
TYPE_KEYS = {
    "investigation": "costs.type_investigation",
    "fix": "costs.type_fix",
    "feature": "costs.type_feature",
    "refactor": "costs.type_refactor",
    "docs": "costs.type_docs",
    "untyped": "costs.type_untyped",
}


def chip_variable(key: str) -> str:
    return CHIP_VARIABLES.get(key, "text")
