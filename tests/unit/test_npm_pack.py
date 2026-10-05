from __future__ import annotations

import pytest
from scripts.npm.pack import validate_files


def test_pack_whitelist_accepts_only_the_runtime_payload() -> None:
    validate_files(
        [
            {"path": "package.json"},
            {"path": "bin/cuanta.js"},
            {"path": "lib/launcher.js"},
            {"path": "payload/manifest.json"},
            {"path": "payload/requirements.txt"},
            {"path": "payload/cuanta.whl"},
            {"path": "README.md"},
            {"path": "LICENSE"},
        ]
    )


@pytest.mark.parametrize(
    "path",
    [
        "test/launcher.test.cjs",
        ".cuanta/config.toml",
        "graphify-out/graph.json",
        "../secret",
        "payload/../secret",
    ],
)
def test_pack_whitelist_refuses_unlisted_or_escaping_files(path: str) -> None:
    with pytest.raises(ValueError, match="whitelist"):
        validate_files([{"path": path}])
