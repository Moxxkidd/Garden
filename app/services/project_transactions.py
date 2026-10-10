"""Explicit SQLite transaction boundaries for project snapshots and savepoints."""

from contextlib import contextmanager

from sqlalchemy.exc import OperationalError

from app.core.errors import ConflictError


def ensure_read_snapshot(session):
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        # Legacy pysqlite does not begin on SELECT or SAVEPOINT; a released
        # outermost SAVEPOINT would otherwise commit behind the caller's back.
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")


@contextmanager
def atomic_import(session):
    ensure_read_snapshot(session)
    try:
        with session.begin_nested():
            yield
    except OperationalError as error:
        session.expire_all()
        # Python 3.10 lacks sqlite_errorcode. Only translate SQLite lock failures;
        # unrelated SQL errors must remain visible to diagnostics.
        if session.get_bind().dialect.name == "sqlite" and (
            getattr(error.orig, "sqlite_errorcode", 0) & 255 in {5, 6}
            or str(error.orig) in {"database is locked", "database table is locked"}
        ):
            raise ConflictError("project_concurrent_update") from None
        raise
    except Exception:
        # Bulk revision updates are not tracked as dirty ORM attributes.
        session.expire_all()
        raise
