"""Collect the concrete Python, Rust, driver and browser notices for this local bundle."""

import importlib.metadata
import json
import os
import shutil
import ssl
import subprocess
import sysconfig
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "desktop/src-tauri/engine-dist/notices"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    inventory = dict(
        project={
            "name": "Garden",
            "license": "Proprietary (pyproject.toml); separate license text not supplied",
        },
        python=[],
        rust=[],
    )
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    shutil.copy2(python_license, output / "Python-LICENSE.txt")
    for dist in sorted(
        importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()
    ):
        name = dist.metadata["Name"]
        if name.lower() == "garden":
            continue
        notices = []
        for file in dist.files or []:
            if any(word in str(file).lower() for word in ("license", "copying", "notice")):
                source = Path(dist.locate_file(file))
                if not source.is_file():
                    continue
                destination = output / "python" / name / str(file)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                notices.append(str(destination.relative_to(output)))
        inventory["python"].append(
            dict(
                name=name,
                version=dist.version,
                license=dist.metadata.get("License-Expression") or dist.metadata.get("License"),
                notices=notices,
            )
        )
    cargo = subprocess.run(
        [
            "cargo",
            "metadata",
            "--locked",
            "--format-version",
            "1",
            "--filter-platform",
            "aarch64-apple-darwin",
        ],
        cwd=ROOT / "desktop/src-tauri",
        capture_output=True,
        text=True,
        check=True,
    )
    metadata = json.loads(cargo.stdout)
    resolved = {node["id"] for node in metadata["resolve"]["nodes"]}
    for package in metadata["packages"]:
        if package["id"] not in resolved or package["name"] == "garden-desktop":
            continue
        source = Path(package["manifest_path"]).parent
        notices = []
        for file in source.iterdir():
            if file.is_file() and file.name.lower().startswith(("license", "copying", "notice")):
                target = output / "rust" / f"{package['name']}-{package['version']}" / file.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)
                notices.append(str(target.relative_to(output)))
        inventory["rust"].append(
            dict(
                name=package["name"],
                version=package["version"],
                license=package["license"],
                notices=notices,
            )
        )
    openssl = Path(os.environ["GARDEN_OPENSSL_LICENSE"])
    shutil.copy2(openssl, output / "OpenSSL-LICENSE.txt")
    inventory["openssl"] = ssl.OPENSSL_VERSION
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / "desktop/src-tauri/engine-dist/browsers")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page()
        page.goto("chrome://credits")
        (output / "Chromium-credits.html").write_text(page.content())
        inventory["chromium"] = browser.version
        browser.close()
    (output / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2))
    (output / "README.txt").write_text(
        "Local Beta notice inventory. Python, Rust, Playwright/Node, "
        "Chromium and OpenSSL notices are included.\n"
        "The Playwright Chromium distribution is Chrome for Testing; "
        "review its credits and bundled notices before formal distribution.\n"
        "Garden is declared Proprietary in pyproject.toml; "
        "no separate project license text is supplied. "
        "This inventory is not distribution clearance.\n"
        "Formal signing, notarization, update channel and license review "
        "remain separate release requirements.\n"
    )
    print(
        json.dumps(
            dict(python=len(inventory["python"]), rust=len(inventory["rust"]), notices=str(output))
        )
    )


if __name__ == "__main__":
    main()
