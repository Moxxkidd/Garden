import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import NoSuchModuleError
from sqlalchemy.orm import Session
from typer.testing import CliRunner

import app.models  # noqa: F401
from app.cli.main import app as cli_app
from app.core.settings import clear_settings_cache, get_settings
from app.db.base import Base
from app.db.bootstrap import (
    reset_database_state,
    stamp_existing_database,
    upgrade_database,
)
from app.main import create_app
from app.models.target import Target

runner = CliRunner()
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_upgrade_database_creates_current_schema(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'fresh.db'}"

    upgrade_database(url)

    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"alembic_version", "scan_runs", "scan_jobs", "inventory_runs"} <= tables


def test_stamp_existing_database_preserves_rows(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Target(
                name="legacy-target",
                base_url="http://localhost:8080",
                type="web",
                owner="appsec",
                tags=["legacy"],
                status="active",
            )
        )
        session.commit()

    stamp_existing_database(url)
    upgrade_database(url)

    with engine.connect() as connection:
        assert connection.scalar(text("select count(*) from targets")) == 1
        assert connection.scalar(text("select version_num from alembic_version")) == "0001"


def test_application_requires_explicit_stamp_for_unversioned_legacy_database(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'legacy-app.db'}"
    Base.metadata.create_all(create_engine(url))
    monkeypatch.setenv("GARDEN_DATABASE_URL", url)
    monkeypatch.setenv("GARDEN_DATABASE_AUTO_MIGRATE", "false")
    clear_settings_cache()
    reset_database_state()

    with pytest.raises(RuntimeError, match="gardenctl db stamp-existing"):
        with TestClient(create_app()):
            pass


def test_application_requires_upgrade_when_database_is_not_at_head(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'empty-app.db'}"
    monkeypatch.setenv("GARDEN_DATABASE_URL", url)
    monkeypatch.setenv("GARDEN_DATABASE_AUTO_MIGRATE", "false")
    clear_settings_cache()
    reset_database_state()

    with pytest.raises(RuntimeError, match="gardenctl db upgrade"):
        with TestClient(create_app()):
            pass


def test_application_rejects_auto_migration_outside_development(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'production.db'}"
    upgrade_database(url)
    monkeypatch.setenv("GARDEN_DATABASE_URL", url)
    monkeypatch.setenv("GARDEN_DATABASE_AUTO_MIGRATE", "true")
    monkeypatch.setenv("GARDEN_ENVIRONMENT", "production")
    clear_settings_cache()
    reset_database_state()

    with pytest.raises(RuntimeError, match="only allowed in development"):
        with TestClient(create_app()):
            pass


def test_application_auto_migrates_fresh_database_in_development(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'development.db'}"
    monkeypatch.setenv("GARDEN_DATABASE_URL", url)
    monkeypatch.setenv("GARDEN_DATABASE_AUTO_MIGRATE", "true")
    monkeypatch.setenv("GARDEN_ENVIRONMENT", "development")
    clear_settings_cache()
    reset_database_state()

    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200

    with create_engine(url).connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version")) == "0001"


def test_database_cli_upgrade_and_current(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("GARDEN_DATABASE_URL", url)
    clear_settings_cache()
    reset_database_state()

    upgrade_result = runner.invoke(cli_app, ["db", "upgrade"])
    current_result = runner.invoke(cli_app, ["db", "current"])

    assert upgrade_result.exit_code == 0
    assert "0001" in current_result.stdout


def test_database_cli_stamp_existing_preserves_legacy_rows(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'cli-legacy.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Target(
                name="cli-legacy-target",
                base_url="http://localhost:8081",
                type="web",
                owner="appsec",
                tags=["legacy"],
                status="active",
            )
        )
        session.commit()
    monkeypatch.setenv("GARDEN_DATABASE_URL", url)
    clear_settings_cache()
    reset_database_state()

    result = runner.invoke(cli_app, ["db", "stamp-existing"])

    assert result.exit_code == 0
    with engine.connect() as connection:
        assert connection.scalar(text("select count(*) from targets")) == 1
        assert connection.scalar(text("select version_num from alembic_version")) == "0001"


def test_database_auto_migrate_defaults_to_false():
    assert get_settings().database_auto_migrate is False


def test_built_wheel_installs_with_loadable_migration_assets(tmp_path):
    wheel_dir = tmp_path / "wheelhouse"
    wheel_dir.mkdir()
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(PROJECT_ROOT),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr

    wheel_path = next(wheel_dir.glob("garden-*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel:
        packaged_paths = set(wheel.namelist())
    expected_paths = {
        "app/db/migration_assets/alembic.ini",
        "app/db/migration_assets/migrations/env.py",
        "app/db/migration_assets/migrations/script.py.mako",
        "app/db/migration_assets/migrations/versions/0001_existing_schema_baseline.py",
    }
    assert expected_paths <= packaged_paths

    install_target = tmp_path / "installed"
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(install_target),
            str(wheel_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert install.returncode == 0, install.stderr

    database_path = tmp_path / "installed-wheel.db"
    verify_script = """
import sys
from pathlib import Path

import app
from sqlalchemy import create_engine, inspect

from app.db.bootstrap import upgrade_database

install_target = Path(sys.argv[1]).resolve()
assert Path(app.__file__).resolve().is_relative_to(install_target)
database_url = f"sqlite+pysqlite:///{sys.argv[2]}"
upgrade_database(database_url)
assert "alembic_version" in inspect(create_engine(database_url)).get_table_names()
"""
    verify_environment = os.environ.copy()
    verify_environment["PYTHONPATH"] = str(install_target)
    verify = subprocess.run(
        [sys.executable, "-c", verify_script, str(install_target), str(database_path)],
        cwd=tmp_path,
        env=verify_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr


def test_percent_encoded_non_sqlite_url_fails_without_leaking_credentials(capsys):
    encoded_password = "encoded-secret%40value%2Fpart"
    database_url = (
        f"postgresql+gardenmissing://garden:{encoded_password}@db.example.invalid/garden"
    )

    with pytest.raises(NoSuchModuleError) as exc_info:
        upgrade_database(database_url)

    captured = capsys.readouterr()
    assert encoded_password not in str(exc_info.value)
    assert encoded_password not in captured.out
    assert encoded_password not in captured.err
