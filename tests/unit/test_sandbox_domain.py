from __future__ import annotations

import pytest

from cuanta.domain.gitignore import IgnoreRules, git_config, git_true
from cuanta.domain.handoff import (
    Workflow,
    branch_from_head,
    branch_name,
    cd_line,
    chain,
    change_type,
    commit_subject,
    gitdir_from_file,
    handoff,
    handoff_commands,
    parse_workflow,
    proposed_subject,
)
from cuanta.domain.sandbox import (
    ChangeKind,
    CopyAction,
    FileChange,
    copy_action,
    dependency_cache,
    diff_manifests,
    docs_only,
    drifted,
    renamed_away,
    tracked,
    unsafe_path,
)
from cuanta.domain.shells import Shell


@pytest.mark.parametrize(
    ("relative", "is_dir", "action"),
    [
        (".git", True, CopyAction.SKIP),
        ("src/.next", True, CopyAction.SKIP),
        ("pkg/__pycache__/x.pyc", False, CopyAction.SKIP),
        (".venv", True, CopyAction.SKIP),
        ("node_modules", True, CopyAction.LINK),
        ("packages/app/node_modules", True, CopyAction.LINK),
        ("node_modules", False, CopyAction.COPY),
        (".cuanta", True, CopyAction.COPY),
        (".cuanta/config.toml", False, CopyAction.COPY),
        (".cuanta/ledger.db", False, CopyAction.SKIP),
        (".cuanta/tmp", True, CopyAction.SKIP),
        (".cuanta/runs", True, CopyAction.SKIP),
        ("vendor/lib.go", False, CopyAction.COPY),
        ("dist/app.js", False, CopyAction.COPY),
        (".claude/agents/senior.md", False, CopyAction.COPY),
        (".env.local", False, CopyAction.COPY),
    ],
)
def test_copy_policy_skips_caches_links_dependencies_and_keeps_sources(
    relative: str, is_dir: bool, action: CopyAction
) -> None:
    assert copy_action(relative, is_dir) is action


def test_tracked_paths_leave_out_state_dependencies_and_caches() -> None:
    assert tracked("src/app.ts")
    assert tracked(".claude/settings.json")
    assert not tracked(".cuanta/config.toml")
    assert not tracked("node_modules/pkg/index.js")
    assert not tracked("app/.next/cache/x")


def test_manifest_diff_reports_added_modified_and_deleted() -> None:
    base = {"a.txt": "1", "b.txt": "2", "c.txt": "3"}
    end = {"a.txt": "1", "b.txt": "9", "d.txt": "4"}
    assert diff_manifests(base, end) == (
        FileChange("b.txt", ChangeKind.MODIFIED, "2", "9"),
        FileChange("c.txt", ChangeKind.DELETED, "3", None),
        FileChange("d.txt", ChangeKind.ADDED, None, "4"),
    )


def test_drift_refuses_changed_deleted_and_newly_created_targets() -> None:
    changes = diff_manifests({"a": "1", "b": "2"}, {"a": "9", "c": "3"})
    assert drifted(changes, {"a": "1", "b": "2", "c": None}) == ()
    assert drifted(changes, {"a": "7", "b": "2", "c": None}) == ("a",)
    assert drifted(changes, {"a": "1", "b": None, "c": None}) == ("b",)
    assert drifted(changes, {"a": "1", "b": "2", "c": "5"}) == ("c",)


def test_case_only_rename_is_not_drift_on_case_insensitive_filesystems() -> None:
    changes = diff_manifests({"README.md": "1"}, {"readme.md": "1"})
    assert drifted(changes, {"README.md": "1", "readme.md": "1"}) == ()


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "/etc/x",
        "C:/x",
        "a/../b",
        "a//b",
        "./a",
        "a\\b",
        ".cuanta/ledger.db",
        ".git/HEAD",
        ".GIT/hooks/pre-commit",
        ".Cuanta/ledger.db",
        "NODE_MODULES/x.js",
        "src/app.ts:stream",
        "src/trailing. ",
        "src/dot.",
        "src/tab\tname",
        "GIT~1/hooks/pre-commit",
        "CUANTA~1/ledger.db",
        "NODE_M~1/x.js",
    ],
)
def test_unsafe_paths_are_refused(relative: str) -> None:
    assert unsafe_path(relative)


def test_plain_relative_paths_are_safe() -> None:
    assert not unsafe_path("src/app/page.tsx")


def test_docs_only_detection() -> None:
    assert docs_only(["README.md", "docs/guide/setup.png", "docs/notes.txt"])
    assert not docs_only(["README.md", "src/app.ts"])
    assert not docs_only(["public/robots.txt"])
    assert not docs_only(["requirements.txt"])
    assert not docs_only([])


