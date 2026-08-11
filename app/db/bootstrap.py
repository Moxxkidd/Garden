"""Database bootstrap and session factory."""

from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger

logger = get_logger(__name__)

_DATABASE_URL: str | None = None
_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def _migration_assets_root() -> Path:
    package_root = Path(__file__).resolve().parent / "migration_assets"
    if package_root.is_dir():
        return package_root
    return Path(__file__).resolve().parents[2]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_migration_assets_root() / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_database(database_url: str, revision: str = "head") -> None:
    _ensure_sqlite_directory(database_url)
    command.upgrade(_alembic_config(database_url), revision)


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
