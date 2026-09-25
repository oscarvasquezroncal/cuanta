from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cuanta.adapters.forge.assets import (
    COMMANDS,
    COMMANDS_DIR,
    SKILL_DIR,
    init_prompt,
    read_asset,
    refresh_prompt,
    skill_files,
    vendored_version,
    verify_manifest,
)
from cuanta.domain.errors import EnvironmentFailure
from cuanta.domain.forge_state import InstallSummary


@dataclass
class InstallReport:
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    new_files: list[str] = field(default_factory=list)


def sibling_new(path: Path) -> Path:
    if path.suffix == ".md":
        return path.with_name(f"{path.stem}.new.md")
    return path.with_name(f"{path.name}.new")


class ForgeInstaller:
    def __init__(self, target_root: Path) -> None:
        self._root = target_root

    def plan(self) -> list[tuple[str, Path]]:
        missing = verify_manifest()
        if missing:
            raise EnvironmentFailure(
                f"vendored Forge is incomplete: {', '.join(missing)}", "reinstall cuanta"
            )
        skill_target = self._root / ".claude" / "skills" / "agent-system-init"
        items = [(f"{SKILL_DIR}/{name}", skill_target / name) for name in skill_files()]
        items.extend(
            (f"{COMMANDS_DIR}/{name}", self._root / ".claude" / "commands" / name)
            for name in COMMANDS
        )
        return items

    def install(self, dry_run: bool = False) -> InstallReport:
        report = InstallReport()
        for source, target in self.plan():
            content = read_asset(source)
            label = str(target)
            if target.is_file():
                existing = target.read_text(encoding="utf-8", errors="replace")
                if existing.replace("\r\n", "\n") == content.replace("\r\n", "\n"):
                    report.unchanged.append(label)
                    continue
                alternate = sibling_new(target)
                if not dry_run:
                    alternate.write_text(content, encoding="utf-8", newline="\n")
                report.new_files.append(str(alternate))
                continue
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="\n")
            report.written.append(label)
        return report

    def installed_skill(self) -> str | None:
        path = self._root / ".claude" / "skills" / "agent-system-init" / "SKILL.md"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None


class VendoredForgeKit:
    def __init__(self, target_root: Path) -> None:
        self._installer = ForgeInstaller(target_root)
        self._root = target_root

    def install(self, dry_run: bool) -> InstallSummary:
        report = self._installer.install(dry_run)
        return InstallSummary(
            tuple(report.written), tuple(report.unchanged), tuple(report.new_files)
        )

    def init_prompt(self) -> str:
        return init_prompt()

    def refresh_prompt(self) -> str:
        return refresh_prompt()

    def installed_skill(self) -> str | None:
        return self._installer.installed_skill()

    def vendored_skill(self) -> str:
        return read_asset(f"{SKILL_DIR}/SKILL.md")

    def vendored_version(self) -> str:
        version = vendored_version()
        return f"{version.get('version', '?')} ({version.get('commit', '?')[:7]})"

    def skill_path(self) -> str:
        return str(self._root / ".claude" / "skills" / "agent-system-init" / "SKILL.md")
