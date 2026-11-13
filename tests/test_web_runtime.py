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
                "version": __import__(
                    "app.core.version", fromlist=["project_version"]
                ).project_version(),
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
    monkeypatch.setattr(manager, "_is_process_alive", lambda pid: False)
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


def test_healthz_is_not_an_instance_handshake(monkeypatch, tmp_path):
    """An unrelated HTTP 200 must never authorize reuse or termination."""
    from app.cli.web_runtime import WebRuntime, WebRuntimeError, WebRuntimeManager

    manager = WebRuntimeManager(paths=GardenPaths(tmp_path))
    runtime = WebRuntime(pid=1234, port=8123, home=str(tmp_path), started_at="now")
    manager._write_state(runtime)
    monkeypatch.setattr(manager, "_is_process_alive", lambda pid: True)

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"status":"ok"}'

    monkeypatch.setattr("app.cli.web_runtime.urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(WebRuntimeError, match="握手"):
        manager.ensure()
    assert manager.stop() is False
    assert manager.paths.server_state_file.exists()


def test_managed_session_requires_matching_origin_and_instance(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setenv("GARDEN_RUNTIME_TOKEN", "synthetic-test-session")
    monkeypatch.setenv("GARDEN_RUNTIME_INSTANCE", "synthetic-instance")
    monkeypatch.setenv("GARDEN_RUNTIME_PORT", "8123")
    monkeypatch.setenv("GARDEN_HOME", str(tmp_path))
    client = TestClient(create_app(), base_url="http://127.0.0.1:8123")
    assert client.get("/_garden/runtime").status_code == 401
    headers = {"Authorization": "Bearer synthetic-test-session"}
    assert (
        client.get(
            "/_garden/runtime", headers={**headers, "Origin": "https://evil.invalid"}
        ).status_code
        == 403
    )
    for site, expected in [("same-origin", 200), ("cross-site", 403)]:
        response = client.get(
            "/_garden/runtime",
            headers={**headers, "Origin": "null", "Sec-Fetch-Site": site},
        )
        assert response.status_code == expected
    handshake = client.get("/_garden/runtime", headers=headers).json()
    assert handshake["application"] == "garden"
    assert handshake["home"] == str(tmp_path.resolve())
    assert handshake["instance_id"] == "synthetic-instance"
    assert client.post("/_garden/session/exchange", headers=headers).status_code == 204
    assert client.get("/_garden/runtime").status_code == 200


def test_app_owner_cannot_stop_a_replacement_cli_instance(monkeypatch, tmp_path):
    from app.cli.desktop import control
    from app.cli.web_runtime import WebRuntime, WebRuntimeError, WebRuntimeManager

    paths = GardenPaths(tmp_path)
    WebRuntimeManager(paths=paths)._write_state(
        WebRuntime(pid=1234, port=8123, home=str(tmp_path), started_at="now", instance_id="new-cli")
    )
    with pytest.raises(WebRuntimeError, match="实例已变化"):
        control(paths, "stop", expected_instance="previous-app")


def test_sidecar_version_must_match_app_before_opening_data(tmp_path):
    from app.cli.desktop import connect
    from app.cli.web_runtime import WebRuntimeError

    paths = GardenPaths(tmp_path / "not-created")
    with pytest.raises(WebRuntimeError, match="App.*引擎.*不一致"):
        connect(paths, initialize=True, expected_version="synthetic-other-version")
    assert not paths.home.exists()
