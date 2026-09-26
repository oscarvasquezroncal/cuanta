from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def cold_home(language: str) -> None:
    from textual.widgets import DataTable

    from cuanta.tui.app import CuantaApp
    from cuanta.tui.views.home import RUN_COLUMNS
    from tests.tui.fakes import FakeServices

    assert "rich._emoji_codes" not in sys.modules

    async def main() -> None:
        app = CuantaApp(
            FakeServices(), language, "calico-dark", motion=False, environ={"WT_SESSION": "1"}
        )
        async with app.run_test(size=(120, 36)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.home_loaded
            table = app.query_one("#runs", DataTable)
            assert [column.label.plain for column in table.ordered_columns] == [
                app.catalog(f"home.{key}") for key in RUN_COLUMNS
            ]
            assert "rich._emoji_codes" not in sys.modules

    asyncio.run(main())


@pytest.mark.parametrize("language", ["en", "es"])
def test_cold_home_keeps_literal_headers_without_loading_emoji_codes(
    tmp_path: Path, language: str
) -> None:
    program = "\n".join(
        (
            "import sys",
            "sys.path.insert(0, sys.argv[1])",
            "from tests.tui.test_home_startup import cold_home",
            "cold_home(sys.argv[2])",
        )
    )
    result = subprocess.run(
        [sys.executable, "-c", program, str(ROOT), language],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
