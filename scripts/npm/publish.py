from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.npm.pack import PACKAGE, npm_command
from scripts.npm.smoke import tarball_from


def registered(name: str, version: str) -> bool:
    url = f"https://registry.npmjs.org/{quote(name, safe='')}/{quote(version, safe='')}"
    try:
        with urlopen(url, timeout=30) as response:
            metadata = json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return False
        raise
    if not isinstance(metadata, dict) or metadata.get("version") != version:
        raise ValueError("Registry response does not match the exact release version")
    return True


def publish(tarball: Path, name: str, version: str) -> bool:
    if registered(name, version):
        print(f"{name}@{version} already exists; publication skipped")
        return False
    command = npm_command()
    npm = subprocess.run([*command, "--version"], capture_output=True, text=True, check=True)
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", npm.stdout.strip())
    if match is None or tuple(map(int, match.groups())) < (11, 5, 1):
        raise ValueError("Publication requires npm >= 11.5.1 for OIDC authentication")
    subprocess.run(
        [*command, "publish", str(tarball), "--provenance", "--access", "public"], check=True
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish a new npm version or skip an existing one"
    )
    parser.add_argument("--tarball-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    options = parser.parse_args()
    metadata = json.loads((PACKAGE / "package.json").read_text(encoding="utf-8"))
    if metadata["version"] != options.version:
        raise ValueError("Publication version must match committed package.json")
    publish(tarball_from(options.tarball_dir), metadata["name"], options.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
