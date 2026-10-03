from __future__ import annotations

from typing import TYPE_CHECKING

from typer.core import TyperCommand

if TYPE_CHECKING:
    from typer._click import Context, HelpFormatter

WHAT_TO_DO = "What to do"
HOW_TO_RUN = "How to run it"
LIMITS = "Limits"
ADVANCED = "Advanced"
PANELS = (WHAT_TO_DO, HOW_TO_RUN, LIMITS, ADVANCED)
ARGUMENTS = "Arguments"
NAME_WIDTH = 30


class PanelCommand(TyperCommand):
    def format_options(self, ctx: Context, formatter: HelpFormatter) -> None:
        sections: dict[str, list[tuple[str, str]]] = {title: [] for title in (ARGUMENTS, *PANELS)}
        for param in self.get_params(ctx):
            record = param.get_help_record(ctx)
            if record is None:
                continue
            panel = getattr(param, "rich_help_panel", None)
            if param.param_type_name == "argument":
                title = ARGUMENTS
            else:
                title = panel if isinstance(panel, str) and panel else ADVANCED
            sections.setdefault(title, []).append(record)
        names = [name for rows in sections.values() for name, _ in rows]
        width = min(NAME_WIDTH, max(map(len, names), default=0))
        for title, rows in sections.items():
            if rows:
                with formatter.section(title):
                    formatter.write_dl([(name.ljust(width), text) for name, text in rows])
