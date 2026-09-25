from __future__ import annotations

import os
from pathlib import Path

import pytest
from coverage import Coverage
from hypothesis.configuration import storage_directory

ROOT = Path(__file__).resolve().parents[2]


def test_default_pytest_artifacts_stay_outside_repo_root(pytestconfig: pytest.Config) -> None:
    snapshot_report = Path(str(pytestconfig.getoption("--snapshot-report"))).resolve()
    coverage_data = Path(Coverage().config.data_file).resolve()
    hypothesis_home = storage_directory(intent_to_write=False).home_directory.resolve()
    assert not pytestconfig.pluginmanager.hasplugin("cacheprovider")
    if not any(arg.startswith("--snapshot-report") for arg in pytestconfig.invocation_params.args):
        assert not snapshot_report.is_relative_to(ROOT)
    if not os.environ.get("COVERAGE_FILE"):
        assert not coverage_data.is_relative_to(ROOT)
    if not os.environ.get("HYPOTHESIS_STORAGE_DIRECTORY"):
        assert not hypothesis_home.is_relative_to(ROOT)
