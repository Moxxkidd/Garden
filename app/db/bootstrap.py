"""Database bootstrap and session factory."""

import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger

logger = get_logger(__name__)

_DATABASE_URL: str | None = None
_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    from app.services.discovery_scheduling import display_template_key

    dbapi_connection.create_function(
        "garden_path_family", 2, display_template_key, deterministic=True
    )
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def _migration_assets_root() -> Path:
    package_root = Path(__file__).resolve().parent / "migration_assets"
    if package_root.is_dir():
        return package_root
    return Path(__file__).resolve().parents[2]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_migration_assets_root() / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_database(database_url: str, revision: str = "head") -> Path | None:
    config = _alembic_config(database_url)
    target = ScriptDirectory.from_config(config).get_revision(revision)
    url = make_url(database_url)
    backup = None
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        path = Path(url.database).absolute()
        if path.is_file():
            if get_database_revision(database_url) == target.revision:
                return None
            # SQLite backup also captures committed WAL data; a plain file copy does not.
            with tempfile.NamedTemporaryFile(
                prefix=path.name + ".pre-upgrade-", suffix=".bak", dir=path.parent, delete=False
            ) as file:
                backup = Path(file.name)
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(backup)) as destination:
                    source.backup(destination)
    _ensure_sqlite_directory(database_url)
    command.upgrade(config, revision)
    return backup


def stamp_existing_database(database_url: str, revision: str = "0001") -> None:
    _ensure_sqlite_directory(database_url)
    command.stamp(_alembic_config(database_url), revision)


def get_database_revision(database_url: str) -> str | None:
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def ensure_database_at_head(
    database_url: str,
    *,
    environment: str,
    auto_migrate: bool,
) -> None:
    if auto_migrate and environment.lower() != "development":
        raise RuntimeError("GARDEN_DATABASE_AUTO_MIGRATE=true is only allowed in development.")

    _ensure_sqlite_directory(database_url)
    config = _alembic_config(database_url)
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    engine = create_engine(database_url, future=True)
    try:
        table_names = set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            current_revision = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()

    if current_revision is None and table_names - {"alembic_version"}:
        raise RuntimeError(
            "Database contains an unversioned existing schema. "
            "Run `gardenctl db stamp-existing` before starting Garden."
        )

    if current_revision == head_revision:
        inspector = inspect(create_engine(database_url))
        try:
            columns = {c["name"] for c in inspector.get_columns("scan_contexts")}
            unique = inspector.get_unique_constraints("scan_contexts") + [
                i for i in inspector.get_indexes("scan_contexts") if i["unique"]
            ]
            if "temporary_secret_ref" not in columns or not any(
                i["column_names"] == ["temporary_secret_ref"] for i in unique
            ):
                raise RuntimeError(
                    "scan_contexts.temporary_secret_ref 列或唯一约束缺失。"
                    "请先备份并核对迁移记录；不要覆盖版本号或盲目升级。"
                )
        finally:
            inspector.bind.dispose()
        return
    if auto_migrate:
        upgrade_database(database_url)
        return
    raise RuntimeError(
        "Database schema is not at the current migration head. "
        "Run `gardenctl db upgrade` before starting Garden."
    )


def init_database(database_url: str) -> None:
    global _DATABASE_URL, _ENGINE, _SESSION_FACTORY
    if _ENGINE is not None and _SESSION_FACTORY is not None and _DATABASE_URL == database_url:
        return
    if _ENGINE is not None and _DATABASE_URL != database_url:
        _ENGINE.dispose()
        _ENGINE = None
        _SESSION_FACTORY = None

    _ensure_sqlite_directory(database_url)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    _ENGINE = create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    if database_url.startswith("sqlite"):
        event.listen(_ENGINE, "connect", _enable_sqlite_foreign_keys)
    _SESSION_FACTORY = sessionmaker(
        bind=_ENGINE,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    _DATABASE_URL = database_url
    logger.info("Database initialized")


def get_engine() -> Engine:
    if _ENGINE is None:
        raise RuntimeError("Database has not been initialized.")
    return _ENGINE


def get_session() -> Session:
    if _SESSION_FACTORY is None:
        raise RuntimeError("Database session factory has not been initialized.")
    return _SESSION_FACTORY()


@contextmanager
def session_scope() -> Session:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database() -> str:
    with get_session() as session:
        session.execute(text("SELECT 1"))
    return "ok"


def reset_database_state() -> None:
    global _DATABASE_URL, _ENGINE, _SESSION_FACTORY
    if _ENGINE is not None:
        _ENGINE.dispose()
    _DATABASE_URL = None
    _ENGINE = None
    _SESSION_FACTORY = None


def _ensure_sqlite_directory(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    relative_path = database_url.split("///", maxsplit=1)[-1]
    db_path = Path(relative_path)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
