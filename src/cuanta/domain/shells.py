from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class Shell(StrEnum):
    CMD = "cmd"
    POWERSHELL = "pwsh"
    BASH = "bash"
    ZSH = "zsh"
    FISH = "fish"
    UNKNOWN = "unknown"


NAMES = {
    "cmd": Shell.CMD,
    "cmd.exe": Shell.CMD,
    "powershell": Shell.POWERSHELL,
    "powershell.exe": Shell.POWERSHELL,
    "pwsh": Shell.POWERSHELL,
    "pwsh.exe": Shell.POWERSHELL,
    "bash": Shell.BASH,
    "bash.exe": Shell.BASH,
    "sh": Shell.BASH,
    "sh.exe": Shell.BASH,
    "zsh": Shell.ZSH,
    "fish": Shell.FISH,
}


def shell_from_name(name: str) -> Shell:
    return NAMES.get(name.strip().lower(), Shell.UNKNOWN)


def assignment(name: str, value: str, shell: Shell) -> str:
    if shell is Shell.CMD:
        return f"set {name}={value}"
    if shell is Shell.POWERSHELL:
        return f"$env:{name} = '{value}'"
    if shell is Shell.FISH:
        return f"set -gx {name} '{value}'"
    if shell in {Shell.BASH, Shell.ZSH}:
        return f"export {name}='{value}'"
    raise ValueError(f"unsupported shell: {shell}")


def snippet(env: Mapping[str, str], shell: Shell) -> str:
    return "\n".join(assignment(name, value, shell) for name, value in env.items())


def env_hint(name: str, value: str, shell: Shell) -> str:
    if shell is Shell.UNKNOWN:
        cmd = assignment(name, value, Shell.CMD)
        pwsh = assignment(name, value, Shell.POWERSHELL)
        posix = assignment(name, value, Shell.BASH)
        return f"cmd: {cmd} · PowerShell: {pwsh} · bash/zsh: {posix}"
    return assignment(name, value, shell)
