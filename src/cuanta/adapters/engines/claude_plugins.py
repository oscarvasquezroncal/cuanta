from __future__ import annotations

from pathlib import Path

from cuanta.domain.plugins import InstalledPlugin, parse_installed

INSTALLED = Path(".claude") / "plugins" / "installed_plugins.json"


def installed_plugins(home: Path) -> tuple[InstalledPlugin, ...]:
    path = home / INSTALLED
    try:
        return parse_installed(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
