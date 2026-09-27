from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from cuanta.cli.runtime import global_options

mcp_app = typer.Typer(help="Serve the local code index over MCP stdio.")


@mcp_app.command("serve")
def serve_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Option("--run-id", help="Run identity for tool metrics.")] = "",
) -> None:
    from cuanta.bootstrap import Container

    options = global_options(ctx)
    container = Container.for_project((options.project or Path.cwd()).resolve())
    try:
        server = container.mcp_server(run_id or os.environ.get("CUANTA_RUN_ID", ""))
        server.serve(sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        raise typer.Exit(130) from None
    except Exception:
        print("cuanta MCP server failed", file=sys.stderr)
        raise typer.Exit(1) from None
    finally:
        container.close()
