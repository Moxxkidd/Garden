import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import NoSuchModuleError
from sqlalchemy.orm import Session
from typer.testing import CliRunner

import app.models  # noqa: F401
from app.cli.main import app as cli_app
from app.core.settings import clear_settings_cache, get_settings
from app.db.bootstrap import (
    reset_database_state,
    stamp_existing_database,
    upgrade_database,
)
from app.main import create_app
from app.models.target import Target

runner = CliRunner()
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def create_unversioned_legacy_database(url: str):
    """构造仅包含 0001 schema、但尚未登记 Alembic 版本的历史数据库。"""
    upgrade_database(url, "0001")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("drop table alembic_version"))
    return engine


def downgrade_database(url: str, revision: str) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.downgrade(config, revision)


def test_upgrade_database_creates_current_schema(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'fresh.db'}"

    upgrade_database(url)

    tables = set(inspect(create_engine(url)).get_table_names())
    assert {
        "alembic_version",
        "scan_runs",
        "scan_contexts",
        "scan_requests",
        "coverage_differences",
        "replay_executions",
        "scan_jobs",
        "inventory_runs",
    } <= tables
    context_columns = {
        column["name"] for column in inspect(create_engine(url)).get_columns("scan_contexts")
    }
    assert "temporary_secret_ref" in context_columns


def test_stamp_existing_database_preserves_rows(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}"
    engine = create_unversioned_legacy_database(url)
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
        assert connection.scalar(text("select version_num from alembic_version")) == "0015"


