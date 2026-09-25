from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable

from cuanta.domain.errors import EnvironmentFailure

SKILL_DIR = "skills/agent-system-init"
COMMANDS_DIR = "commands"
REQUIRED_SKILL_FILES = (
    "SKILL.md",
    "reference/claude-md-spec.md",
    "reference/graph-integration.md",
    "templates/architecture-analyst.md",
    "templates/senior-engineer.md",
    "templates/tester.md",
    "templates/docs-updater.md",
    "templates/AGENTS_GUIDE.template.md",
    "templates/MANDATE_TEMPLATE.template.md",
    "templates/GROUND_TRUTH.template.md",
    "templates/CHANGELOG_INTERNAL.template.md",
    "templates/FLAGS.template.md",
    "templates/RUN_LOG.template.md",
    "templates/IMPROVEMENTS.template.md",
    "templates/HISTORIAS.template.md",
    "templates/LOOP.template.md",
)
COMMANDS = ("init-agents.md", "refresh-agents.md")
RESUME_LINE = "Phases 0 and 0.5 were executed by cuanta. Resume from .claude/forge-state.json."
STAGING_DIR = ".cuanta/forge-out"
STAGING_LINE = (
    "This headless run cannot write to any path with a .claude segment (Claude Code protects "
    "it). Write every file you would create or update under .claude/ to "
    f"{STAGING_DIR}/claude/ instead, with the same relative path and no leading dot — for "
    f"example {STAGING_DIR}/claude/agents/tester.md and "
    f"{STAGING_DIR}/claude/forge-state.json — and read your own state back from there once it "
    "exists. cuanta moves those files into .claude/ after the run, applying the .new.md policy."
)


def forge_root() -> Traversable:
    return resources.files("cuanta").joinpath("assets", "forge")


def _child(base: Traversable, relative: str) -> Traversable:
    node = base
    for part in relative.split("/"):
        node = node.joinpath(part)
    return node


def read_asset(relative: str) -> str:
    node = _child(forge_root(), relative)
    if not node.is_file():
        raise EnvironmentFailure(f"vendored Forge file missing: {relative}", "reinstall cuanta")
    return node.read_text(encoding="utf-8")


def vendored_version() -> dict[str, str]:
    text = read_asset("VERSION")
    pairs = (line.split("=", 1) for line in text.splitlines() if "=" in line)
    return {key.strip(): value.strip() for key, value in pairs}


def verify_manifest() -> tuple[str, ...]:
    root = forge_root()
    missing = [
        f"{SKILL_DIR}/{name}"
        for name in REQUIRED_SKILL_FILES
        if not _child(root, f"{SKILL_DIR}/{name}").is_file()
    ]
    missing.extend(
        f"{COMMANDS_DIR}/{name}"
        for name in COMMANDS
        if not _child(root, f"{COMMANDS_DIR}/{name}").is_file()
    )
    return tuple(missing)


def skill_files() -> tuple[str, ...]:
    found: list[str] = []

    def walk(node: Traversable, prefix: str) -> None:
        for child in sorted(node.iterdir(), key=lambda item: item.name):
            relative = f"{prefix}{child.name}"
            if child.is_dir():
                walk(child, f"{relative}/")
            elif child.is_file():
                found.append(relative)

    walk(_child(forge_root(), SKILL_DIR), "")
    return tuple(found)


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text.strip()
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        return text.strip()
    body = parts[1]
    return body[body.find("\n") + 1 :].strip() if "\n" in body else ""


def init_prompt() -> str:
    body = strip_frontmatter(read_asset(f"{COMMANDS_DIR}/init-agents.md"))
    return f"{body}\n\n{RESUME_LINE}\n\n{STAGING_LINE}"


def refresh_prompt() -> str:
    body = strip_frontmatter(read_asset(f"{COMMANDS_DIR}/refresh-agents.md"))
    return f"{body}\n\n{STAGING_LINE}"
