"""Exercise the actual NSIS package on a Windows desktop runner."""

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

import win32con
import win32gui
import win32process
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def install(package, destination):
    # NSIS requires /D= to be last and unquoted even if the path contains spaces.
    args = subprocess.list2cmdline([str(package), "/S"]) + f" /D={destination}"
    subprocess.run(args, check=True, timeout=240)
    assert (destination / "Garden.exe").is_file()
    assert (destination / "uninstall.exe").is_file()


def run_engine(executable, args, *, cwd):
    return json.loads(subprocess.check_output([executable, *args], cwd=cwd, timeout=120))


def control_engine(executable, home, operation, *, cwd):
    args = ["control", "--home", str(home), "--operation", operation]
    return run_engine(executable, args, cwd=cwd)


def webview_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for_webview(process, endpoint):
    deadline = time.monotonic() + 45
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with urlopen(endpoint + "/json/version", timeout=1) as response:  # noqa: S310
                if json.load(response).get("webSocketDebuggerUrl"):
                    return
        except (URLError, TimeoutError):
            time.sleep(0.3)
    raise RuntimeError(f"WebView2 CDP unavailable. Garden exit status: {process.poll()}")


def workbench_window(process):
    windows = []

    def collect(hwnd, _):
        if win32process.GetWindowThreadProcessId(hwnd)[
            1
        ] == process.pid and "资产工作台" in win32gui.GetWindowText(hwnd):
            windows.append(hwnd)

    win32gui.EnumWindows(collect, None)
    assert len(windows) == 1, windows
    return windows[0]


def wait_for_page(browser, match, description):
    # The WebView2 debugging endpoint may listen before it has any page targets.
    cdp = browser.new_browser_cdp_session()
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            cdp.send("Target.getTargets")
            for context in browser.contexts:
                for page in context.pages:
                    if match(urlsplit(page.url)):
                        return page
            time.sleep(0.2)
    finally:
        cdp.detach()
    raise AssertionError(f"WebView2 did not open {description}")


def connect_workbench(session, setup, state):
    output = setup.locator("#output")
    previous = output.text_content()
    setup.locator("#initialize").check()
    setup.locator("#connect").click()
    setup.wait_for_function(
        "(previous) => document.querySelector('#output').textContent !== previous",
        arg=previous,
        timeout=45_000,
    )
    result = output.text_content()
    assert '"connected": true' in result, f"Garden could not connect: {result}"
    assert state.is_file()
    port = json.loads(state.read_text(encoding="utf-8"))["port"]
    page = wait_for_page(
        session,
        lambda address: address.netloc == f"127.0.0.1:{port}" and address.path == "/",
        "the connected workbench",
    )
    page.get_by_role("heading", name="创建资产扫描").wait_for(timeout=15_000)


def serve_and_close(destination, home, directory, version):
    port = webview_port()
    endpoint = f"http://127.0.0.1:{port}"
    env = {**os.environ, "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port}"}
    process = subprocess.Popen(
        [destination / "Garden.exe", "--config", directory / "settings.json"],
        cwd=directory,
        env=env,
    )
    executable = destination / "engine/garden-engine.exe"
    state = home / "runtime/server.json"
    try:
        wait_for_webview(process, endpoint)
        with sync_playwright() as playwright:
            session = playwright.chromium.connect_over_cdp(endpoint)
            setup = wait_for_page(
                session, lambda address: address.hostname == "tauri.localhost", "the setup page"
            )
            setup.wait_for_function(
                "(version) => document.querySelector('#version')?.textContent === version",
                arg=version,
            )
            setup.locator("#home").fill(str(home))
            setup.locator("#inspect").click()
            setup.locator("#output").filter(has_text='"exists": false').wait_for()
            connect_workbench(session, setup, state)
            assert control_engine(executable, home, "activity", cwd=directory)["idle"] is True
            win32gui.PostMessage(workbench_window(process), win32con.WM_CLOSE, 0, 0)
            assert process.wait(timeout=45) == 0
        assert not state.exists(), "Closing the App should stop its owned engine and clear state"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        if state.exists():
            control_engine(executable, home, "stop", cwd=directory)


