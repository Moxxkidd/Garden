"""Explicit SQLite transaction boundaries for project snapshots and savepoints."""

from contextlib import contextmanager


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
    except Exception:
        # Bulk revision updates are not tracked as dirty ORM attributes.
        session.expire_all()
        raise
