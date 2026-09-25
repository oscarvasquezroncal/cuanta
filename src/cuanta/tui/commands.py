from __future__ import annotations

from dataclasses import dataclass

HOME = "home"
TESTS = "tests"
MANDATES = "mandates"
SPECTRUM = "spectrum"
LEDGER = "ledger"
INSTINCT = "instinct"
HEALTH = "health"
SETTINGS = "settings"
INIT = "init"
LOOP = "loop"
MODELS = "models"

SIDEBAR = (HOME, TESTS, MANDATES, SPECTRUM, LEDGER, INSTINCT, HEALTH, SETTINGS, MODELS)
SECTIONS = (*SIDEBAR, INIT, LOOP)
ICONS = {
    HOME: "⌂",
    TESTS: "✓",
    MANDATES: "✎",
    SPECTRUM: "▦",
    LEDGER: "≣",
    INSTINCT: "◆",
    HEALTH: "✚",
    SETTINGS: "✱",
    INIT: "▶",
    LOOP: "↻",
    MODELS: "◇",
}
CLI_EQUIVALENT = {
    HOME: "cuanta doctor",
    TESTS: "cuanta test",
    MANDATES: "cuanta mandate",
    SPECTRUM: "cuanta spectrum",
    LEDGER: "cuanta ledger export --format csv",
    INSTINCT: "cuanta instinct show",
    HEALTH: "cuanta doctor",
    SETTINGS: "cuanta doctor",
    INIT: "cuanta init",
    LOOP: "cuanta loop",
    MODELS: "cuanta models list",
}

BENCH_COMMAND = "cuanta bench run --suite mini --yes"


@dataclass(frozen=True, slots=True)
class PaletteCommand:
    name: str
    title_key: str
    help_key: str
    action: str
    use_case: str
    key: str = ""


COMMANDS: tuple[PaletteCommand, ...] = (
    PaletteCommand("init", "action.init", "action.init_help", "goto('init')", "init_project", "i"),
    PaletteCommand("tests", "action.tests", "action.tests_help", "run_tests", "gateway", "t"),
    PaletteCommand(
        "mandate",
        "action.mandate",
        "action.mandate_help",
        "goto('mandates')",
        "mandate_service",
        "m",
    ),
    PaletteCommand(
        "spectrum",
        "action.spectrum",
        "action.spectrum_help",
        "goto('spectrum')",
        "spectrum_query",
        "s",
    ),
    PaletteCommand(
        "health", "action.health", "action.health_help", "goto('health')", "doctor", "h"
    ),
    PaletteCommand(
        "ledger", "action.ledger", "action.ledger_help", "goto('ledger')", "ledger", "l"
    ),
    PaletteCommand(
        "instinct", "action.instinct", "action.instinct_help", "goto('instinct')", "decisions"
    ),
    PaletteCommand(
        "settings",
        "action.settings",
        "action.settings_help",
        "goto('settings')",
        "set_project_value",
    ),
    PaletteCommand("loop", "action.loop", "action.loop_help", "goto('loop')", "gateway"),
    PaletteCommand(
        "import", "action.import", "action.import_help", "import_sessions", "transcript_import"
    ),
    PaletteCommand("reload", "action.reload", "action.reload_help", "reload", "home_query", "r"),
    PaletteCommand(
        "onboarding", "action.onboarding", "action.onboarding_help", "onboarding", "routing_policy"
    ),
    PaletteCommand("help", "action.help", "action.help_help", "help", "doctor"),
    PaletteCommand("bench", "action.bench", "action.bench_help", "bench", "latest_bench"),
)


def action_name(action: str) -> str:
    return action.split("(", 1)[0]
