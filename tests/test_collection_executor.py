import os
import subprocess
import sys

from app.core.settings import get_settings
from app.services.collection_executor import CollectionExecutor


def test_only_one_process_can_own_durable_execution_and_last_close_releases_it():
    first = CollectionExecutor(get_settings().database_url)
    second = CollectionExecutor(get_settings().database_url)
    script = """
from app.core.errors import ConflictError
from app.core.settings import get_settings
from app.services.collection_executor import CollectionExecutor
owner = CollectionExecutor(get_settings().database_url)
try:
    owner.acquire()
except ConflictError:
    raise SystemExit(42)
else:
    owner.close()
"""

    def probe():
        return subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONPATH": "."},
            capture_output=True,
            text=True,
            timeout=10,
        ).returncode

    try:
        first.acquire()
        second.acquire()
        assert probe() == 42
        first.close()
        assert probe() == 42
        second.close()
        assert probe() == 0
    finally:
        first.close()
        second.close()


def test_other_process_can_read_but_cannot_run_startup_recovery():
    owner = CollectionExecutor(get_settings().database_url)
    owner.acquire()
    script = """
from app.core.errors import ConflictError
from app.services.scan_application import ScanApplicationService, InlineScanDispatcher
from app.core.settings import get_settings
from app.db.bootstrap import init_database
init_database(get_settings().database_url)
service = ScanApplicationService(dispatcher=InlineScanDispatcher())
assert service.list_scans() == []
try:
    service.interrupt_active_scans()
except ConflictError as error:
    assert str(error) == "collection_executor_busy"
    raise SystemExit(42)
finally:
    service.shutdown()
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONPATH": "."},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 42, result.stderr
    finally:
        owner.close()


def test_real_cli_stop_releases_web_executor_before_startup_recovery(tmp_path, monkeypatch):
    import socket

    from app.cli.paths import GardenPaths
    from app.cli.web_runtime import WebRuntimeManager

    paths = GardenPaths(tmp_path / "home")
    monkeypatch.setenv("GARDEN_HOME", str(paths.home))
    monkeypatch.setenv("GARDEN_CLI_RUNTIME", "1")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    manager = WebRuntimeManager(paths=paths, startup_timeout_seconds=15)
    try:
        manager.ensure(ui_port=port)
        result = subprocess.run(
            [sys.executable, "-m", "app.cli.main", "stop"],
            env={**os.environ, "PYTHONPATH": "."},
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert "本机 Garden Web UI 已停止" in result.stdout
        assert not paths.server_state_file.exists()
        owner = CollectionExecutor(get_settings().database_url)
        owner.acquire()
        owner.close()
    finally:
        manager.stop()