def test_chained_commands_stop_at_the_first_failure_in_every_shell() -> None:
    commands = ("a", "b", "c")
    assert chain(commands, Shell.BASH) == "a && b && c"
    assert chain(commands, Shell.CMD) == "a && b && c"
    assert chain(commands, Shell.UNKNOWN) == "a && b && c"
    assert chain(commands, Shell.FISH) == "a; and b; and c"
    assert chain(commands, Shell.POWERSHELL) == "a; if ($?) { b; if ($?) { c } }"
    assert chain((), Shell.BASH) == ""


def test_dependency_caches_are_recognised_directly_under_node_modules() -> None:
    assert dependency_cache("node_modules/.cache")
    assert dependency_cache("packages/web/node_modules/.vite")
    assert not dependency_cache("node_modules/pkg/.cache")
    assert not dependency_cache(".cache")


def test_only_case_only_renames_are_renamed_away() -> None:
    changes = diff_manifests({"src/app.ts": "1", "src/old.ts": "2"}, {"src/App.ts": "1"})
    assert renamed_away(changes) == frozenset({"src/app.ts"})


@pytest.mark.parametrize(
    ("patterns", "path", "is_dir", "expected"),
    [
        ("*.log\n", "a/b/debug.log", False, True),
        ("/build\n", "build", True, True),
        ("/build\n", "src/build", True, False),
        ("build/\n", "src/build", True, True),
        ("build/\n", "build", False, False),
        ("doc/*.txt\n", "doc/notes.txt", False, True),
        ("doc/*.txt\n", "doc/sub/notes.txt", False, False),
        ("**/logs\n", "deep/logs", True, True),
        ("logs/**\n", "logs/a/b", False, True),
        ("a/**/b\n", "a/x/y/b", False, True),
        ("a/**/b\n", "a/b", False, True),
        ("*.tsbuildinfo\nnext-env.d.ts\n", "tsconfig.tsbuildinfo", False, True),
        ("*.log\n!keep.log\n", "keep.log", False, False),
        ("# comment\n\n\\#hash\n", "#hash", False, True),
        ("file?.txt\n", "file1.txt", False, True),
        ("[ab].txt\n", "b.txt", False, True),
        ("[!ab].txt\n", "b.txt", False, False),
        (".env*\n", ".env.local", False, True),
    ],
)
def test_gitignore_patterns(patterns: str, path: str, is_dir: bool, expected: bool) -> None:
    assert IgnoreRules().with_file("", patterns).ignored(path, is_dir) is expected


def test_ignored_directory_hides_its_files_even_with_negation() -> None:
    rules = IgnoreRules().with_file("", "out/\n!out/keep.txt\n")
    assert rules.ignored("out/keep.txt")


def test_nested_gitignore_is_relative_to_its_directory_and_wins() -> None:
    rules = IgnoreRules().with_file("", "*.gen\n").with_file("pkg", "!special.gen\n/local\n")
    assert rules.ignored("a.gen")
    assert not rules.ignored("pkg/special.gen")
    assert rules.ignored("pkg/local", True)
    assert not rules.ignored("local", True)


def test_git_directory_is_always_ignored() -> None:
    assert IgnoreRules().ignored(".git/HEAD")


def test_change_type_by_task_and_docs_only_paths() -> None:
    assert change_type("feature", ["src/a.ts"]) == "feat"
    assert change_type("bug", ["src/a.ts"]) == "fix"
    assert change_type("refactor", ["src/a.ts"]) == "refactor"
    assert change_type("feature", ["docs/setup.md"]) == "docs"
    assert change_type("", ["src/a.ts"]) == "chore"
    assert change_type("investigation", ["src/a.ts"]) == ""


def test_commit_subject_prefers_a_conventional_proposal() -> None:
    proposal = "Suggested commit:\n`feat(seo): add sitemap and robots routes`\n"
    assert proposed_subject(proposal) == "feat(seo): add sitemap and robots routes"
    assert (
        commit_subject("feat", "whatever", proposal) == "feat(seo): add sitemap and robots routes"
    )


def test_commit_subject_falls_back_to_type_and_request() -> None:
    subject = commit_subject("fix", "Reveal content when GSAP fails.\n", "no proposal")
    assert subject == "fix: reveal content when GSAP fails"
    long = commit_subject("feat", "word " * 40, "")
    assert len(long) <= 72
    assert commit_subject("feat", "", "") == "feat: apply cuanta changes"


def test_branch_names_follow_the_type_prefix() -> None:
    assert branch_name("feat", "feat(seo): add sitemap and robots", "01ABCD") == (
        "feat/add-sitemap-and-robots-abcd"
    )
    assert branch_name("fix", "fix: ¡Revelar contenido!", "01AB9Z") == "fix/revelar-contenido-ab9z"
    assert branch_name("docs", "docs: ***", "01M3ASK1YZAAD65TTMV0VVPSGZ") == "docs/v0vvpsgz"