def test_legacy_scan_assets_are_backfilled_into_anonymous_context(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'legacy-assets.db'}"
    engine = create_unversioned_legacy_database(url)
    with engine.begin() as connection:
        run_id = connection.execute(
            text(
                """
                insert into scan_runs (
                    input_url, normalized_url, active_key, status, current_stage,
                    progress, options, retry_count, report_path, error_code,
                    error_message, started_at, finished_at, report_generated_at,
                    created_at, updated_at
                ) values (
                    :input_url, :normalized_url, null, 'completed', 'report',
                    100, '{}', 0, null, null, null, null, null, null,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "input_url": "http://localhost:8080/items?token=secret&id=7",
                "normalized_url": "http://localhost:8080/items?id=7&token=secret",
            },
        ).lastrowid
        asset_id = connection.execute(
            text(
                """
                insert into scan_assets (
                    scan_run_id, asset_type, url, method, status_code, title,
                    attributes, discovered_at
                ) values (
                    :run_id, 'page', :url, 'GET', 200, 'Items', '{}', CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "run_id": run_id,
                "url": "http://localhost:8080/items?token=secret&id=7",
            },
        ).lastrowid

    stamp_existing_database(url)
    upgrade_database(url)

    with engine.connect() as connection:
        run = (
            connection.execute(
                text("select mode, completeness from scan_runs where id = :run_id"),
                {"run_id": run_id},
            )
            .mappings()
            .one()
        )
        context = (
            connection.execute(
                text("select id, kind, asset_count from scan_contexts where scan_run_id = :run_id"),
                {"run_id": run_id},
            )
            .mappings()
            .one()
        )
        asset = (
            connection.execute(
                text("select context_id, identity_key from scan_assets where id = :asset_id"),
                {"asset_id": asset_id},
            )
            .mappings()
            .one()
        )

    assert run == {"mode": "quick", "completeness": "complete"}
    assert context["kind"] == "anonymous"
    assert context["asset_count"] == 1
    assert asset["context_id"] == context["id"]
    assert asset["identity_key"] == "GET:/items?id&token"


def test_unified_assessment_migration_round_trip(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'round-trip.db'}"
    upgrade_database(url)

    downgrade_database(url, "0001")

    downgraded_tables = set(inspect(create_engine(url)).get_table_names())
    assert "scan_contexts" not in downgraded_tables
    assert "scan_requests" not in downgraded_tables
    assert "coverage_differences" not in downgraded_tables
    assert "replay_executions" not in downgraded_tables

    upgrade_database(url)
    with create_engine(url).connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version")) == "0015"


def test_application_requires_explicit_stamp_for_unversioned_legacy_database(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'legacy-app.db'}"
    create_unversioned_legacy_database(url)
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
        assert connection.scalar(text("select version_num from alembic_version")) == "0015"


def test_database_cli_upgrade_and_current(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("GARDEN_DATABASE_URL", url)
    clear_settings_cache()
    reset_database_state()

    upgrade_result = runner.invoke(cli_app, ["db", "upgrade"])
    current_result = runner.invoke(cli_app, ["db", "current"])

    assert upgrade_result.exit_code == 0
    assert "0015" in current_result.stdout


def test_database_cli_stamp_existing_preserves_legacy_rows(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'cli-legacy.db'}"
    engine = create_unversioned_legacy_database(url)
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
        "app/db/migration_assets/migrations/versions/0002_unified_assessment.py",
        "app/db/migration_assets/migrations/versions/0003_scan_rerun_link.py",
        "app/db/migration_assets/migrations/versions/0004_coverage_gap_details.py",
        "app/db/migration_assets/migrations/versions/0005_asset_method_identity.py",
        "app/db/migration_assets/migrations/versions/0006_asset_validity_metadata.py",
        "app/db/migration_assets/migrations/versions/0007_discovery_provenance.py",
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
    database_url = f"postgresql+gardenmissing://garden:{encoded_password}@db.example.invalid/garden"

    with pytest.raises(NoSuchModuleError) as exc_info:
        upgrade_database(database_url)

    captured = capsys.readouterr()
    assert encoded_password not in str(exc_info.value)
    assert encoded_password not in captured.out
    assert encoded_password not in captured.err


def test_rerun_link_migration_preserves_existing_run_and_round_trips(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'rerun.db'}"
    upgrade_database(url, "0002")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO scan_runs (id, input_url, normalized_url, status, current_stage,
                progress, options, retry_count, created_at, updated_at)
            VALUES (1, 'http://localhost/', 'http://localhost/', 'completed', 'report',
                100, '{"max_pages": 7}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        )
    upgrade_database(url)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT input_url, options, rerun_of_run_id FROM scan_runs WHERE id=1")
        ).one()
        assert tuple(row) == ("http://localhost/", '{"max_pages": 7}', None)
    foreign_keys = inspect(engine).get_foreign_keys("scan_runs")
    assert any(fk["constrained_columns"] == ["rerun_of_run_id"] for fk in foreign_keys)
    downgrade_database(url, "0002")
    assert "rerun_of_run_id" not in {c["name"] for c in inspect(engine).get_columns("scan_runs")}
    upgrade_database(url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM scan_runs")) == 1


def test_coverage_gap_migration_preserves_legacy_warning_and_round_trips(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'gaps.db'}"
    upgrade_database(url, "0003")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO scan_runs (id, input_url, normalized_url, status, current_stage,
                progress, options, retry_count, created_at, updated_at)
            VALUES (1, 'http://localhost/', 'http://localhost/', 'completed_with_warnings',
                'report', 100, '{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        )
        connection.execute(
            text("""
            INSERT INTO scan_failures (id, scan_run_id, stage, code, message,
                retryable, attempt, occurred_at)
            VALUES (1, 1, 'collect', 'coverage_limit_reached', 'legacy warning',
                0, 1, CURRENT_TIMESTAMP)
        """)
        )
    upgrade_database(url)
    with engine.connect() as connection:
        assert tuple(
            connection.execute(
                text("SELECT message, coverage_details FROM scan_failures WHERE id=1")
            ).one()
        ) == ("legacy warning", None)
    downgrade_database(url, "0003")
    assert "coverage_details" not in {
        column["name"] for column in inspect(engine).get_columns("scan_failures")
    }
    upgrade_database(url)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT message FROM scan_failures WHERE id=1"))
            == "legacy warning"
        )


def test_asset_identity_migration_keeps_ids_requests_and_evidence_and_guards_downgrade(tmp_path):
    from datetime import datetime, timezone

    from app.models.scan_run import ScanAsset, ScanEvidence

    url = f"sqlite+pysqlite:///{tmp_path / 'asset-method.db'}"
    upgrade_database(url, "0004")
    engine = create_engine(url)
    with Session(engine) as session:
        from types import SimpleNamespace

        run = SimpleNamespace(id=1)
        session.execute(
            text(
                """INSERT INTO scan_runs
                (id, input_url, normalized_url, status, current_stage, progress,
                 options, retry_count, created_at, updated_at)
                VALUES (1, 'https://site.test/', 'https://site.test/',
                        'completed', 'report', 100, '{}', 0,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""
            )
        )
        context = SimpleNamespace(id=1)
        session.execute(
            text("""INSERT INTO scan_contexts
            (id,scan_run_id,kind,created_at,updated_at)
            VALUES (1,1,'anonymous',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        )
        asset = ScanAsset(
            scan_run_id=run.id,
            context_id=context.id,
            asset_type="endpoint",
            url="https://site.test/api",
            method="GET",
            status_code=200,
            discovered_at=datetime.now(timezone.utc),
        )
        session.add(asset)
        session.flush()
        # Seed the actual 0004 schema, without today's ORM-only columns.
        session.execute(
            text("""INSERT INTO scan_requests
            (scan_run_id,source_context_id,asset_id,method,normalized_redacted_url,
             header_names,fingerprint,replay_allowed,replay_status,protected_storage_ref,
             created_at,updated_at)
            VALUES (:run_id,:context_id,:asset_id,'GET','https://site.test/api',
                    '[]','legacy',0,'pending','protected-ref',
                    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""),
            {"run_id": run.id, "context_id": context.id, "asset_id": asset.id},
        )
        session.add(
            ScanEvidence(
                scan_run_id=run.id,
                asset_id=asset.id,
                evidence_type="http",
                title="legacy",
                source_url=asset.url,
                summary="retained",
                data={},
                collected_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
        ids = (run.id, context.id, asset.id)
    upgrade_database(url, "0005")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT asset_id FROM scan_requests")) == ids[2]
        assert connection.scalar(text("SELECT asset_id FROM scan_evidence")) == ids[2]
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    downgrade_database(url, "0004")
    upgrade_database(url, "0005")
    with Session(engine) as session:
        session.add(
            ScanAsset(
                scan_run_id=ids[0],
                context_id=ids[1],
                asset_type="endpoint",
                url="https://site.test/api",
                method="POST",
                status_code=403,
                discovered_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        downgrade_database(url, "0004")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM scan_assets")) == 2
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"


def test_validity_migration_preserves_legacy_rows_and_unknown_metadata(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'validity.db'}"
    upgrade_database(url, "0005")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO scan_runs
            (id, input_url, normalized_url, status, current_stage, progress,
             options, retry_count, created_at, updated_at)
            VALUES (7, 'https://site.test/', 'https://site.test/', 'completed',
                    'report', 100, '{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""")
        )
    for _ in range(2):
        upgrade_database(url, "0006")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT id, asset_metadata FROM scan_runs")).one() == (
                7,
                None,
            )
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        for table in ["inventory_pages", "inventory_endpoints"]:
            columns = {c["name"]: c for c in inspect(engine).get_columns(table)}
            assert columns["response_traits"]["nullable"]
        downgrade_database(url, "0005")
        assert "asset_metadata" not in {c["name"] for c in inspect(engine).get_columns("scan_runs")}


def test_discovery_migration_preserves_nullable_legacy_metadata(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'discovery.db'}"
    upgrade_database(url, "0006")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO scan_runs
            (id,input_url,normalized_url,status,current_stage,progress,options,retry_count,
             created_at,updated_at,asset_metadata)
            VALUES (1,'https://site.test/','https://site.test/','completed','report',100,'{}',0,
                    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'{"version": 1,"candidates":[]}')""")
        )
    for _ in range(2):
        upgrade_database(url, "0007")
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT id, discovery_input_ref, asset_metadata FROM scan_runs")
            ).one()
            assert row[0:2] == (1, None)
            assert '"version": 1' in row[2]
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        downgrade_database(url, "0006")


@pytest.mark.parametrize("shape", ["missing", "manual", "normal"])
def test_known_context_drift_upgrade_preserves_data_and_uniqueness(tmp_path, shape):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    database = tmp_path / "drift.db"
    url = f"sqlite:///{database}"
    upgrade_database(url, "0011")
    engine = create_engine(url)
    # Construct the known historical omission from the actual historical migrations.
    if shape != "normal":
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table("scan_contexts") as batch:
                batch.drop_constraint("uq_scan_contexts_temporary_secret_ref", type_="unique")
                if shape == "missing":
                    batch.drop_column("temporary_secret_ref")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO targets "
                "(name, base_url, type, owner, tags, status, created_at, updated_at) "
                "VALUES ('keep', 'http://localhost', 'web', 'fixture', '[]', 'active', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    backup = upgrade_database(url)
    assert backup is not None and backup.is_file()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT name FROM targets")) == "keep"
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0015"
    inspector = inspect(engine)
    assert "temporary_secret_ref" in {c["name"] for c in inspector.get_columns("scan_contexts")}
    unique = inspector.get_unique_constraints("scan_contexts") + [
        i for i in inspector.get_indexes("scan_contexts") if i["unique"]
    ]
    assert any(i["column_names"] == ["temporary_secret_ref"] for i in unique)
    before = database.read_bytes()
    assert upgrade_database(url) is None
    assert database.read_bytes() == before
    engine.dispose()


def test_workbench_upgrade_preserves_legacy_annotation_without_inventing_review(tmp_path):
    url = f"sqlite:///{tmp_path / 'workbench-upgrade.db'}"
    upgrade_database(url, "0012")
    engine = create_engine(url)
    with engine.begin() as connection:
        # Minimal synthetic previous-version records; old tags do not imply formal review.
        connection.execute(
            text(
                "INSERT INTO projects(id,name,description,status,revision,created_at,updated_at) "
                "VALUES (1,'fixture','','active',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO project_assets(id,project_id,natural_key,rule_version,site,"
                "method,kind,"
                "original_type,url,manual,created_at,updated_at) VALUES "
                "('fixture',1,'fixture','project-route-v1','http://localhost','GET','page','page',"
                "'http://localhost/',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO project_annotations(asset_id,tags,note,created_at,updated_at) VALUES "
                "('fixture','[\"reviewed\"]','keep old note',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
    backup = upgrade_database(url)
    assert backup and backup.is_file()
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT tags,note,review_state,reviewed_at,needs_review FROM project_annotations")
        ).one()
        assert tuple(row) == ('["reviewed"]', "keep old note", "unreviewed", None, 0)
        assert connection.scalar(text("SELECT COUNT(*) FROM project_saved_views")) == 0
    engine.dispose()


def test_identity_plan_migration_preserves_old_request_source_as_unknown(tmp_path):
    url = f"sqlite:///{tmp_path / 'identity-upgrade.db'}"
    upgrade_database(url, "0013")
    engine = create_engine(url)
    with engine.begin() as connection:
        # These rows are synthetic; do not infer how an old captured request originated.
        connection.execute(
            text(
                "INSERT INTO scan_runs(id,input_url,normalized_url,status,current_"
                "stage,progress,options,retry_count,created_at,updated_at) VALUES "
                "(1,'http://localhost/','http://localhost/','completed','report',1"
                "00,'{}',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO scan_contexts(id,scan_run_id,kind,context_key,status,"
                "login_status,session_validation_status,collection_status,complete"
                "ness,asset_count,request_count,failure_count,created_at,updated_a"
                "t) VALUES (1,1,'anonymous','anonymous','completed','not_required'"
                ",'not_required','completed','complete',0,0,0,CURRENT_TIMESTAMP,CU"
                "RRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO scan_requests(scan_run_id,source_context_id,method,no"
                "rmalized_redacted_url,header_names,fingerprint,replay_allowed,rep"
                "lay_status,protected_storage_ref,created_at,updated_at) VALUES (1"
                ",1,'GET','http://localhost/','[]','synthetic',0,'pending','',CURR"
                "ENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
    backup = upgrade_database(url)
    assert backup and backup.is_file()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT capture_source FROM scan_requests")) is None
        assert connection.scalar(text("SELECT COUNT(*) FROM identity_plans")) == 0
        assert connection.scalar(text("SELECT method FROM scan_requests")) == "GET"
    engine.dispose()
