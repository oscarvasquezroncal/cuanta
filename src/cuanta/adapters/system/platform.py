from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def config_dir(environ: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("CUANTA_CONFIG_DIR")
    if override:
        return Path(override)
    system = platform or sys.platform
    home = Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    if system.startswith("win"):
        base = env.get("APPDATA")
        return Path(base) / "cuanta" if base else home / "AppData" / "Roaming" / "cuanta"
    if system == "darwin":
        return home / "Library" / "Application Support" / "cuanta"
    xdg = env.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else home / ".config") / "cuanta"


def home_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("CUANTA_HOME")
    if override:
        return Path(override)
    return Path.home()


def is_windows() -> bool:
    return sys.platform.startswith("win")