def test_head_and_gitdir_parsing() -> None:
    assert branch_from_head("ref: refs/heads/master\n") == "master"
    assert branch_from_head("4f2a9c0d\n") == ""
    assert gitdir_from_file("gitdir: ../repo/.git/worktrees/x\n") == "../repo/.git/worktrees/x"
    assert gitdir_from_file("nonsense") == ""


@pytest.mark.parametrize(
    ("shell", "expected"),
    [
        (Shell.CMD, 'cd /d "C:\\work\\it\'s"'),
        (Shell.POWERSHELL, "Set-Location -LiteralPath 'C:\\work\\it''s'"),
        (Shell.BASH, "cd 'C:\\work\\it'\\''s'"),
        (Shell.FISH, "cd 'C:\\\\work\\\\it\\'s'"),
        (Shell.UNKNOWN, None),
    ],
)
def test_cd_line_quotes_per_shell(shell: Shell, expected: str | None) -> None:
    assert cd_line("C:\\work\\it's", shell) == expected


def test_handoff_commands_never_pass_user_text_through_the_shell() -> None:
    commands = handoff_commands("/p", "01RUN", Shell.BASH, "feat/x", pending=True)
    assert commands == (
        "cd '/p'",
        "git switch -c feat/x",
        "cuanta runs apply 01RUN --yes",
        "git --literal-pathspecs add -A "
        "--pathspec-from-file=.cuanta/trials/01RUN/paths.nul --pathspec-file-nul",
        "git --literal-pathspecs commit -F .cuanta/trials/01RUN/commit.txt "
        "--pathspec-from-file=.cuanta/trials/01RUN/paths.nul --pathspec-file-nul",
    )


def test_trunk_handoff_commits_on_the_current_branch_and_skips_applied_step() -> None:
    result = handoff(
        Workflow.TRUNK,
        "feat",
        "feat: add a sitemap",
        "01RUN",
        "/p",
        Shell.UNKNOWN,
        "master",
        pending=False,
        git=True,
    )
    assert result.branch == ""
    assert result.current_branch == "master"
    assert result.subject == "feat: add a sitemap"
    assert not any("switch" in line or "runs apply" in line for line in result.commands)
    branched = handoff(
        Workflow.BRANCHES, "feat", "feat: add a sitemap", "01RUN", "/p", Shell.BASH, "", True, True
    )
    assert branched.branch == "feat/add-a-sitemap-1run"
    assert "git switch -c feat/add-a-sitemap-1run" in branched.commands
    assert branched.chained == " && ".join(branched.commands)


def test_no_git_directory_means_no_git_commands() -> None:
    commands = handoff_commands("/p", "01RUN", Shell.BASH, "feat/x", pending=True, git=False)
    assert commands == ("cd '/p'", "cuanta runs apply 01RUN --yes")


def test_workflow_parsing_defaults_to_branches() -> None:
    assert parse_workflow("trunk") is Workflow.TRUNK
    assert parse_workflow("branches") is Workflow.BRANCHES
    assert parse_workflow("weird") is Workflow.BRANCHES


def test_case_insensitive_rules_match_any_case() -> None:
    rules = IgnoreRules(ignorecase=True).with_file("", "Build/\n*.LOG\n")
    assert rules.ignored("build/out.js")
    assert rules.ignored("logs/app.log")
    assert not IgnoreRules().with_file("", "*.LOG\n").ignored("app.log")


def test_ordinary_tilde_names_stay_safe() -> None:
    assert not unsafe_path("docs/notes~draft.md")
    assert not unsafe_path("src/backup~.ts")


def test_git_config_reads_core_settings_like_git() -> None:
    text = (
        "[user]\n\tname = x\n[core]\n\tIgnoreCase = true\n"
        '\texcludesFile = "~/my ignore" ; comment\n'
        "\tbare\n"
        '[remote "origin"]\n\turl = https://example.test/repo.git\n'
    )
    values = git_config(text)
    assert values["core.ignorecase"] == "true"
    assert values["core.excludesfile"] == "~/my ignore"
    assert values["core.bare"] == "true"
    assert values["remote.origin.url"] == "https://example.test/repo.git"
    assert git_true(values["core.ignorecase"])
    assert not git_true("false")


def test_git_config_accepts_a_bom_and_keys_on_the_header_line() -> None:
    values = git_config("﻿[core] excludesfile = ~/.gitignore_global\r\n\tignorecase\r\n")
    assert values["core.excludesfile"] == "~/.gitignore_global"
    assert values["core.ignorecase"] == "true"
    assert IgnoreRules().with_file("", "﻿*.log\n").ignored("app.log")
