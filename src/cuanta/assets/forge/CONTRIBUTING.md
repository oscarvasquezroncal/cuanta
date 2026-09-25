# Contributing to claude-agent-forge

Thanks for your interest in improving this project. Contributions are welcome.

## Ways to help
- **Report results**: ran `/init-agents` on a stack we haven't tested? Open an issue describing the stack, what it generated well, and any rough edges (especially unfilled `{{placeholders}}`).
- **Improve the templates**: the four agent skeletons in `skills/agent-system-init/templates/` and the method in `SKILL.md` are the core. PRs that make them more accurate for a given language/framework are valuable.
- **Improve the CLAUDE.md spec**: `skills/agent-system-init/reference/claude-md-spec.md`.

## Ground rules for this project
- **Honest scope.** This tool standardizes context and improves consistency. It does not promise bug-free code. Keep docs and copy aligned with that — no overclaiming.
- **Stay focused.** The value is the fixed 4-agent core + verified docs. Proposals that balloon the tool into a sprawling multi-subsystem framework will likely be declined; open an issue to discuss before large additions.
- **Evidence over assumption.** The skill documents only what it can verify in code, marking the rest `[UNVERIFIED]`. Keep that principle in any change.

## Submitting a change
1. Fork the repo and create a branch.
2. Make your change. If you touched the skill or templates, test it by running `/init-agents` on a real repo and confirm there are no unfilled placeholders.
3. Open a PR with a clear description: what changed, why, and how you tested it.

## Reporting a security concern
See `SECURITY.md`.