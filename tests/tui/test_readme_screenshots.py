from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = next((ROOT / "tests" / "tui" / "__snapshots__").iterdir())
SCREENSHOTS = ROOT / "docs" / "screenshots"
SOURCES = {
    "home-dark": "test_home[120x36-calico-dark]",
    "home-light": "test_home[120x36-calico-light]",
    "tests": "test_tests_red[120x36-calico-dark]",
    "capsule": "test_capsule_viewer[120x36-calico-dark]",
    "mandate": "test_mandate_form[120x36-calico-dark]",
    "pipeline": "test_pipeline_finished[120x36-calico-dark]",
    "spectrum": "test_spectrum[120x36-calico-dark]",
    "leaks": "test_spectrum_leaks[calico-light]",
    "ledger": "test_ledger[120x36-calico-dark]",
    "health": "test_health[120x36-calico-light]",
    "init": "test_init_results[120x36-calico-dark]",
    "consent": "test_consent_modal[calico-light]",
    "diff": "test_diff_screen[calico-dark]",
}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_readme_screenshot_matches_its_snapshot(name: str) -> None:
    published = (SCREENSHOTS / f"{name}.svg").read_bytes()
    assert published == (SNAPSHOTS / f"{SOURCES[name]}.svg").read_bytes()


def test_readme_links_every_screenshot() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    linked = set(re.findall(r"docs/screenshots/([\w-]+)\.svg", readme))
    assert linked == set(SOURCES)
