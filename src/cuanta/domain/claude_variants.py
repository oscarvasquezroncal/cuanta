from __future__ import annotations

from dataclasses import dataclass

from cuanta.domain.errors import DomainFailure

EFFORTS = ("low", "medium", "high", "xhigh", "max")
VARIANTS = (*EFFORTS, "ultracode", "fast", *(f"fast-{level}" for level in (*EFFORTS, "ultracode")))
MODEL_ALIASES = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5"}
PURE_MODEL_ENV = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_AUTO_MODE_MODEL",
    "CLAUDE_CODE_BG_CLASSIFIER_MODEL",
)
DELEGATION_TOOLS = ("Agent", "Task", "Skill", "Workflow")
COORDINATION_TOOLS = ("SendMessage", "ListAgents", "TaskStop")
SCHEDULING_TOOLS = (
    "RemoteTrigger",
    "CronCreate",
    "CronDelete",
    "CronList",
    "ScheduleWakeup",
    "EnterWorktree",
    "ExitWorktree",
)


@dataclass(frozen=True, slots=True)
class ClaudeVariant:
    effort: str = ""
    fast: bool = False
    ultracode: bool = False


def resolve_variant(variant: str, model: str) -> ClaudeVariant:
    if not variant:
        return ClaudeVariant()
    if variant in EFFORTS:
        return ClaudeVariant(effort=variant)
    if variant == "ultracode":
        return ClaudeVariant(effort="xhigh", ultracode=True)
    if variant == "fast" or variant.startswith("fast-"):
        effort = variant.removeprefix("fast-") if variant != "fast" else ""
        if effort not in ("", *EFFORTS, "ultracode"):
            raise DomainFailure(f"unknown Claude variant {variant}", f"use {', '.join(VARIANTS)}")
        resolved = MODEL_ALIASES.get(model, model)
        if "opus-5" not in resolved and "opus-4-8" not in resolved:
            raise DomainFailure("fast output requires a supported Opus model", "choose opus")
        return ClaudeVariant(
            effort="xhigh" if effort == "ultracode" else effort,
            fast=True,
            ultracode=effort == "ultracode",
        )
    raise DomainFailure(f"unknown Claude variant {variant}", f"use {', '.join(VARIANTS)}")


def session_denied_tools(variant: str) -> tuple[str, ...]:
    if variant.endswith("ultracode"):
        return SCHEDULING_TOOLS
    return (*DELEGATION_TOOLS, *COORDINATION_TOOLS, *SCHEDULING_TOOLS)


def pure_environment(model: str) -> dict[str, str]:
    if not model:
        raise DomainFailure("pure runs require a model", "select an explicit model")
    resolved = MODEL_ALIASES.get(model, model)
    return {
        **dict.fromkeys(PURE_MODEL_ENV, resolved),
        "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1",
        "CLAUDE_CODE_NO_MODEL_FALLBACK": "1",
        "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
    }


def variant_settings(model: str, variant: str, pure: bool) -> dict[str, object]:
    choice = resolve_variant(variant, model)
    result: dict[str, object] = {
        "fastMode": choice.fast,
        "ultracode": choice.ultracode,
    }
    if choice.effort and choice.effort != "max":
        result["effortLevel"] = choice.effort
    if choice.ultracode:
        result["enableWorkflows"] = True
    if pure:
        resolved = MODEL_ALIASES.get(model, model)
        result.update(
            {
                "model": resolved,
                "env": pure_environment(resolved),
                "promptSuggestionEnabled": False,
                "awaySummaryEnabled": False,
                "precomputeCompactionEnabled": False,
            }
        )
    return result
