from __future__ import annotations

from cuanta.application.doctor import lsp_rows
from cuanta.domain.plugins import LSP_PLUGIN, InstalledPlugin, lean_settings, lsp_ready
from cuanta.domain.progress import Status
from cuanta.tui.i18n import Catalog

LSP = InstalledPlugin(LSP_PLUGIN, "1.0.0", "user")
OTHER = InstalledPlugin("caveman@caveman", "1", "user")


def test_the_lean_profile_enables_only_an_installed_lsp_plugin_for_a_writer() -> None:
    plugins = (LSP, OTHER)
    enabled = lean_settings(plugins, lsp=True)["enabledPlugins"]
    assert isinstance(enabled, dict)
    assert enabled[LSP_PLUGIN] is True and enabled["caveman@caveman"] is False
    plain = lean_settings(plugins)["enabledPlugins"]
    assert isinstance(plain, dict) and plain[LSP_PLUGIN] is False
    missing = lean_settings((OTHER,), lsp=True)["enabledPlugins"]
    assert isinstance(missing, dict) and LSP_PLUGIN not in missing


def test_lsp_is_ready_for_python_with_the_server_and_the_plugin() -> None:
    assert lsp_ready("python", True, (LSP,))
    assert not lsp_ready("python", False, (LSP,))
    assert not lsp_ready("python", True, (OTHER,))
    assert not lsp_ready("typescript", True, (LSP,))


def test_doctor_suggests_the_server_then_the_plugin_for_a_python_project() -> None:
    absent = lsp_rows("python", False, ())
    assert [(row.status, row.fix) for row in absent] == [(Status.WARN, "npm install -g pyright")]
    assert "pyright-langserver" in absent[0].detail
    no_plugin = lsp_rows("python", True, ())
    assert [row.fix for row in no_plugin] == [f"claude plugin install {LSP_PLUGIN}"]
    assert [row.status for row in lsp_rows("python", True, (LSP,))] == [Status.OK]
    assert lsp_rows("typescript", False, ()) == []
    spanish = Catalog("es").message(absent[0].message)
    assert "pyright-langserver" in spanish and "diagnósticos" in spanish
