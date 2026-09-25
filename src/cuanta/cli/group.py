from __future__ import annotations

GLOBAL_FLAGS = frozenset({"--plain", "--json", "--no-emoji", "--yes", "-y", "--verbose", "-v"})
GLOBAL_VALUED = frozenset({"--theme", "--project"})


def hoist_globals(args: list[str]) -> list[str]:
    hoisted: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            rest.extend(args[index:])
            break
        name = arg.split("=", 1)[0]
        if arg in GLOBAL_FLAGS or (name in GLOBAL_VALUED and "=" in arg):
            hoisted.append(arg)
        elif arg in GLOBAL_VALUED and index + 1 < len(args):
            hoisted.extend(args[index : index + 2])
            index += 1
        else:
            rest.append(arg)
        index += 1
    return hoisted + rest
