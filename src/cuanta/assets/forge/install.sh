#!/usr/bin/env bash
# claude-agent-forge — manual installer (fallback for the plugin path)
# Installs the agent-system-init skill + the /init-agents and /refresh-agents commands
# into your Claude Code config.
set -euo pipefail

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SKILL_SRC="skills/agent-system-init"
CMD_SRC_DIR="commands"
COMMANDS="init-agents.md refresh-agents.md"

# Manifest of every file the skill needs at runtime. Missing one is a loud failure, not a
# silent half-install: a phase that reads a missing template fails in the user's repo, not here.
# ADDING A TEMPLATE = ADDING ONE LINE HERE (and the same line to install.ps1). The check is by
# NAME, never by count — a count is a number that drifts, which this project forbids everywhere
# else. Keep this list and install.ps1's identical.
REQUIRED_SKILL_FILES="
SKILL.md
reference/claude-md-spec.md
reference/graph-integration.md
templates/architecture-analyst.md
templates/senior-engineer.md
templates/tester.md
templates/docs-updater.md
templates/AGENTS_GUIDE.template.md
templates/MANDATE_TEMPLATE.template.md
templates/GROUND_TRUTH.template.md
templates/CHANGELOG_INTERNAL.template.md
templates/FLAGS.template.md
templates/RUN_LOG.template.md
templates/IMPROVEMENTS.template.md
templates/HISTORIAS.template.md
templates/LOOP.template.md
"

echo "==> Installing claude-agent-forge into: $CLAUDE_DIR"

if [ ! -d "$SKILL_SRC" ]; then
  echo "ERROR: run this from the repo root (skills/agent-system-init not found)." >&2
  exit 1
fi

for f in $REQUIRED_SKILL_FILES; do
  if [ ! -f "$SKILL_SRC/$f" ]; then
    echo "ERROR: missing $SKILL_SRC/$f" >&2
    exit 1
  fi
done

# The skill directory is copied whole: SKILL.md, reference/ (claude-md-spec.md,
# graph-integration.md) and templates/ (4 agents + 8 artifact templates).
mkdir -p "$CLAUDE_DIR/skills/agent-system-init"
cp -R "$SKILL_SRC/." "$CLAUDE_DIR/skills/agent-system-init/"
echo "    skill   -> $CLAUDE_DIR/skills/agent-system-init"

mkdir -p "$CLAUDE_DIR/commands"
for cmd in $COMMANDS; do
  if [ ! -f "$CMD_SRC_DIR/$cmd" ]; then
    echo "ERROR: missing $CMD_SRC_DIR/$cmd" >&2
    exit 1
  fi
  cp "$CMD_SRC_DIR/$cmd" "$CLAUDE_DIR/commands/$cmd"
  echo "    command -> $CLAUDE_DIR/commands/$cmd"
done

echo "==> Done."
echo "    RESTART Claude Code before running anything. The skill and its commands are NOT"
echo "    registered in the session that installed them — running /init-agents now gives"
echo "    'Error: Unknown skill'. Restart, then run /init-agents inside any repo."
echo "    Later, run /refresh-agents to re-verify docs against code, drain any queued"
echo "    per-directory rulebooks, and enforce the size ceiling."
