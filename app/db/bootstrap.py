"""Database bootstrap and session factory."""

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.db.base import Base

logger = get_logger(__name__)

_DATABASE_URL: str | None = None
_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def init_database(database_url: str) -> None:
    global _DATABASE_URL, _ENGINE, _SESSION_FACTORY
    if _ENGINE is not None and _SESSION_FACTORY is not None and _DATABASE_URL == database_url:
        return
    if _ENGINE is not None and _DATABASE_URL != database_url:
        _ENGINE.dispose()
        _ENGINE = None
        _SESSION_FACTORY = None

    _ensure_sqlite_directory(database_url)
    import app.models  # noqa: F401

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
    Base.metadata.create_all(_ENGINE)
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
