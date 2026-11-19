"""Build the native one-directory sidecar with fixed Python and browser resources."""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    versions = json.loads((ROOT / "desktop/build-versions.json").read_text())
    host = platform.system(), platform.machine().lower()
    if host == ("Windows", "amd64"):
        versions.update(versions["windows"])
        arch_args = []
    elif host == ("Darwin", "arm64"):
        arch_args = ["--target-arch", "arm64"]
    else:
        raise SystemExit(f"No native desktop engine build for {host}.")
    if platform.python_version() != versions["python"]:
        raise SystemExit(f"Use pinned Python {versions['python']}.")
    browsers = Path(os.environ["GARDEN_BUILD_BROWSERS"])
    work = ROOT / ".verification"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--name",
        "garden-engine",
        *arch_args,
        "--distpath",
        str(ROOT / "desktop/src-tauri/engine-dist"),
        "--workpath",
        str(work / "pyinstaller-build"),
        "--specpath",
        str(work),
        "--paths",
        str(ROOT),
        "--collect-all",
        "app",
        "--collect-all",
        "playwright",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "sqlalchemy.dialects.sqlite",
        "--copy-metadata",
        "garden",
        "--add-data",
        str(ROOT / "pyproject.toml") + ":.",
        "--add-data",
        str(ROOT / "alembic.ini") + ":app/db/migration_assets",
        "--add-data",
        str(ROOT / "migrations") + ":app/db/migration_assets/migrations",
        str(ROOT / "desktop/engine.py"),
    ]
    subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(work / "pyinstaller-cache")},
        check=True,
    )
    # Keep Chromium's complete native App/Framework layout outside PyInstaller's
    # binary cache. Tauri packages it alongside the frozen engine.
    output = ROOT / "desktop/src-tauri/engine-dist/browsers"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    for name in versions["browser_resources"]:
        shutil.copytree(browsers / name, output / name, symlinks=True)


if __name__ == "__main__":
    main()
