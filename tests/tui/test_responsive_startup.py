from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.screen import Screen

from cuanta.tui import app as app_module
from cuanta.tui.app import CuantaApp
from tests.tui.fakes import FakeServices


class StartupApp(CuantaApp):
    CSS_PATH = str(Path(app_module.__file__).with_name("cuanta.tcss"))

    def __init__(self) -> None:
        self.initial_classes: set[str] | None = None
        super().__init__(
            FakeServices(), "en", "calico-dark", motion=False, environ={"WT_SESSION": "1"}
        )

    def get_default_screen(self) -> Screen[object]:
        screen = super().get_default_screen()
        self.initial_classes = set(screen.classes)
        assert not screen.is_mounted
        return screen


@pytest.mark.parametrize(
    ("size", "classes"),
    [
        ((79, 29), {"-single", "-short"}),
        ((80, 30), {"-icons", "-tall"}),
        ((99, 29), {"-icons", "-short"}),
        ((100, 36), {"-full", "-tall"}),
    ],
)
def test_first_mount_uses_responsive_classes(size: tuple[int, int], classes: set[str]) -> None:
    async def main() -> None:
        app = StartupApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert app.initial_classes == classes
            assert app.screen.classes == classes
            assert app.screen.id == "_default"

    asyncio.run(main())


def test_resizing_replaces_initial_responsive_classes() -> None:
    async def main() -> None:
        app = StartupApp()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            assert app.initial_classes == {"-full", "-tall"}
            for size, classes in (
                ((80, 24), {"-icons", "-short"}),
                ((79, 30), {"-single", "-tall"}),
                ((100, 36), {"-full", "-tall"}),
            ):
                await pilot.resize_terminal(*size)
                await pilot.pause()
                assert app.screen.classes == classes

    asyncio.run(main())
