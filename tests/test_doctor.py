"""Public doctor command contracts, including absence of repair side effects."""

import json

import pytest
from typer.testing import CliRunner

from app.cli.main import app

runner = CliRunner()


def test_doctor_handles_invalid_settings_without_initializing_database(tmp_path, monkeypatch):
    home = tmp_path / "new-home"
    monkeypatch.setenv("GARDEN_HOME", str(home))
    monkeypatch.setenv("GARDEN_CLI_RUNTIME", "1")
    monkeypatch.setenv("GARDEN_API_PORT", "password=PRIVATE_VALUE")
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{home / 'new.db'}")
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    checks = {check["id"]: check for check in data["checks"]}
    assert checks["configuration"]["status"] == "action_required"
    assert "PRIVATE_VALUE" not in result.output
    assert "Traceback" not in result.output
    assert not home.exists()


def test_doctor_missing_database_does_not_create_it(tmp_path, monkeypatch):
    home = tmp_path / "empty"
    monkeypatch.setenv("GARDEN_CLI_RUNTIME", "1")
    monkeypatch.setenv("GARDEN_HOME", str(home))
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{home / 'garden.db'}")
    result = runner.invoke(app, ["doctor", "--json"])
    checks = {c["id"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["database"]["status"] == "action_required"
    assert "gardenctl db upgrade" in checks["database"]["next_step"]
    assert not home.exists()


def test_doctor_reads_revision_without_repair_or_sidecar_files(tmp_path, monkeypatch):
    import sqlite3

    database = tmp_path / "existing.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('0003')")
    before = database.read_bytes()
    files_before = set(tmp_path.iterdir())
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{database}")
    result = runner.invoke(app, ["doctor", "--json"])
    check = next(c for c in json.loads(result.stdout)["checks"] if c["id"] == "database")
    assert check["status"] == "action_required"
    assert "0003" in check["message"] and "0004" in check["message"]
    assert database.read_bytes() == before
    assert set(tmp_path.iterdir()) == files_before


def test_doctor_reports_missing_chromium_without_installing(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=0, stdout=json.dumps(str(tmp_path / "missing-chrome")))

    monkeypatch.setattr(subprocess, "run", run)
    result = runner.invoke(app, ["doctor", "--json"])
    checks = {c["id"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["browser"]["status"] == "action_required"
    assert "playwright install chromium" in checks["browser"]["next_step"]
    assert len(calls) == 1
    assert "install" not in calls[0]
    assert not (tmp_path / "missing-chrome").exists()


def test_doctor_keeps_unreadable_state_and_checks_storage_without_mkdir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    state = home / "runtime" / "server.json"
    state.parent.mkdir(parents=True)
    state.write_text("password=STATE_SECRET")
    monkeypatch.setenv("GARDEN_HOME", str(home))
    monkeypatch.setenv("GARDEN_CLI_RUNTIME", "1")
    result = runner.invoke(app, ["doctor", "--json"])
    checks = {c["id"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["service"]["status"] == "unknown"
    assert "STATE_SECRET" not in result.output
    assert state.read_text() == "password=STATE_SECRET"
    assert not (home / "reports").exists()
    assert checks["directory_reports"]["status"] == "unknown"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:DB_SECRET@remote.invalid/garden?token=QUERY_SECRET",
        "sqlite:///:memory:",
        "not-a-url PASSWORD_SECRET",
    ],
)
def test_doctor_skips_remote_or_unsupported_database_without_leaking_values(monkeypatch, url):
    monkeypatch.setenv("GARDEN_DATABASE_URL", url)
    result = runner.invoke(app, ["doctor", "--json"])
    data = json.loads(result.stdout)
    check = next(c for c in data["checks"] if c["id"] == "database")
    assert check["status"] == "unknown"
    assert not any(
        secret in result.output for secret in ["DB_SECRET", "QUERY_SECRET", "PASSWORD_SECRET"]
    )


@pytest.mark.parametrize("revision, status", [("0004", "ok"), ("FUTURE_SECRET", "unknown")])
def test_doctor_distinguishes_current_and_unknown_database_revision(
    tmp_path, monkeypatch, revision, status
):
    import sqlite3

    database = tmp_path / "revision.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{database}")
    result = runner.invoke(app, ["doctor", "--json"])
    check = next(c for c in json.loads(result.stdout)["checks"] if c["id"] == "database")
    assert check["status"] == status
    assert "FUTURE_SECRET" not in result.output


def test_doctor_does_not_open_active_wal_database(tmp_path, monkeypatch):
    database = tmp_path / "wal.db"
    database.write_bytes(b"not sqlite but must not be opened")
    wal = tmp_path / "wal.db-wal"
    wal.write_bytes(b"WAL_SECRET")
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{database}")
    result = runner.invoke(app, ["doctor", "--json"])
    check = next(c for c in json.loads(result.stdout)["checks"] if c["id"] == "database")
    assert check["status"] == "unknown" and "WAL" in check["message"]
    assert wal.read_bytes() == b"WAL_SECRET"
    assert not (tmp_path / "wal.db-shm").exists()


def test_doctor_occupied_port_is_not_mistaken_for_healthy_garden(tmp_path, monkeypatch):
    import socket

    monkeypatch.setenv("GARDEN_HOME", str(tmp_path / "missing-home"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = runner.invoke(
            app, ["doctor", "--json", "--ui-port", str(listener.getsockname()[1])]
        )
        connection, _address = listener.accept()
        with connection:
            assert connection.recv(1) == b""  # No HTTP request to the unknown listener.
    check = next(c for c in json.loads(result.stdout)["checks"] if c["id"] == "service")
    assert check["status"] == "unknown"
    assert "不能确认所属程序" in check["message"]


def test_doctor_preserves_stale_service_state(tmp_path, monkeypatch):
    import socket

    home = tmp_path / "home"
    state = home / "runtime" / "server.json"
    state.parent.mkdir(parents=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    original = json.dumps(dict(home=str(home), pid=123456789, port=port))
    state.write_text(original)
    monkeypatch.setenv("GARDEN_HOME", str(home))
    result = runner.invoke(app, ["doctor", "--json"])
    check = next(c for c in json.loads(result.stdout)["checks"] if c["id"] == "service")
    assert check["status"] == "action_required"
    assert state.read_text() == original


def test_doctor_does_not_initialize_even_with_auto_migration_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("GARDEN_DATABASE_AUTO_MIGRATE", "true")
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{tmp_path / 'missing' / 'garden.db'}")
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1
    assert not (tmp_path / "missing").exists()


def test_doctor_rejects_oversized_pid_without_crashing(tmp_path, monkeypatch):
    import socket

    home = tmp_path / "oversized"
    state = home / "runtime" / "server.json"
    state.parent.mkdir(parents=True)
    monkeypatch.setenv("GARDEN_HOME", str(home))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        state.write_text(
            json.dumps(dict(home=str(home), port=listener.getsockname()[1], pid=10**100))
        )
        result = runner.invoke(app, ["doctor", "--json"])
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    checks = {c["id"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["service"]["status"] == "unknown"


def test_doctor_rejects_non_regular_state_before_opening(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    home = tmp_path / "fifo"
    state = home / "runtime" / "server.json"
    state.parent.mkdir(parents=True)
    os.mkfifo(state)
    monkeypatch.setenv("GARDEN_HOME", str(home))
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path != state, "must not open FIFO"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    checks = {c["id"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["service"]["status"] == "unknown"


def test_doctor_reads_migration_metadata_without_executing_migration_code(tmp_path, monkeypatch):
    import sqlite3

    import app as garden_package

    package = tmp_path / "app"
    migrations = tmp_path / "migrations" / "versions"
    package.mkdir()
    migrations.mkdir(parents=True)
    marker = tmp_path / "migration-executed"
    (migrations / "0004_fixture.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('side effect')\n"
        "revision = '0004'\ndown_revision = None\n"
    )
    database = tmp_path / "version.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('0004')")
    monkeypatch.setattr(garden_package, "__file__", str(package / "__init__.py"))
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{database}")
    result = runner.invoke(app, ["doctor", "--json"])
    checks = {c["id"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["database"]["status"] == "ok"
    assert not marker.exists()


@pytest.fixture
def ready_doctor(tmp_path, monkeypatch):
    import socket
    import sqlite3
    import subprocess
    from types import SimpleNamespace

    home = tmp_path / "ready"
    for name in ("reports", "storage", "runtime"):
        (home / name).mkdir(parents=True)
    database = home / "garden.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('0004')")
    chromium = tmp_path / "chromium"
    chromium.write_text("fixture executable")
    chromium.chmod(0o700)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    launcher = binaries / "garden"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o700)
    monkeypatch.setenv("PATH", str(binaries))
    monkeypatch.setenv("GARDEN_HOME", str(home))
    monkeypatch.setenv("GARDEN_CLI_RUNTIME", "1")
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{database}")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        monkeypatch.setenv("GARDEN_API_PORT", str(probe.getsockname()[1]))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(str(chromium))),
    )
    return home, chromium


def test_doctor_healthy_exit_code_and_plain_text_match_json(ready_doctor):
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    checks = json.loads(result.stdout)["checks"]
    assert len(checks) == 8
    assert all(check["status"] == "ok" for check in checks)
    text = runner.invoke(app, ["doctor"])
    assert text.exit_code == 0
    assert "Garden 环境自检（只读）" in text.output
    assert text.output.count("[正常]") == 8


def test_doctor_unknown_exit_code_does_not_mean_healthy(ready_doctor):
    home, _chromium = ready_doctor
    (home / "reports").rmdir()
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 2
    checks = {c["id"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["directory_reports"]["status"] == "unknown"
    assert not (home / "reports").exists()


def test_doctor_browser_permissions_and_failure_exit_code(ready_doctor):
    _home, chromium = ready_doctor
    chromium.chmod(0o600)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1
    checks = {c["id"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["browser"]["status"] == "action_required"


def test_doctor_multiple_path_entries_are_visible_and_not_executed(
    ready_doctor, tmp_path, monkeypatch
):
    import os

    extra = tmp_path / "extra"
    extra.mkdir()
    entry = extra / "garden"
    entry.write_text("#!/bin/sh\nexit 99\n")
    entry.chmod(0o700)
    monkeypatch.setenv("PATH", os.environ["PATH"] + os.pathsep + str(extra))
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 2
    check = next(c for c in json.loads(result.stdout)["checks"] if c["id"] == "installation")
    assert check["status"] == "unknown"
    assert "/extra/garden" in check["message"]


def test_doctor_bad_home_does_not_prevent_safe_diagnostic(monkeypatch):
    monkeypatch.setenv("GARDEN_HOME", "~garden-user-that-does-not-exist")
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    checks = {c["id"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["configuration"]["status"] == "action_required"


def test_real_doctor_process_never_creates_runtime_or_database(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    home = tmp_path / "never-created"
    environment = os.environ.copy()
    environment.update(
        {
            "GARDEN_HOME": str(home),
            "GARDEN_CLI_RUNTIME": "1",
            "GARDEN_DATABASE_URL": f"sqlite:///{home / 'garden.db'}",
            "GARDEN_DATABASE_AUTO_MIGRATE": "true",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "GARDEN_SCAN_PROXY_URL": "http://user:PROXY_SECRET@proxy.invalid/",
        }
    )
    process = subprocess.run(
        [sys.executable, "-B", "-m", "app.cli.main", "doctor", "--json"],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert process.returncode == 1, process.stderr
    report = json.loads(process.stdout)
    assert report["exit_code"] == 1
    assert len(report["checks"]) == 8
    assert "PROXY_SECRET" not in process.stdout + process.stderr
    assert "Database initialized" not in process.stderr
    assert not home.exists()


def test_doctor_directory_permission_failure_is_actionable(ready_doctor, monkeypatch):
    import os

    home, _browser = ready_doctor
    original = os.access

    def permission(path, flags):
        return False if path == home / "reports" else original(path, flags)

    monkeypatch.setattr(os, "access", permission)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    check = next(c for c in report["checks"] if c["id"] == "directory_reports")
    assert check["status"] == "action_required"
    assert check["next_step"]


def test_doctor_browser_driver_failure_hides_raw_error(ready_doctor, monkeypatch):
    import subprocess

    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("password=DRIVER_SECRET", 5)

    monkeypatch.setattr(subprocess, "run", fail)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 2
    assert "DRIVER_SECRET" not in result.output
    check = next(c for c in json.loads(result.stdout)["checks"] if c["id"] == "browser")
    assert check["status"] == "unknown"


@pytest.mark.parametrize(
    "arguments",
    [
        ["checks", "run", "--help"],
        ["report", "generate", "--help"],
        ["retest", "run", "--help"],
        ["target", "list", "--help"],
        ["db", "upgrade", "--help"],
        ["scan", "--help"],
        ["coverage", "--help"],
    ],
)
def test_lazy_command_loading_preserves_public_command_paths(arguments):
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0, result.output
    assert "Usage: garden " + " ".join(arguments[:-1]) in result.output
