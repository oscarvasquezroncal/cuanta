from __future__ import annotations

import json
import os
import stat
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from cuanta.adapters.system.sandbox import (
    MARKER,
    LocalSandbox,
    _alive,
    _Copier,
    _digest,
    _is_link,
    _long,
    _plain,
    _Remover,
    _root_rules,
    _slot_name,
    _unlink_link,
    outside_dependencies,
    python_path,
    state_hashes,
)
from cuanta.domain.errors import EnvironmentFailure
from cuanta.domain.sandbox import CopyAction, copy_action, dependency_cache
from cuanta.ports.sandbox import SandboxCopy

WARM_STATE = "warm.json"
LEASE = "lease.json"


def _tree_state(root: Path, source: bool = False) -> dict[str, str]:
    found: dict[str, str] = {}
    stack = [root]
    while stack:
        folder = stack.pop()
        for entry in os.scandir(_long(folder)):
            relative = Path(_plain(entry.path)).relative_to(root).as_posix()
            directory = entry.is_dir(follow_symlinks=False)
            if source and (
                copy_action(relative, directory) is CopyAction.SKIP or dependency_cache(relative)
            ):
                continue
            if _is_link(entry):
                found[relative] = f"link:{os.readlink(entry.path)}"
            elif directory:
                found[relative] = "directory"
                stack.append(Path(_plain(entry.path)))
            else:
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    found[relative] = f"special:{info.st_mode}"
                    continue
                linked = any(
                    copy_action(part, True) is CopyAction.LINK for part in relative.split("/")
                )
                content = str((info.st_size, info.st_mtime_ns)) if linked else _digest(entry.path)
                found[relative] = f"file:{content}:{info.st_mode}:{info.st_ino}"
    return found


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid sandbox state")
    return value


