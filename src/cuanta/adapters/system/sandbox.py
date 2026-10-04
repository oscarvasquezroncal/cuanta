from __future__ import annotations

import ast
import ctypes
import errno
import hashlib
import importlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Sequence
from contextlib import suppress
from ctypes import wintypes
from functools import partial
from pathlib import Path
from typing import Any

from cuanta.adapters.system.git_files import root_rules
from cuanta.domain.errors import EnvironmentFailure
from cuanta.domain.gitignore import GITIGNORE, IgnoreRules
from cuanta.domain.sandbox import (
    GUARDED_TRIAL_FILES,
    LINKED_DIRS,
    STATE_DIR,
    STATE_SETTINGS,
    TRIALS_DIR,
    UNGUARDED_SETTINGS,
    CopyAction,
    copy_action,
    dependency_cache,
    tracked,
)
from cuanta.ports.sandbox import FileStat, SandboxCopy

SANDBOX_DIR = "cuanta-sandbox"
VOLUME_DIR = ".cuanta-sandbox"
MARKER = "sandbox.json"
EXTENDED = "\\\\?\\"
CHUNK = 1 << 16
REMOVE_RETRIES = (0.2, 0.5, 1.0, 2.0)
PRIVATE = 0o700
SHARED_WRITE = 0o022
EDITABLE_MAPPING = re.compile(r"MAPPING\s*(?::[^=]*)?=\s*(\{.*?\})", re.DOTALL)
BUILD_OUTPUTS = frozenset({"target", "build", "dist", "out", "coverage", "obj"})
SYNCHRONIZE = 0x00100000
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
TOKEN_OWNER = 4
FILE_OBJECT = 1
OWNER_INFORMATION = 1
INVALID_PARAMETER = 87
OWNER_RIGHTS = "*S-1-3-4"
LOCAL_SYSTEM = "*S-1-5-18"
DISK_ERRORS = frozenset({errno.ENOSPC, errno.EFBIG, getattr(errno, "EDQUOT", errno.ENOSPC)})


def default_parents() -> tuple[Path, ...]:
    if os.name == "nt":
        parents = [Path(tempfile.gettempdir())]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            parents.append(Path(local) / "cuanta")
        return tuple(parents)
    cache = os.environ.get("XDG_CACHE_HOME")
    home = (Path(cache) if cache else Path.home() / ".cache") / "cuanta"
    return (home, Path(tempfile.gettempdir()))


def _long(path: Path) -> str:
    text = str(path)
    if sys.platform != "win32" or text.startswith(EXTENDED):
        return text
    if text.startswith("\\\\"):
        return EXTENDED + "UNC\\" + text[2:]
    return EXTENDED + text


def _plain(target: str) -> str:
    if target.startswith(EXTENDED + "UNC\\"):
        return "\\\\" + target[len(EXTENDED) + 4 :]
    return target.removeprefix(EXTENDED)


def _real(path: str | Path) -> Path:
    return Path(_plain(os.path.realpath(path)))


def _is_link(entry: os.DirEntry[str]) -> bool:
    return entry.is_symlink() or entry.is_junction()


def _junction(target: str, link: str) -> None:
    if sys.platform == "win32":
        winapi: Any = importlib.import_module("_winapi")
        winapi.CreateJunction(target, link)
        return
    raise OSError("junctions exist only on Windows")


def _private(path: Path) -> bool:
    user: Callable[[], int] | None = vars(os).get("getuid")
    if user is None:
        return True
    info = os.lstat(path)
    return stat.S_ISDIR(info.st_mode) and info.st_uid == user() and not info.st_mode & SHARED_WRITE