def verify_files(work):
    from app.core.local_files import local_file_lock
    from app.services.collection_storage import CollectionStorage

    storage = CollectionStorage(work / "资料" / "collection")
    payload = {"url": "http://127.0.0.1/example", "label": "合成材料"}
    ref, digest = storage.write(payload)
    assert storage.read(ref, digest) == payload
    lock = work / "executor.lock"
    with local_file_lock(lock, blocking=False):
        try:
            with local_file_lock(lock, blocking=False):
                raise AssertionError("A second handle acquired an exclusive lock")
        except BlockingIOError:
            pass
    with local_file_lock(lock, blocking=False):
        pass


def verify_engine_start(executable):
    with tempfile.TemporaryDirectory(prefix="garden-engine-") as path:
        directory = Path(path).resolve(strict=True)
        home = directory / "引擎 数据"
        args = ["connect", "--home", str(home), "--initialize"]
        instance = run_engine(executable, args, cwd=directory)
        try:
            assert instance["owned"]
            assert control_engine(executable, home, "activity", cwd=directory)["idle"]
        finally:
            assert control_engine(executable, home, "stop", cwd=directory)["stopped"]
        assert not (home / "runtime/server.json").exists()
        print("Frozen Windows engine started and stopped successfully", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, help="Check the frozen engine before bundling")
    args = parser.parse_args()
    if args.engine:
        verify_engine_start(args.engine.resolve(strict=True))
        return
    version = json.loads((ROOT / "desktop/package.json").read_text())["version"]
    report = json.loads((ROOT / ".verification/package-report.json").read_text())
    package = Path(report["artifacts"][0]["file"])
    with package.open("rb") as handle:
        assert hashlib.file_digest(handle, "sha256").hexdigest() == report["artifacts"][0]["sha256"]

    with tempfile.TemporaryDirectory(prefix="garden-install-") as path:
        # Windows may supply a DOS short path for TEMP; the engine resolves it.
        directory = Path(path).resolve(strict=True)
        verify_files(directory)
        # Exercise quoting and Unicode in both program and data paths.
        installed = directory / "Garden 安装"
        home = directory / "工作台 数据"
        install(package, installed)
        assert (installed / "notices/inventory.json").is_file()
        probe = run_engine(installed / "engine/garden-engine.exe", ["probe"], cwd=directory)
        assert probe["version"] == version
        assert probe["frozen"] and probe["offline_download"] and probe["migration"]
        chromium = Path(probe["chromium_path"]).resolve(strict=True)
        browsers = (installed / "browsers").resolve(strict=True)
        assert chromium.is_relative_to(browsers), f"{chromium} is outside {browsers}"
        print(f"Installed engine and bundled Chromium {probe['chromium']} passed", flush=True)

        serve_and_close(installed, home, directory, version)
        print("WebView2 setup, migration, workbench and clean shutdown passed", flush=True)
        database = home / "garden.db"
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "0016",
            )
        original = hashlib.sha256(database.read_bytes()).hexdigest()
        install(package, installed)
        assert hashlib.sha256(database.read_bytes()).hexdigest() == original
        subprocess.run([installed / "uninstall.exe", "/S"], check=True, timeout=120)
        # NSIS normally copies and restarts its uninstaller from a temporary directory.
        remaining = [
            installed / name for name in ("Garden.exe", "engine/garden-engine.exe", "uninstall.exe")
        ]
        deadline = time.monotonic() + 120
        while any(file.exists() for file in remaining) and time.monotonic() < deadline:
            time.sleep(0.25)
        assert not any(file.exists() for file in remaining)
        assert hashlib.sha256(database.read_bytes()).hexdigest() == original
        print("Reinstall and uninstall preserved the selected SQLite database", flush=True)


if __name__ == "__main__":
    main()
