"""Reuse explicit SQLite savepoints while exposing collection-specific conflicts."""

from contextlib import contextmanager

from app.core.errors import ConflictError
from app.services.project_transactions import atomic_import


@contextmanager
def atomic_collection(session):
    try:
        with atomic_import(session):
            yield
    except ConflictError as error:
        if str(error) == "project_concurrent_update":
            raise ConflictError("collection_concurrent_update") from None
        raise
