from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from cuanta.domain.detection import environment_dir
from cuanta.domain.gitignore import GITIGNORE, IgnoreRules, git_config, git_true
from cuanta.domain.gitindex import TrackedByGit, parse_index, tracked_by_git
from cuanta.domain.handoff import gitdir_from_file

GIT_DIR = ".git"
HOME_PREFIXES = ("~/", "~\\")
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
WINDOWS = os.name == "nt"

Pending = tuple[str, str, IgnoreRules, bool]


@dataclass(frozen=True, slots=True)
class GitView:
    rules: IgnoreRules = field(default_factory=IgnoreRules)
    tracked: TrackedByGit = field(default_factory=TrackedByGit)
    origin: Path | None = None


@dataclass(frozen=True, slots=True)
class VisibleFile:
    path: str
    entry: os.DirEntry[str]
    ignored: bool


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def config_home(home: Path, environ: Mapping[str, str]) -> Path:
    configured = environ.get("XDG_CONFIG_HOME")
    return (Path(configured) if configured else home / ".config") / "git"


def git_dir(origin: Path) -> Path | None:
    marker = origin / GIT_DIR
    try:
        if marker.is_dir():
            return marker
        if not marker.is_file():
            return None
    except OSError:
        return None
    target = gitdir_from_file(_text(marker))
    if not target:
        return None
    path = Path(target)
    return path if path.is_absolute() else origin / path


def common_dir(gitdir: Path) -> Path:
    target = _text(gitdir / "commondir").strip()
    if not target:
        return gitdir
    path = Path(target)
    return path if path.is_absolute() else gitdir / path


def _settings(gitdir: Path | None, home: Path, environ: Mapping[str, str]) -> dict[str, str]:
    paths = [config_home(home, environ) / "config", home / ".gitconfig"]
    if gitdir is not None:
        paths.append(common_dir(gitdir) / "config")
    merged: dict[str, str] = {}
    for path in paths:
        merged.update(git_config(_text(path)))
    return merged


def global_excludes(
    origin: Path, settings: Mapping[str, str], home: Path, environ: Mapping[str, str]
) -> str:
    configured = settings.get("core.excludesfile", "")
    if not configured:
        path = config_home(home, environ) / "ignore"
    elif configured.startswith(HOME_PREFIXES):
        path = home / configured[2:]
    else:
        path = Path(configured)
    return _text(path if path.is_absolute() else origin / path)


def _rules(
    origin: Path, gitdir: Path | None, home: Path, environ: Mapping[str, str]
) -> IgnoreRules:
    settings = _settings(gitdir, home, environ)
    ignorecase = git_true(settings.get("core.ignorecase", ""))
    excludes = global_excludes(origin, settings, home, environ)
    rules = IgnoreRules(ignorecase=ignorecase).with_file("", excludes)
    if gitdir is None:
        return rules
    return rules.with_file("", _text(common_dir(gitdir) / "info" / "exclude"))


def root_rules(origin: Path, home: Path, environ: Mapping[str, str]) -> IgnoreRules:
    return _rules(origin, git_dir(origin), home, environ)


def _tracked(gitdir: Path | None, ignorecase: bool) -> TrackedByGit:
    if gitdir is None:
        return TrackedByGit()
    try:
        data = (gitdir / "index").read_bytes()
    except OSError:
        return TrackedByGit()
    return tracked_by_git(parse_index(data) or (), ignorecase)


def load_git_view(origin: Path, home: Path, environ: Mapping[str, str]) -> GitView:
    gitdir = git_dir(origin)
    rules = _rules(origin, gitdir, home, environ)
    return GitView(rules, _tracked(gitdir, rules.ignorecase), origin if gitdir else None)


def linked(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink() or entry.is_junction():
        return True
    if not WINDOWS:
        return False
    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & REPARSE_POINT)


def _name(entry: os.DirEntry[str]) -> str:
    return entry.name


def _with_gitignore(rules: IgnoreRules, base: str, entries: list[os.DirEntry[str]]) -> IgnoreRules:
    marker = next((entry for entry in entries if entry.name == GITIGNORE), None)
    if marker is None:
        return rules
    try:
        if linked(marker) or not marker.is_file(follow_symlinks=False):
            return rules
    except OSError:
        return rules
    return rules.with_file(base, _text(Path(marker.path)))


def _same(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _checkout(origin: Path, local: bool, relative: str, entries: list[os.DirEntry[str]]) -> bool:
    if local:
        return any(entry.name.casefold() == GIT_DIR for entry in entries)
    return os.path.lexists(origin / relative / GIT_DIR)


def walk_visible(
    root: Path, view: GitView, skip_dir: Callable[[str, str], bool]
) -> Iterator[VisibleFile]:
    tracked = view.tracked
    origin = view.origin
    local = origin is not None and _same(origin, root)
    stack: list[Pending] = [(str(root), "", view.rules, False)]
    while stack:
        folder, prefix, rules, hidden = stack.pop()
        try:
            with os.scandir(folder) as listing:
                entries = sorted(listing, key=_name)
        except OSError:
            continue
        if prefix and environment_dir(entry.name for entry in entries):
            continue
        here = prefix[:-1]
        if (
            origin is not None
            and here
            and not tracked.holds(here)
            and not tracked.has_under(here)
            and _checkout(origin, local, here, entries)
        ):
            continue
        if not hidden:
            rules = _with_gitignore(rules, here, entries)
        folders: list[Pending] = []
        for entry in entries:
            relative = prefix + entry.name
            try:
                if linked(entry):
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                if not is_dir and not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if is_dir:
                if skip_dir(entry.name, relative):
                    continue
                ignored = hidden or rules.matches(relative, True)
                if not ignored or tracked.has_under(relative):
                    folders.append((entry.path, f"{relative}/", rules, ignored))
            elif hidden:
                if tracked.holds(relative):
                    yield VisibleFile(relative, entry, False)
            else:
                ignored = rules.matches(relative, False) and not tracked.holds(relative)
                yield VisibleFile(relative, entry, ignored)
        stack.extend(reversed(folders))
