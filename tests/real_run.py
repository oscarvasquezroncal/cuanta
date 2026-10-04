from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from cuanta.adapters.forge.installer import ForgeInstaller

REAL_RUN_ID = "01M3YMH7MKDDJTDYZ122B4BD08"
REAL_MODEL = "claude-opus-5-5"
SESSION = "SESSION"
MANDATES = Path(__file__).parent / "fixtures" / "mandates"
PHASES_LINE = "This mandate has 5 phases; the forecast covers one change"
FASES_LINE = "Este mandato tiene 5 fases; el pronóstico cubre un cambio"
REAL_RUN_NOISE = (".venv312", ".venv_ci", ".odoo_ref", ".deploy-backups", "tmp")
REAL_RUN_GITIGNORE = ".venv312/\n.odoo_ref/\n.deploy-backups/\ntmp/\n"
REAL_RUN_SOURCES = {
    "creator/__init__.py": "",
    "creator/routers/consult.py": "def consult():\n    return 1\n",
    "creator/services/opus_interpreter.py": "def interpret():\n    return 2\n",
    "tests/test_consult.py": "def test_consult():\n    assert True\n",
    "docs/audits/README.md": "# Audits\n",
    "pyproject.toml": "[project]\nname = 'backend'\n",
    "README.md": "# Backend\n",
    "CLAUDE.md": "# Rules\n",
    "creator/CLAUDE.md": "# Creator rules\n",
}
REAL_RUN_NOISE_FILES = {
    ".venv312/pyvenv.cfg": "home = C:/Python312\n",
    ".venv312/Lib/site-packages/fastapi/__init__.py": "from .routing import APIRouter\n",
    ".venv312/Lib/site-packages/fastapi/routing.py": "class APIRouter:\n    pass\n",
    ".venv312/Lib/site-packages/fastapi/CLAUDE.md": "# vendored\n",
    ".venv_ci/pyvenv.cfg": "home = C:/Python312\n",
    ".venv_ci/Lib/site-packages/pytest/__init__.py": "def main():\n    return 0\n",
    ".odoo_ref/.git/HEAD": "ref: refs/heads/main\n",
    ".odoo_ref/addons/sale/models/sale.py": "class Sale:\n    pass\n",
    ".odoo_ref/addons/sale/static/x.js": "export const x = 1\n",
    ".deploy-backups/2026-10-01/creator/routers/consult.py": "def consult():\n    return 0\n",
    "tmp/run.json": "{}\n",
    "tmp/scratch.py": "print(1)\n",
}


FORGE_TEAM = ("architecture-analyst", "backend-senior", "tester", "docs-updater")
FORGE_GATEWAY = (
    "Run the suite with `cuanta test --json`; its output is bounded, never pipe it. "
    "Read failure detail with `cuanta cat <capsule> --level L2`.\n"
)
FORGE_TEMPLATE = FORGE_GATEWAY + "# Mandate\n\n```\n# MANDATE\n\n=== REQUEST ===\n\nTYPE: x\n```\n"
FORGE_DONE = ("0", "0.5", "1", "2A", "2B", "2.5", "3", "4", "5", "6")
FORGE_SUPPORT = {
    "AGENTS_GUIDE.md": "# Guide\n\n## Harness\n\n| Layer | Implemented by |\n",
    "HISTORIAS.md": "# Backlog\n",
    "docs/GROUND_TRUTH.md": "# Facts\n",
    "docs/CHANGELOG_INTERNAL.md": "# Changelog\n",
    "docs/FLAGS.md": "# Flags\n",
    "docs/RUN_LOG.md": "# Run log\n",
    "docs/IMPROVEMENTS.md": "# Improvements\n",
    "docs/LOOP.md": "# Loop\n",
}


def forged_rulebook(lines: int = 301) -> str:
    head = [
        "# Rules",
        "",
        "> **Over ceiling.** The project keeps more rules than the ceiling allows.",
        "> **No code graph, deliberately.** Structure lives in the code.",
        "",
    ]
    rules = [f"- rule {number}" for number in range(lines - len(head))]
    return "\n".join((*head, *rules)) + "\n"


def forged_project(
    root: Path, graph_mode: str = "none", template: bool = True, kit: bool = True
) -> Path:
    files = {**FORGE_SUPPORT, "CLAUDE.md": forged_rulebook()}
    for name in FORGE_TEAM:
        body = f"---\nname: {name}\ndescription: {name} of the backend\n---\nYou are {name}.\n"
        files[f".claude/agents/{name}.md"] = body + (FORGE_GATEWAY if name == "tester" else "")
    if template:
        files["docs/MANDATE_TEMPLATE.md"] = FORGE_TEMPLATE
    state = {
        "version": "0.3",
        "started_at": "2026-09-30T09:00:00Z",
        "size_tier": "large",
        "graph_mode": graph_mode,
        "docs_state": "exists",
        "forge_state": "fresh",
        "verify_tier": "strong",
        "phases_completed": list(FORGE_DONE),
        "dirs_written": ["creator"],
        "dirs_queued": [],
        "dirs_rejected": [],
        "unverified": [],
        "verify_markers": [],
    }
    files[".claude/forge-state.json"] = json.dumps(state, indent=2) + "\n"
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    if kit:
        ForgeInstaller(root).install()
    return root


def forged_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".cuanta" not in path.relative_to(root).parts
    }


def phased_mandate() -> str:
    return (MANDATES / "real_run_es.md").read_text(encoding="utf-8")


def real_project_tree(root: Path) -> tuple[str, ...]:
    files = {**REAL_RUN_SOURCES, **REAL_RUN_NOISE_FILES, ".gitignore": REAL_RUN_GITIGNORE}
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return tuple(sorted(REAL_RUN_SOURCES))


def usage(
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read: int = 0,
    cache_write: int = 0,
) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }


def init_line(model: str = REAL_MODEL) -> str:
    return json.dumps({"type": "system", "subtype": "init", "session_id": SESSION, "model": model})


def assistant_line(
    message_id: str,
    parent: str = "",
    tool: tuple[str, str, Mapping[str, object]] | None = None,
    model: str = REAL_MODEL,
    tokens: Mapping[str, int] | None = None,
) -> str:
    content: list[dict[str, object]] = []
    if tool is not None:
        name, tool_id, inputs = tool
        content.append({"type": "tool_use", "id": tool_id, "name": name, "input": dict(inputs)})
    else:
        content.append({"type": "text", "text": "working"})
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": message_id,
                "model": model,
                "content": content,
                "usage": dict(tokens) if tokens is not None else usage(),
            },
            "parent_tool_use_id": parent or None,
            "session_id": SESSION,
        }
    )


def result_line(
    text: str = "done",
    cost: float | None = 0.2,
    turns: int = 1,
    subtype: str = "success",
) -> str:
    data: dict[str, object] = {
        "type": "result",
        "subtype": subtype,
        "is_error": subtype != "success",
        "result": text,
        "num_turns": turns,
        "session_id": SESSION,
    }
    if cost is not None:
        data["total_cost_usd"] = cost
    return json.dumps(data)


def scout_spawn(message_id: str = "msg_main_spawn", tool_id: str = "toolu_scout") -> str:
    return assistant_line(
        message_id, tool=("Agent", tool_id, {"subagent_type": "scout", "prompt": "map"})
    )


def scout_read(message_id: str, path: str = "src/app.py", parent: str = "toolu_scout") -> str:
    return assistant_line(
        message_id, parent=parent, tool=("Read", f"toolu_{message_id}", {"file_path": path})
    )
