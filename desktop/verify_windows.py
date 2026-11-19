"""Exercise the actual NSIS package on a Windows desktop runner."""

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


def run_engine(destination, args, *, cwd):
    return json.loads(
        subprocess.check_output(
            [destination / "engine/garden-engine.exe", *args], cwd=cwd, timeout=120
        )
    )


def control_engine(destination, home, operation, *, cwd):
    args = ["control", "--home", str(home), "--operation", operation]
    return run_engine(destination, args, cwd=cwd)


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


def close_window(process):
    windows = []

    def collect(hwnd, _):
        if win32process.GetWindowThreadProcessId(hwnd)[
            1
        ] == process.pid and "资产工作台" in win32gui.GetWindowText(hwnd):
            windows.append(hwnd)

    win32gui.EnumWindows(collect, None)
    assert len(windows) == 1, windows
    win32gui.PostMessage(windows[0], win32con.WM_CLOSE, 0, 0)
    assert process.wait(timeout=45) == 0


def serve_and_close(destination, home, directory, version):
    port = webview_port()
    endpoint = f"http://127.0.0.1:{port}"
    env = {**os.environ, "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port}"}
    process = subprocess.Popen(
        [destination / "Garden.exe", "--config", directory / "settings.json"],
        cwd=directory,
        env=env,
    )
    state = home / "runtime/server.json"
    try:
        wait_for_webview(process, endpoint)
        with sync_playwright() as playwright:
            session = playwright.chromium.connect_over_cdp(endpoint)
            setup = session.contexts[0].pages[0]
            setup.wait_for_function(
                "(version) => document.querySelector('#version')?.textContent === version",
                arg=version,
            )
            setup.locator("#home").fill(str(home))
            setup.locator("#inspect").click()
            setup.locator("#output").filter(has_text='"exists": false').wait_for()
            setup.locator("#initialize").check()
            with setup.context.expect_page(timeout=60_000) as opened:
                setup.locator("#connect").click()
            page = opened.value
            page.get_by_role("heading", name="创建资产扫描").wait_for(timeout=30_000)
            assert state.is_file()
            assert control_engine(destination, home, "activity", cwd=directory)["idle"] is True
            close_window(process)
        assert not state.exists(), "Closing the App should stop its owned engine and clear state"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        if state.exists():
            control_engine(destination, home, "stop", cwd=directory)


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


def main():
    version = json.loads((ROOT / "desktop/package.json").read_text())["version"]
    report = json.loads((ROOT / ".verification/package-report.json").read_text())
    package = Path(report["artifacts"][0]["file"])
    with package.open("rb") as handle:
        assert hashlib.file_digest(handle, "sha256").hexdigest() == report["artifacts"][0]["sha256"]

    with tempfile.TemporaryDirectory(prefix="garden-install-") as path:
        directory = Path(path)
        verify_files(directory)
        # Exercise quoting and Unicode in both program and data paths.
        installed = directory / "Garden 安装"
        home = directory / "工作台 数据"
        install(package, installed)
        assert (installed / "notices/inventory.json").is_file()
        probe = run_engine(installed, ["probe"], cwd=directory)
        assert probe["version"] == version
        assert probe["frozen"] and probe["offline_download"] and probe["migration"]
        assert Path(probe["chromium_path"]).is_relative_to(installed / "browsers")
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
        assert not (installed / "Garden.exe").exists()
        assert not (installed / "engine/garden-engine.exe").exists()
        assert hashlib.sha256(database.read_bytes()).hexdigest() == original
        print("Reinstall and uninstall preserved the selected SQLite database", flush=True)


if __name__ == "__main__":
    main()
