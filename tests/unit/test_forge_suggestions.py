from __future__ import annotations

from pathlib import Path

from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.forge_suggestions import listed, relocate, siblings, suggest
from cuanta.domain.forge_verify import suggestion_findings
from cuanta.domain.messages import english
from cuanta.domain.new_files import Suggestion
from cuanta.domain.progress import Status

TESTER = ".claude/agents/tester.md"
SIBLING = ".claude/agents/tester.new.md"
SUGGESTED = ".cuanta/forge-suggested/claude/agents/tester.md"
FALLBACK = ".cuanta/forge-suggested/claude/agents/tester.new.md"


class LockedWorkspace(LocalWorkspace):
    def __init__(self, root: Path, locked: str = "", readonly: str = "") -> None:
        super().__init__(root)
        self.locked = locked
        self.readonly = readonly

    def remove(self, relative: str) -> None:
        if relative == self.locked:
            raise PermissionError(relative)
        super().remove(relative)

    def write_bytes(self, relative: str, content: bytes, executable: bool | None = None) -> None:
        if relative == self.readonly:
            raise PermissionError(relative)
        super().write_bytes(relative, content, executable)


def tree(root: Path, files: dict[str, str]) -> LocalWorkspace:
    workspace = LocalWorkspace(root)
    for relative, text in files.items():
        workspace.write_text(relative, text)
    return workspace


def test_an_older_sibling_never_overwrites_a_newer_suggestion(tmp_path: Path) -> None:
    workspace = tree(
        tmp_path, {TESTER: "mine\n", SIBLING: "older forge\n", SUGGESTED: "newer forge\n"}
    )
    assert relocate(workspace) == (SIBLING,)
    assert not workspace.exists(SIBLING)
    assert workspace.read_text(SUGGESTED) == "newer forge\n"
    assert workspace.read_text(FALLBACK) == "older forge\n"
    assert listed(workspace) == (
        Suggestion(SUGGESTED, TESTER, 1, 1),
        Suggestion(FALLBACK, TESTER, 1, 1),
    )


def test_a_sibling_stays_beside_and_warns_when_both_slots_hold_other_text(
    tmp_path: Path,
) -> None:
    workspace = tree(
        tmp_path, {TESTER: "mine\n", SIBLING: "third\n", SUGGESTED: "one\n", FALLBACK: "two\n"}
    )
    assert relocate(workspace) == ()
    assert siblings(workspace) == (SIBLING,)
    beside = suggestion_findings((), (), siblings(workspace))
    assert [finding.status for finding in beside] == [Status.WARN]
    fix = beside[0].fix
    assert fix is not None
    assert english(fix) == (
        f"compare {SIBLING} with your file, keep what you need, then delete {SIBLING}"
    )


def test_a_sibling_equal_to_yours_or_to_a_waiting_suggestion_is_dropped(tmp_path: Path) -> None:
    workspace = tree(
        tmp_path,
        {
            TESTER: "mine\n",
            SIBLING: "forge\r\n",
            SUGGESTED: "forge\n",
            ".claude/agents/docs-updater.md": "same\n",
            ".claude/agents/docs-updater.new.md": "same\r\n",
        },
    )
    assert relocate(workspace) == (".claude/agents/docs-updater.new.md", SIBLING)
    assert siblings(workspace) == ()
    assert workspace.read_text(SUGGESTED) == "forge\n"
    assert not workspace.exists(".cuanta/forge-suggested/claude/agents/docs-updater.md")


def test_moving_keeps_the_exact_bytes_of_a_skill_file(tmp_path: Path) -> None:
    skill = ".claude/skills/agent-system-init/templates/tester.md"
    workspace = tree(tmp_path, {skill: "vendored\n"})
    raw = b"forge\r\nline two\r\n"
    workspace.write_bytes(".claude/skills/agent-system-init/templates/tester.new.md", raw)
    moved = relocate(workspace)
    assert moved == (".claude/skills/agent-system-init/templates/tester.new.md",)
    target = ".cuanta/forge-suggested/claude/skills/agent-system-init/templates/tester.md"
    assert workspace.read_bytes(target) == raw
    assert listed(workspace) == (Suggestion(target, skill, 2, 1),)


def test_a_lone_new_md_file_and_files_outside_forge_folders_stay(tmp_path: Path) -> None:
    workspace = tree(
        tmp_path,
        {
            ".claude/agents/helper.new.md": "only copy\n",
            "docs/notes.md": "mine\n",
            "docs/notes.new.md": "draft\n",
            "CLAUDE.md": "rules\n",
            "CLAUDE.new.md": "my draft\n",
        },
    )
    assert siblings(workspace) == ()
    assert relocate(workspace) == ()
    assert workspace.exists(".claude/agents/helper.new.md")
    assert workspace.exists("docs/notes.new.md")
    assert workspace.exists("CLAUDE.new.md")


def test_a_locked_sibling_stays_until_the_next_init_settles_it(tmp_path: Path) -> None:
    tree(tmp_path, {TESTER: "mine\n", SIBLING: "forge\n"})
    locked = LockedWorkspace(tmp_path, locked=SIBLING)
    assert relocate(locked) == ()
    assert siblings(locked) == (SIBLING,)
    assert locked.read_text(SUGGESTED) == "forge\n"
    assert relocate(LocalWorkspace(tmp_path)) == (SIBLING,)
    assert siblings(LocalWorkspace(tmp_path)) == ()


def test_an_unwritable_suggestion_folder_leaves_the_sibling_in_place(tmp_path: Path) -> None:
    tree(tmp_path, {TESTER: "mine\n", SIBLING: "forge\n"})
    blocked = LockedWorkspace(tmp_path, readonly=SUGGESTED)
    assert relocate(blocked) == ()
    assert blocked.read_text(SIBLING) == "forge\n"
    assert not blocked.exists(SUGGESTED)


def test_an_adopted_suggestion_is_pruned_only_by_verify(tmp_path: Path) -> None:
    workspace = tree(tmp_path, {TESTER: "mine\n"})
    assert suggest(workspace, TESTER, "forge\n") == SUGGESTED
    assert listed(workspace) == (Suggestion(SUGGESTED, TESTER, 1, 1),)
    workspace.write_text(TESTER, "forge\r\n")
    assert listed(workspace) == ()
    assert workspace.exists(SUGGESTED)
    assert listed(workspace, drop_adopted=True) == ()
    assert not workspace.exists(SUGGESTED)


def test_a_suggestion_for_a_file_you_deleted_counts_every_line_as_added(tmp_path: Path) -> None:
    workspace = tree(tmp_path, {SUGGESTED: "a\nb\n"})
    assert listed(workspace) == (Suggestion(SUGGESTED, TESTER, 2, 0),)