def _alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_alive(pid: int) -> bool:
    kernel: Any = vars(ctypes)["WinDLL"]("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        last_error: Any = vars(ctypes)["get_last_error"]
        return bool(last_error() != INVALID_PARAMETER)
    try:
        return bool(kernel.WaitForSingleObject(handle, 0) != 0)
    finally:
        kernel.CloseHandle(handle)


def _windows_owner(path: Path) -> tuple[bool, str]:
    advapi: Any = vars(ctypes)["WinDLL"]("advapi32", use_last_error=True)
    kernel: Any = vars(ctypes)["WinDLL"]("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    advapi.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    advapi.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    advapi.EqualSid.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    advapi.ConvertSidToStringSidW.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    )
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return False, ""
    try:
        token_user = _token_sid(advapi, token, TOKEN_USER)
        if token_user is None:
            return False, ""
        user = token_user[1]
        token_owner = _token_sid(advapi, token, TOKEN_OWNER)
        accepted = [user] if token_owner is None else [user, token_owner[1]]
        text = ctypes.c_wchar_p()
        if not advapi.ConvertSidToStringSidW(user, ctypes.byref(text)):
            return False, ""
        sid = str(text.value)
        kernel.LocalFree(text)
        owner = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        found = advapi.GetNamedSecurityInfoW(
            str(path),
            FILE_OBJECT,
            OWNER_INFORMATION,
            ctypes.byref(owner),
            None,
            None,
            None,
            ctypes.byref(descriptor),
        )
        if found != 0:
            return False, sid
        try:
            return any(advapi.EqualSid(owner, known) for known in accepted), sid
        finally:
            kernel.LocalFree(descriptor)
    finally:
        kernel.CloseHandle(token)


def _token_sid(advapi: Any, token: Any, kind: int) -> tuple[Any, Any] | None:
    size = wintypes.DWORD()
    advapi.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
    if not size.value:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if not advapi.GetTokenInformation(token, kind, buffer, size, ctypes.byref(size)):
        return None
    return buffer, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]


def protect_windows_home(path: Path) -> bool:
    mine, sid = _windows_owner(path)
    if not mine or not sid:
        return False
    grants = [f"{principal}:(OI)(CI)F" for principal in (f"*{sid}", OWNER_RIGHTS, LOCAL_SYSTEM)]
    done = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", *grants],
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return done.returncode == 0


def _join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def _device(path: Path) -> int | None:
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def _slot_name(origin: Path) -> str:
    digest = hashlib.sha256(str(origin).casefold().encode("utf-8")).hexdigest()[:8]
    return f"{origin.name or 'project'}-{digest}"


def _stamp(path: str) -> tuple[int, int]:
    stats = os.stat(path, follow_symlinks=False)
    return stats.st_size, stats.st_mtime_ns


def _runs_as_program(mode: int) -> bool:
    return os.name != "nt" and bool(mode & stat.S_IXUSR)


def _digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as reader:
        for chunk in iter(lambda: reader.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _settings_digest(path: Path) -> str:
    raw = path.read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return hashlib.sha256(raw).hexdigest()
    kept = {key: value for key, value in data.items() if key not in UNGUARDED_SETTINGS}
    canonical = json.dumps(kept, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def state_hashes(origin: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    settings = origin / STATE_DIR / STATE_SETTINGS
    if settings.is_file():
        with suppress(OSError):
            found[f"{STATE_DIR}/{STATE_SETTINGS}"] = _settings_digest(settings)
    try:
        folders = sorted(os.scandir(_long(origin / TRIALS_DIR)), key=lambda item: item.name)
    except OSError:
        return found
    for folder in folders:
        if not folder.is_dir(follow_symlinks=False):
            continue
        for name in GUARDED_TRIAL_FILES:
            with suppress(OSError):
                found[f"{TRIALS_DIR}/{folder.name}/{name}"] = _digest(
                    os.path.join(folder.path, name)
                )
    return found


def _entry_is_dir(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


class _Copier:
    def __init__(self, origin: Path, root: Path, reuse: bool = False) -> None:
        self.origin = origin
        self.root = root
        self.base: dict[str, FileStat] = {}
        self.hidden: dict[str, tuple[int, int]] = {}
        self.fingerprint: dict[str, tuple[int, int]] = {}
        self.linked: list[str] = []
        self.skipped: list[str] = []
        self.caches: list[str] = []
        self.outputs: list[str] = []
        self.unreadable: list[str] = []
        self.pending: list[tuple[str, str, bool, bool]] = []
        self.roots: list[tuple[Path, Path]] = [(_real(origin), root)]
        self.linked_files = 0
        self.copied_files = 0
        self.copied_bytes = 0
        self.rules = IgnoreRules()
        self.reuse = reuse
        self.visited: set[str] = set()

    def run(self, rules: IgnoreRules) -> None:
        self.rules = rules
        stack: list[tuple[str, bool]] = [("", False)]
        while stack:
            folder, ignored = stack.pop()
            source = self.origin / folder if folder else self.origin
            marker = source / GITIGNORE
            try:
                if marker.is_file():
                    text = marker.read_text(encoding="utf-8", errors="replace")
                    self.rules = self.rules.with_file(folder, text)
                entries = sorted(os.scandir(_long(source)), key=lambda item: item.name)
            except OSError as error:
                self._unreadable(folder, ignored, error)
                continue
            for entry in entries:
                relative = _join(folder, entry.name)
                try:
                    self._entry(entry, relative, ignored, stack)
                except OSError as error:
                    hidden = ignored or self.rules.matches(relative, _entry_is_dir(entry))
                    self._unreadable(relative, hidden, error)
        for relative, target, junction, directory in self.pending:
            self._make_link(relative, target, junction, directory)

    def _unreadable(self, relative: str, hidden: bool, error: OSError) -> None:
        if relative and isinstance(error, FileNotFoundError):
            return
        if hidden and relative and error.errno not in DISK_ERRORS:
            self.unreadable.append(relative)
            with suppress(OSError):
                os.unlink(_long(self.root / relative))
            return
        raise EnvironmentFailure(
            f"cannot copy {relative or 'the project'} into the isolated copy ({error.strerror})",
            "close the program that holds it, free disk space, or add it to .gitignore",
        ) from error

    def _entry(
        self,
        entry: os.DirEntry[str],
        relative: str,
        ignored: bool,
        stack: list[tuple[str, bool]],
    ) -> None:
        linked = _is_link(entry)
        is_dir = entry.is_dir() if linked else entry.is_dir(follow_symlinks=False)
        action = copy_action(relative, is_dir)
        if action is CopyAction.SKIP:
            return
        self.visited.add(relative)
        if action is CopyAction.LINK:
            if linked:
                self.roots.append((_real(entry.path), self.root / relative))
            try:
                self._farm(relative)
            except OSError as error:
                raise EnvironmentFailure(
                    f"cannot link {relative} into the isolated copy ({error.strerror})",
                    "check that the folder is readable, or run npm ci in the project",
                ) from error
            return
        if linked:
            self._relink(entry, relative)
            return
        hidden = ignored or self.rules.matches(relative, is_dir)
        if is_dir and hidden and entry.name.casefold() in BUILD_OUTPUTS:
            self.outputs.append(relative)
            self.visited.discard(relative)
            return
        if is_dir:
            os.makedirs(_long(self.root / relative), exist_ok=self.reuse)
            stack.append((relative, hidden))
        elif stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
            self._copy(entry, relative, hidden)
        else:
            self.skipped.append(relative)

    def _copy(self, entry: os.DirEntry[str], relative: str, hidden: bool) -> None:
        target = _long(self.root / relative)
        if self.reuse and os.path.isfile(target) and not os.path.islink(target):
            checksum = _digest(entry.path)
            if checksum == _digest(target):
                shutil.copystat(entry.path, target)
                stamp = _stamp(target)
                if tracked(relative):
                    if hidden:
                        self.hidden[relative] = stamp
                    else:
                        self.base[relative] = FileStat(
                            checksum, stamp[0], stamp[1], _runs_as_program(os.stat(target).st_mode)
                        )
                return
        digest = hashlib.sha256()
        size = 0
        with open(entry.path, "rb") as reader, open(target, "wb") as writer:
            for chunk in iter(lambda: reader.read(CHUNK), b""):
                if not hidden:
                    digest.update(chunk)
                writer.write(chunk)
                size += len(chunk)
        shutil.copystat(entry.path, target)
        self.copied_files += 1
        self.copied_bytes += size
        if not tracked(relative):
            return
        stamp = _stamp(target)
        if hidden:
            self.hidden[relative] = stamp
        else:
            mode = os.stat(target).st_mode
            self.base[relative] = FileStat(
                digest.hexdigest(), size, stamp[1], _runs_as_program(mode)
            )

    def _mapped(self, real: Path) -> Path | None:
        for source, destination in reversed(self.roots):
            if real == source or real.is_relative_to(source):
                return destination / real.relative_to(source)
        return None

    def _relink(self, entry: os.DirEntry[str], relative: str) -> None:
        if copy_action(relative, False) is CopyAction.SKIP:
            return
        try:
            text = os.readlink(entry.path)
            mapped = self._mapped(_real(entry.path))
        except OSError:
            mapped = None
        if mapped is None:
            self.skipped.append(relative)
            return
        junction = entry.is_junction()
        keep_text = not junction and not Path(_plain(text)).is_absolute()
        target = _plain(text) if keep_text else str(mapped)
        self.pending.append((relative, target, junction, entry.is_dir()))

    def _make_link(self, relative: str, target: str, junction: bool, directory: bool) -> None:
        link = _long(self.root / relative)
        try:
            if self.reuse and os.path.lexists(link):
                if os.path.isdir(link) and (os.name == "nt" or not os.path.islink(link)):
                    os.rmdir(link)
                else:
                    os.unlink(link)
            if junction:
                _junction(target, link)
            else:
                os.symlink(target, link, target_is_directory=directory)
        except OSError:
            self.skipped.append(relative)

    def _farm(self, relative: str) -> None:
        self.linked.append(relative)
        stack = [relative]
        os.makedirs(_long(self.root / relative), exist_ok=self.reuse)
        while stack:
            folder = stack.pop()
            for entry in os.scandir(_long(self.origin / folder)):
                inner = _join(folder, entry.name)
                destination = _long(self.root / inner)
                self.visited.add(inner)
                if _is_link(entry):
                    self._relink(entry, inner)
                elif entry.is_dir(follow_symlinks=False):
                    if dependency_cache(inner):
                        self.caches.append(inner)
                        self.visited.discard(inner)
                        continue
                    os.makedirs(destination, exist_ok=self.reuse)
                    stack.append(inner)
                elif stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
                    self.fingerprint[inner] = _stamp(entry.path)
                    self._hard_link(entry.path, destination)
                    self.linked_files += 1
                else:
                    self.skipped.append(inner)

    def _hard_link(self, source: str, destination: str) -> None:
        try:
            if self.reuse and os.path.exists(destination):
                if os.path.samefile(source, destination):
                    return
                os.unlink(destination)
            os.link(source, destination)
        except OSError as error:
            raise EnvironmentFailure(
                f"cannot hard-link node_modules into the isolated copy ({error.strerror})",
                "the copy needs a folder on the project's drive that supports hard links: "
                "set TEMP (Windows) or TMPDIR to a folder on that drive",
            ) from error


def fingerprint(origin: Path, linked: Sequence[str]) -> dict[str, tuple[int, int]]:
    found: dict[str, tuple[int, int]] = {}
    stack = list(linked)
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(_long(origin / folder)))
        except OSError:
            continue
        for entry in entries:
            inner = _join(folder, entry.name)
            if _is_link(entry):
                continue
            if entry.is_dir(follow_symlinks=False):
                if not dependency_cache(inner):
                    stack.append(inner)
                continue
            try:
                found[inner] = _stamp(entry.path)
            except OSError:
                continue
    return found


def _editable_targets(site: Path) -> list[Path]:
    targets: list[Path] = []
    for pth in sorted(site.glob("*.pth")):
        text = pth.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            entry = line.strip()
            if entry and not entry.startswith(("#", "import")) and Path(entry).is_absolute():
                targets.append(Path(entry))
    for finder in sorted(site.glob("__editable___*_finder.py")):
        text = finder.read_text(encoding="utf-8", errors="replace")
        found = EDITABLE_MAPPING.search(text)
        if found is None:
            continue
        with suppress(ValueError, SyntaxError):
            mapping = ast.literal_eval(found.group(1))
            if isinstance(mapping, dict):
                targets.extend(Path(str(value)).parent for value in mapping.values())
    return targets


def python_path(origin: Path, root: Path) -> str:
    real_origin = _real(origin)
    found: list[str] = []
    active = os.environ.get("VIRTUAL_ENV")
    venvs = [origin / ".venv", origin / "venv", *([Path(active)] if active else [])]
    for base in venvs:
        if not base.is_dir():
            continue
        sites = [*base.glob("Lib/site-packages"), *base.glob("lib/python*/site-packages")]
        for site in sites:
            for target in _editable_targets(site):
                real = _real(target)
                if not real.is_relative_to(real_origin):
                    continue
                mapped = str(root / real.relative_to(real_origin))
                if mapped not in found:
                    found.append(mapped)
    if not found:
        return ""
    inherited = os.environ.get("PYTHONPATH", "")
    return os.pathsep.join([*found, inherited] if inherited else found)


def outside_dependencies(origin: Path) -> tuple[str, ...]:
    found: list[str] = []
    current = origin
    while current.parent != current and not (current / ".git").exists():
        current = current.parent
        for name in LINKED_DIRS:
            if (current / name).is_dir():
                found.append(str(current / name))
    return tuple(found)


def _root_rules(origin: Path) -> IgnoreRules:
    return root_rules(origin, Path.home(), os.environ)


def _base_dirs(base: Sequence[str]) -> frozenset[str]:
    folders: set[str] = set()
    for path in base:
        parts = path.split("/")
        folders.update("/".join(parts[:depth]) for depth in range(1, len(parts)))
    return frozenset(folders)


class LocalSandbox:
    def __init__(
        self,
        parents: Sequence[Path] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        now_iso: Callable[[], str] | None = None,
    ) -> None:
        self._parents = tuple(parents) if parents is not None else None
        self._clock = clock
        self._now_iso = now_iso

    def _volume_home(self, origin: Path) -> Path:
        base = Path(origin.anchor) if os.name == "nt" else origin.parent
        return base / VOLUME_DIR

    def _candidates(self, origin: Path) -> tuple[Path, ...]:
        parents = self._parents if self._parents is not None else default_parents()
        extra = (self._volume_home(origin),) if self._parents is None else ()
        return (*(parent / SANDBOX_DIR for parent in parents), *extra)

    def _prepared(self, candidate: Path, shared: bool) -> bool:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.mkdir(mode=PRIVATE, exist_ok=True)
            if shared and os.name == "nt":
                return protect_windows_home(candidate)
            return _private(candidate)
        except OSError:
            return False

    def _home(self, origin: Path) -> Path:
        wanted = _device(origin)
        needs_links = any((origin / name).is_dir() for name in LINKED_DIRS)
        writable: list[Path] = []
        shared = self._volume_home(origin)
        for candidate in self._candidates(origin):
            if candidate.resolve().is_relative_to(origin):
                continue
            if not self._prepared(candidate, candidate == shared):
                continue
            writable.append(candidate)
            if _device(candidate) == wanted:
                return candidate.resolve()
        if writable and not needs_links:
            return writable[0].resolve()
        raise EnvironmentFailure(
            "no folder on the project's drive can hold the isolated copy",
            "node_modules is shared through hard links, which need the same drive: "
            "set TEMP (Windows) or TMPDIR to a folder on the project's drive",
        )

    def _sweep(self, home: Path, origin: Path) -> None:
        name = _slot_name(origin)
        try:
            slots = [slot for slot in home.iterdir() if slot.name.startswith(name)]
        except OSError:
            return
        for slot in slots:
            try:
                marker = json.loads((slot / MARKER).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                not isinstance(marker, dict)
                or marker.get("kept")
                or marker.get("warm")
                or _alive(marker.get("pid"))
            ):
                continue
            with suppress(OSError, ValueError):
                self._delete(slot, origin, strict=True)

    def keep(self, copy: SandboxCopy) -> None:
        marker = copy.slot / MARKER
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"origin": str(copy.origin)}
        if isinstance(data, dict):
            data["kept"] = True
            marker.write_text(json.dumps(data), encoding="utf-8")

    def _slot(self, home: Path, origin: Path) -> Path:
        self._sweep(home, origin)
        name = _slot_name(origin)
        for index in range(1, 1000):
            slot = home / (name if index == 1 else f"{name}-{index}")
            try:
                slot.mkdir(mode=PRIVATE)
            except FileExistsError:
                continue
            return slot
        raise EnvironmentFailure(f"too many isolated copies in {home}", "remove old ones")

    def create(self, origin: Path) -> SandboxCopy:
        origin = origin.resolve()
        started = self._clock()
        slot = self._slot(self._home(origin), origin)
        root = slot / (origin.name or "project")
        marker = {"origin": str(origin), "pid": os.getpid()}
        if self._now_iso is not None:
            marker["created_at"] = self._now_iso()
        (slot / MARKER).write_text(json.dumps(marker), encoding="utf-8")
        copier = _Copier(origin, root)
        root_rules = _root_rules(origin)
        try:
            os.mkdir(_long(root))
            copier.run(root_rules)
            state = state_hashes(origin)
        except BaseException:
            self._delete(slot, origin, strict=False)
            raise
        return SandboxCopy(
            origin=origin,
            root=root,
            slot=slot,
            base=copier.base,
            rules=copier.rules,
            linked=tuple(copier.linked),
            linked_files=copier.linked_files,
            copied_files=copier.copied_files,
            copied_bytes=copier.copied_bytes,
            seconds=self._clock() - started,
            skipped_links=tuple(copier.skipped),
            fingerprint=copier.fingerprint,
            root_rules=root_rules,
            hidden=copier.hidden,
            skipped_caches=tuple(copier.caches),
            skipped_outputs=tuple(copier.outputs),
            outside_dependencies=outside_dependencies(origin),
            python_path=python_path(origin, root),
            unreadable=tuple(copier.unreadable),
            state=state,
        )

    def manifest(self, copy: SandboxCopy) -> dict[str, str]:
        found: dict[str, str] = {}
        folders = _base_dirs(tuple(copy.base))
        stack: list[tuple[str, bool, IgnoreRules]] = [("", False, copy.root_rules)]
        while stack:
            folder, ignored, rules = stack.pop()
            current = copy.root / folder if folder else copy.root
            marker = current / GITIGNORE
            if marker.is_file():
                text = marker.read_text(encoding="utf-8", errors="replace")
                rules = rules.with_file(folder, text)
            try:
                entries = list(os.scandir(_long(current)))
            except OSError:
                continue
            for entry in entries:
                relative = _join(folder, entry.name)
                if _is_link(entry) or not tracked(relative):
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                hidden = ignored or rules.matches(relative, is_dir)
                if is_dir:
                    if not hidden or relative in folders:
                        stack.append((relative, hidden, rules))
                elif relative in copy.base or (
                    not hidden and stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode)
                ):
                    found[relative] = self._hash(entry, copy.base.get(relative))
        return found

    def _hash(self, entry: os.DirEntry[str], known: FileStat | None) -> str:
        stats = entry.stat(follow_symlinks=False)
        if known is not None and (stats.st_size, stats.st_mtime_ns) == (
            known.size,
            known.mtime_ns,
        ):
            return known.sha256
        return _digest(entry.path)

    def mode_changes(self, copy: SandboxCopy) -> tuple[str, ...]:
        if os.name == "nt":
            return ()
        changed: list[str] = []
        for relative, known in copy.base.items():
            try:
                mode = os.stat(_long(copy.root / relative)).st_mode
            except OSError:
                continue
            if _runs_as_program(mode) != known.executable:
                changed.append(relative)
        return tuple(sorted(changed))

    def state_changed(self, copy: SandboxCopy) -> tuple[str, ...]:
        now = state_hashes(copy.origin)
        before = copy.state
        changed = {path for path in set(now) | set(before) if now.get(path) != before.get(path)}
        return tuple(sorted(changed))

    def ignored_changes(self, copy: SandboxCopy) -> tuple[str, ...]:
        changed: list[str] = []
        for relative, before in copy.hidden.items():
            try:
                now = _stamp(_long(copy.root / relative))
            except OSError:
                changed.append(relative)
                continue
            if now != before:
                changed.append(relative)
        return tuple(sorted(changed))

    def executable(self, copy: SandboxCopy, relative: str) -> bool:
        if os.name == "nt":
            return False
        try:
            return bool(os.stat(_long(copy.root / relative)).st_mode & stat.S_IXUSR)
        except OSError:
            return False

    def read(self, copy: SandboxCopy, relative: str) -> bytes | None:
        try:
            with open(_long(copy.root / relative), "rb") as reader:
                return reader.read()
        except OSError:
            return None

    def dependencies_changed(self, copy: SandboxCopy) -> tuple[str, ...]:
        now = fingerprint(copy.origin, copy.linked)
        before = copy.fingerprint
        changed = {path for path in set(now) | set(before) if now.get(path) != before.get(path)}
        return tuple(sorted(changed))

    def remove(self, copy: SandboxCopy) -> bool:
        return self._delete(copy.slot, copy.origin, strict=True)

    def _owned(self, slot: Path, origin: Path) -> bool:
        try:
            marker = json.loads((slot / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        homes = {candidate.resolve() for candidate in self._candidates(origin)}
        return (
            isinstance(marker, dict)
            and marker.get("origin") == str(origin)
            and slot.resolve().parent in homes
            and slot.name.startswith(_slot_name(origin))
        )

    def _delete(self, slot: Path, origin: Path, strict: bool) -> bool:
        if strict and not self._owned(slot, origin):
            raise ValueError(f"refusing to delete {slot}: not an isolated copy of {origin}")
        remover = _Remover(slot / (origin.name or "project"), origin)
        for delay in (*REMOVE_RETRIES, None):
            remover.tree(slot)
            if not slot.exists() or delay is None:
                break
            time.sleep(delay)
        return not slot.exists()


def _gone(action: Callable[[], None]) -> bool:
    try:
        action()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _unlink_link(entry: os.DirEntry[str]) -> None:
    if entry.is_junction() or (sys.platform == "win32" and entry.is_dir()):
        os.rmdir(entry.path)
    else:
        os.unlink(entry.path)


def _open_folder(folder: Path) -> None:
    if os.name == "nt":
        return
    with suppress(OSError):
        mode = os.lstat(folder).st_mode
        if stat.S_ISDIR(mode) and mode & stat.S_IRWXU != stat.S_IRWXU:
            os.chmod(folder, mode | stat.S_IRWXU)


class _Remover:
    def __init__(self, root: Path, origin: Path) -> None:
        self._root = root
        self._origin = origin

    def tree(self, folder: Path) -> None:
        _open_folder(folder)
        try:
            entries = list(os.scandir(_long(folder)))
        except OSError:
            return
        for entry in entries:
            if _is_link(entry):
                _gone(partial(_unlink_link, entry))
            elif entry.is_dir(follow_symlinks=False):
                self.tree(folder / entry.name)
            else:
                self._file(entry.path, folder / entry.name)
        _gone(partial(os.rmdir, _long(folder)))

    def _file(self, target: str, path: Path) -> None:
        if _gone(partial(os.unlink, target)):
            return
        try:
            mode = os.stat(target).st_mode
        except OSError:
            return
        if mode & stat.S_IWRITE:
            return
        original = self._original(path)
        shared = original is not None and self._same(target, original)
        os.chmod(target, mode | stat.S_IWRITE)
        removed = _gone(partial(os.unlink, target))
        if shared and original is not None:
            _gone(partial(os.chmod, _long(original), stat.S_IMODE(mode)))
        if not removed:
            _gone(partial(os.chmod, target, stat.S_IMODE(mode)))

    def _same(self, target: str, original: Path) -> bool:
        try:
            return os.path.samefile(target, _long(original))
        except OSError:
            return False

    def _original(self, path: Path) -> Path | None:
        if not path.is_relative_to(self._root):
            return None
        relative = path.relative_to(self._root)
        if not any(part.casefold() in LINKED_DIRS for part in relative.parts[:-1]):
            return None
        return self._origin / relative
