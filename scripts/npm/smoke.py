from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.npm.pack import ROOT, npm_command, pack


def command(
    args: list[str], cwd: Path, env: dict[str, str], *, batch: bool = False
) -> tuple[str, float]:
    started = time.perf_counter()
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=batch,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"npm smoke command exited {result.returncode}: {result.stderr}")
    return result.stdout, time.perf_counter() - started


def smoke() -> dict[str, object]:
    tarball = pack()
    with tempfile.TemporaryDirectory(prefix="cuanta-npm-smoke-") as folder:
        temporary = Path(folder)
        target = temporary / "install space"
        environment = {
            **os.environ,
            "CUANTA_RUNTIME_DIR": str(temporary / "runtime"),
            "CUANTA_HOME": str(temporary / "home"),
        }
        command(
            [
                *npm_command(),
                "install",
                str(tarball),
                "--prefix",
                str(target),
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            temporary,
            environment,
        )
        entry = target / "node_modules" / ".bin" / ("cuanta.cmd" if os.name == "nt" else "cuanta")
        version, first = command(
            [str(entry), "--version"], temporary, environment, batch=os.name == "nt"
        )
        expected = str(
            json.loads((ROOT / "packaging/npm/package.json").read_text(encoding="utf-8"))["version"]
        )
        if version.strip() != f"cuanta {expected}":
            raise ValueError(f"Unexpected installed version output: {version!r}")
        help_text, warm = command(
            [str(entry), "--help"], temporary, environment, batch=os.name == "nt"
        )
        if "Usage:" not in help_text:
            raise ValueError("Installed CLI help did not render")
        npx_version, npx_seconds = command(
            [
                *npm_command("npx"),
                "--yes",
                "--cache",
                str(temporary / "npm-cache"),
                "file:" + tarball.as_posix(),
                "--version",
            ],
            temporary,
            environment,
        )
        if npx_version.strip() != f"cuanta {expected}":
            raise ValueError(f"Unexpected npx version output: {npx_version!r}")
        result: dict[str, object] = {
            "version": expected,
            "first_seconds": round(first, 3),
            "warm_help_seconds": round(warm, 3),
            "npx_seconds": round(npx_seconds, 3),
        }
        print(json.dumps(result))
        return result


if __name__ == "__main__":
    smoke()
