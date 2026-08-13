import json

import pytest

from app.cli.paths import GardenPaths


def test_ensure_prefers_default_port_and_records_started_server(monkeypatch, tmp_path):
    from app.cli.web_runtime import WebRuntimeManager

    started = []

    class Process:
        pid = 4210

        def poll(self):
            return None

    paths = GardenPaths(tmp_path / "garden-home")
    manager = WebRuntimeManager(paths=paths, default_port=8000)
    monkeypatch.setattr(manager, "_is_port_available", lambda port: port == 8000)
    monkeypatch.setattr(manager, "_start_process", lambda port: started.append(port) or Process())
    monkeypatch.setattr(manager, "_wait_for_health", lambda port: True)

    runtime = manager.ensure()

    assert runtime.base_url == "http://127.0.0.1:8000"
    assert started == [8000]
    state = json.loads(paths.server_state_file.read_text(encoding="utf-8"))
    assert state["pid"] == 4210
    assert state["port"] == 8000
    assert state["home"] == str(paths.home)
    assert state["started_at"]
    assert paths.server_pid_file.read_text(encoding="utf-8") == "4210\n"


def test_ensure_uses_system_free_port_when_default_is_occupied(monkeypatch, tmp_path):
    from app.cli.web_runtime import WebRuntimeManager

    class Process:
        pid = 4211

        def poll(self):
            return None

    paths = GardenPaths(tmp_path / "garden-home")
    manager = WebRuntimeManager(paths=paths, default_port=8000)
    monkeypatch.setattr(manager, "_is_port_available", lambda port: False)
    monkeypatch.setattr(manager, "_reserve_free_port", lambda: 51843)
    monkeypatch.setattr(manager, "_start_process", lambda port: Process())
    monkeypatch.setattr(manager, "_wait_for_health", lambda port: True)

    runtime = manager.ensure()

    assert runtime.port == 51843


def test_ensure_reuses_healthy_server_and_rejects_different_explicit_port(monkeypatch, tmp_path):
    from app.cli.web_runtime import WebRuntimeError, WebRuntimeManager

    paths = GardenPaths(tmp_path / "garden-home")
    paths.ensure_directories()
    paths.server_state_file.write_text(
        json.dumps(
            {
                "pid": 4212,
                "port": 8123,
                "home": str(paths.home),
                "started_at": "2026-08-13T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    manager = WebRuntimeManager(paths=paths, default_port=8000)
    monkeypatch.setattr(manager, "_is_healthy", lambda runtime: True)

    assert manager.ensure().port == 8123
    with pytest.raises(WebRuntimeError, match="8123"):
        manager.ensure(ui_port=9000)


def test_ensure_removes_stale_state_before_starting(monkeypatch, tmp_path):
    from app.cli.web_runtime import WebRuntimeManager

    class Process:
        pid = 4213

        def poll(self):
            return None

    paths = GardenPaths(tmp_path / "garden-home")
    paths.ensure_directories()
    paths.server_state_file.write_text(
        json.dumps(
            {
                "pid": 4212,
                "port": 8123,
                "home": str(paths.home),
                "started_at": "2026-08-13T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    manager = WebRuntimeManager(paths=paths, default_port=8000)
    monkeypatch.setattr(manager, "_is_healthy", lambda runtime: False)
    monkeypatch.setattr(manager, "_is_port_available", lambda port: port == 8000)
    monkeypatch.setattr(manager, "_start_process", lambda port: Process())
    monkeypatch.setattr(manager, "_wait_for_health", lambda port: True)

    runtime = manager.ensure()

    assert runtime.port == 8000
    assert json.loads(paths.server_state_file.read_text(encoding="utf-8"))["pid"] == 4213


def test_stop_only_terminates_a_healthy_recorded_server(monkeypatch, tmp_path):
    from app.cli.web_runtime import WebRuntimeManager

    paths = GardenPaths(tmp_path / "garden-home")
    paths.ensure_directories()
    paths.server_state_file.write_text(
        json.dumps(
            {
                "pid": 4214,
                "port": 8000,
                "home": str(paths.home),
                "started_at": "2026-08-13T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    manager = WebRuntimeManager(paths=paths)
    killed = []
    alive = iter([True, False])
    monkeypatch.setattr(manager, "_is_healthy", lambda runtime: True)
    monkeypatch.setattr(manager, "_is_process_alive", lambda pid: next(alive))
    monkeypatch.setattr("app.cli.web_runtime.os.killpg", lambda pid, signal: killed.append(pid))

    assert manager.stop() is True
    assert killed == [4214]
    assert not paths.server_state_file.exists()
