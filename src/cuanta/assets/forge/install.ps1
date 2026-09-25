# claude-agent-forge - manual installer for Windows (fallback for the plugin path)
# Installs the agent-system-init skill + the /init-agents and /refresh-agents commands
# into your Claude Code config.
$ErrorActionPreference = "Stop"

$ClaudeDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { "$env:USERPROFILE\.claude" }
$SkillSrc = "skills\agent-system-init"
$CmdSrcDir = "commands"
$Commands = @("init-agents.md", "refresh-agents.md")

# Manifest of every file the skill needs at runtime. Missing one is a loud failure, not a
# silent half-install: a phase that reads a missing template fails in the user's repo, not here.
# ADDING A TEMPLATE = ADDING ONE LINE HERE (and the same line to install.sh). The check is by
# NAME, never by count — a count is a number that drifts, which this project forbids everywhere
# else. Keep this list and install.sh's identical.
$RequiredSkillFiles = @(
  "SKILL.md",
  "reference\claude-md-spec.md",
  "reference\graph-integration.md",
  "templates\architecture-analyst.md",
  "templates\senior-engineer.md",
  "templates\tester.md",
  "templates\docs-updater.md",
  "templates\AGENTS_GUIDE.template.md",
  "templates\MANDATE_TEMPLATE.template.md",
  "templates\GROUND_TRUTH.template.md",
  "templates\CHANGELOG_INTERNAL.template.md",
  "templates\FLAGS.template.md",
  "templates\RUN_LOG.template.md",
  "templates\IMPROVEMENTS.template.md",
  "templates\HISTORIAS.template.md",
  "templates\LOOP.template.md"
)

Write-Host "==> Installing claude-agent-forge into: $ClaudeDir"

if (-not (Test-Path $SkillSrc)) {
  Write-Error "Run this from the repo root (skills\agent-system-init not found)."
  exit 1
}

foreach ($f in $RequiredSkillFiles) {
  if (-not (Test-Path (Join-Path $SkillSrc $f))) {
    Write-Error "Missing $SkillSrc\$f"
    exit 1
  }
}

# The skill directory is copied whole: SKILL.md, reference\ (claude-md-spec.md,
# graph-integration.md) and templates\ (4 agents + 8 artifact templates).
$SkillDest = Join-Path $ClaudeDir "skills\agent-system-init"
New-Item -ItemType Directory -Force -Path $SkillDest | Out-Null
Copy-Item -Recurse -Force "$SkillSrc\*" $SkillDest
Write-Host "    skill   -> $SkillDest"

$CmdDest = Join-Path $ClaudeDir "commands"
New-Item -ItemType Directory -Force -Path $CmdDest | Out-Null
foreach ($cmd in $Commands) {
  $src = Join-Path $CmdSrcDir $cmd
  if (-not (Test-Path $src)) {
    Write-Error "Missing $src"
    exit 1
  }
  Copy-Item -Force $src (Join-Path $CmdDest $cmd)
  Write-Host "    command -> $CmdDest\$cmd"
}

Write-Host "==> Done."
Write-Host "    RESTART Claude Code before running anything. The skill and its commands are NOT"
Write-Host "    registered in the session that installed them - running /init-agents now gives"
Write-Host "    'Error: Unknown skill'. Restart, then run /init-agents inside any repo."
Write-Host "    Later, run /refresh-agents to re-verify docs against code, drain any queued"
Write-Host "    per-directory rulebooks, and enforce the size ceiling."