class WarmSandbox(LocalSandbox):
    def _pointer(self, origin: Path) -> Path:
        return self._home(origin) / f"{_slot_name(origin)}-warm.json"

    def _lease(self, slot: Path) -> bool:
        path = slot / LEASE
        try:
            with path.open("x", encoding="utf-8") as writer:
                json.dump({"pid": os.getpid()}, writer)
        except FileExistsError:
            return False
        return True

    def _marker(self, slot: Path, **values: object) -> None:
        path = slot / MARKER
        path.write_text(json.dumps({**_json(path), **values}), encoding="utf-8")

    def create(self, origin: Path) -> SandboxCopy:
        origin = origin.resolve()
        started = self._clock()
        pointer = self._pointer(origin)
        slot: Path | None = None
        try:
            target = _json(pointer).get("slot")
            if not isinstance(target, str):
                raise ValueError("invalid warm copy pointer")
            slot = Path(target)
            if not self._owned(slot, origin):
                return super().create(origin)
            marker = _json(slot / MARKER)
            if marker.get("kept"):
                pointer.unlink()
                return super().create(origin)
            if not marker.get("warm") and not _alive(marker.get("pid")):
                self._marker(slot, kept=True)
                pointer.unlink()
                return super().create(origin)
            if not self._lease(slot):
                return super().create(origin)
            root = slot / (origin.name or "project")
            saved = _json(slot / WARM_STATE)
            if saved.get("tree") != _tree_state(root):
                raise ValueError("warm copy drift")
            copy = self._sync(origin, root, slot)
            self._marker(slot, warm=False, pid=os.getpid())
            return replace(copy, seconds=self._clock() - started, reused=True)
        except (OSError, ValueError, EnvironmentFailure):
            if slot is not None and self._owned(slot, origin):
                with suppress(OSError, ValueError):
                    self._delete(slot, origin, strict=True)
            with suppress(OSError):
                pointer.unlink()
        return super().create(origin)

    def keep(self, copy: SandboxCopy) -> None:
        super().keep(copy)
        pointer = self._pointer(copy.origin)
        with suppress(OSError, ValueError):
            if _json(pointer).get("slot") == str(copy.slot):
                pointer.unlink()

    def _safe_target(self, copier: _Copier, relative: str, directory: bool) -> None:
        path = copier.root / relative
        if not os.path.lexists(_long(path)):
            return
        info = os.lstat(_long(path))
        if stat.S_ISLNK(info.st_mode) or path.is_junction():
            with os.scandir(_long(path.parent)) as entries:
                entry = next(entry for entry in entries if entry.name == path.name)
                _unlink_link(entry)
        elif stat.S_ISDIR(info.st_mode) != directory:
            if stat.S_ISDIR(info.st_mode):
                _Remover(copier.root, copier.origin).tree(path)
            else:
                path.unlink()
        elif not directory and info.st_nlink > 1:
            path.unlink()

    def _sync(self, origin: Path, root: Path, slot: Path) -> SandboxCopy:
        if root.is_symlink() or root.is_junction() or not root.is_dir():
            raise ValueError("warm copy root is not an isolated directory")
        before = _tree_state(origin, source=True)
        self._unlink_copy_links(root)
        copier = _WarmCopier(origin, root, self)
        rules = _root_rules(origin)
        copier.run(rules)
        self._prune(root, origin, copier.visited)
        if before != _tree_state(origin, source=True):
            raise ValueError("project changed during warm synchronization")
        for relative in copier.base.keys() | copier.hidden.keys():
            source = origin / relative
            target = root / relative
            if (
                source.is_symlink()
                or target.is_symlink()
                or _digest(_long(source)) != _digest(_long(target))
            ):
                raise ValueError("warm copy content failed verification")
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
            skipped_links=tuple(copier.skipped),
            fingerprint=copier.fingerprint,
            root_rules=rules,
            hidden=copier.hidden,
            skipped_caches=tuple(copier.caches),
            skipped_outputs=tuple(copier.outputs),
            outside_dependencies=outside_dependencies(origin),
            python_path=python_path(origin, root),
            unreadable=tuple(copier.unreadable),
            state=state_hashes(origin),
        )

    def _unlink_copy_links(self, root: Path) -> None:
        stack = [root]
        while stack:
            folder = stack.pop()
            for entry in os.scandir(_long(folder)):
                if _is_link(entry):
                    _unlink_link(entry)
                elif entry.is_dir(follow_symlinks=False):
                    stack.append(Path(_plain(entry.path)))

    def _prune(self, root: Path, origin: Path, visited: set[str]) -> None:
        stack = [root]
        remover = _Remover(root, origin)
        while stack:
            folder = stack.pop()
            for entry in os.scandir(_long(folder)):
                path = Path(_plain(entry.path))
                relative = str(path.relative_to(root)).replace("\\", "/")
                linked = _is_link(entry)
                directory = entry.is_dir(follow_symlinks=False)
                if relative not in visited:
                    if linked:
                        _unlink_link(entry)
                    elif directory:
                        remover.tree(path)
                    else:
                        remover._file(entry.path, path)
                    if os.path.lexists(_long(path)):
                        raise OSError("warm copy retained a stale path")
                elif directory and not linked:
                    stack.append(path)

    def release(self, origin: Path, slot: Path) -> bool:
        origin = origin.resolve()
        if not self._owned(slot, origin):
            raise ValueError("refusing to recycle an unowned copy")
        pointer = self._pointer(origin)
        try:
            current = _json(pointer).get("slot") if pointer.exists() else None
            if current is not None and current != str(slot):
                return self._delete(slot, origin, strict=True)
            self._marker(slot, pid=os.getpid())
            (slot / LEASE).write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
            root = slot / (origin.name or "project")
            self._sync(origin, root, slot)
            (slot / WARM_STATE).write_text(
                json.dumps({"tree": _tree_state(root)}), encoding="utf-8"
            )
            self._marker(slot, warm=True, pid=0)
            if current is None:
                try:
                    with pointer.open("x", encoding="utf-8") as writer:
                        json.dump({"slot": str(slot)}, writer)
                except FileExistsError:
                    return self._delete(slot, origin, strict=True)
            (slot / LEASE).unlink()
            return True
        except (OSError, ValueError, EnvironmentFailure):
            with suppress(OSError):
                if pointer.exists() and _json(pointer).get("slot") == str(slot):
                    pointer.unlink()
            return self._delete(slot, origin, strict=True)


class _WarmCopier(_Copier):
    def __init__(self, origin: Path, root: Path, sandbox: WarmSandbox) -> None:
        super().__init__(origin, root, reuse=True)
        self._sandbox = sandbox

    def _entry(
        self,
        entry: os.DirEntry[str],
        relative: str,
        ignored: bool,
        stack: list[tuple[str, bool]],
    ) -> None:
        directory = entry.is_dir(follow_symlinks=False)
        if copy_action(relative, directory) is not CopyAction.SKIP:
            self._sandbox._safe_target(self, relative, directory)
        super()._entry(entry, relative, ignored, stack)
