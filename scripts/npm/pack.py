from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packaging" / "npm"


def npm_command(tool: str = "npm") -> list[str]:
    executable = shutil.which(tool)
    if executable is None:
        raise FileNotFoundError(f"{tool} is required")
    if os.name == "nt" and executable.lower().endswith((".cmd", ".ps1")):
        node = shutil.which("node")
        name = "npm-cli.js" if tool == "npm" else "npx-cli.js"
        script = Path(executable).parent / "node_modules" / "npm" / "bin" / name
        if node is None or not script.is_file():
            raise FileNotFoundError("The Node installation must include npm's CLI script")
        return [node, str(script)]
    return [executable]


def validate_files(files: Sequence[Mapping[str, object]]) -> None:
    roots = {"bin", "lib", "payload"}
    names = {"package.json", "README.md", "LICENSE"}
    for item in files:
        value = item.get("path")
        if not isinstance(value, str):
            raise ValueError("Invalid npm whitelist entry")
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or (path.parts[0] not in roots and value not in names)
        ):
            raise ValueError(f"Outside npm whitelist: {value}")


def stamp() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = str(project["version"])
    package: dict[str, object] = json.loads((PACKAGE / "package.json").read_text(encoding="utf-8"))
    urls = project["urls"]
    package.update(
        version=version,
        description=project["description"],
        license=project["license"],
        keywords=project["keywords"],
        repository={"type": "git", "url": "git+" + urls["Repository"] + ".git"},
        homepage=urls["Homepage"],
        bugs={"url": urls["Bug Tracker"]},
    )
    (PACKAGE / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    return version


def pack(destination: Path | None = None) -> Path:
    version = stamp()
    uv = shutil.which("uv")
    if uv is None:
        raise FileNotFoundError("uv is required to build the Python wheel")
    output = destination or ROOT / "dist"
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(output)], cwd=ROOT, check=True)
    wheel = output / f"cuanta-{version}-py3-none-any.whl"
    payload = PACKAGE / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    for old in payload.glob("*.whl"):
        old.unlink()
    shutil.copy2(wheel, payload / wheel.name)
    subprocess.run(
        [
            uv,
            "export",
            "--no-dev",
            "--frozen",
            "--no-emit-project",
            "--no-header",
            "--output-file",
            str(payload / "requirements.txt"),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    manifest = {
        "version": version,
        "wheel": wheel.name,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }
    (payload / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(ROOT / "LICENSE", PACKAGE / "LICENSE")
    npm = npm_command()
    dry = subprocess.run(
        [*npm, "pack", "--dry-run", "--json", "--ignore-scripts"],
        cwd=PACKAGE,
        capture_output=True,
        text=True,
        check=True,
    )
    records: list[dict[str, object]] = json.loads(dry.stdout)
    files = records[0]["files"]
    if not isinstance(files, list):
        raise ValueError("Invalid npm pack whitelist response")
    validate_files(files)
    built = subprocess.run(
        [*npm, "pack", "--json", "--ignore-scripts", "--pack-destination", str(output)],
        cwd=PACKAGE,
        capture_output=True,
        text=True,
        check=True,
    )
    packed: list[dict[str, object]] = json.loads(built.stdout)
    tarball = output / str(packed[0]["filename"])
    print(tarball)
    return tarball


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the locked Python payload and npm tarball")
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    pack(options.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
