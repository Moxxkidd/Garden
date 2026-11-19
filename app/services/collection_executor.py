"""Single-machine executor ownership; construction and reads create no files."""

import os
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.errors import ConflictError, InputValidationError
from app.core.local_files import local_file_lock

_owners = {}
_mutex = threading.RLock()
_operation_mutex = threading.RLock()
_quiescing = set()


class CollectionExecutor:
    def __init__(self, database_url):
        database = make_url(database_url)
        if database.get_backend_name() != "sqlite":
            raise InputValidationError("collection_requires_local_sqlite")
        path = database.database
        self.key = (
            str(Path(path).resolve()) + ".executor.lock" if path and path != ":memory:" else None
        )
        self.held = False
        self.pid = os.getpid()

    def acquire(self):
        with _mutex:
            if self.held:
                return
            if self.pid != os.getpid():
                raise ConflictError("collection_executor_forked")
            key = (self.pid, self.key)
            if key in _owners:
                _owners[key][1] += 1
            else:
                lock = None
                if self.key:
                    lock = local_file_lock(self.key, blocking=False)
                    try:
                        lock.__enter__()
                    except BlockingIOError:
                        raise ConflictError("collection_executor_busy") from None
                _owners[key] = [lock, 1]
            self.held = True

    def close(self):
        with _mutex:
            if not self.held or self.pid != os.getpid():
                return
            self.held = False
            key = (self.pid, self.key)
            lock, count = _owners[key]
            if count == 1:
                del _owners[key]
                if lock is not None:
                    lock.__exit__(None, None, None)
            else:
                _owners[key][1] -= 1

    def __del__(self):
        # Kernel ownership also disappears if the entire process terminates abruptly.
        try:
            self.close()
        except Exception:
            pass


@contextmanager
def executor_operation(database_url):
    """Serialize admission/maintenance under the existing process ownership lock."""
    # A submission can wait for browser/HTTP workers. Keep owner bookkeeping
    # available to their cleanup/finalizers while the operation stays serialized.
    with _operation_mutex:
        owner = CollectionExecutor(database_url)
        owner.acquire()
        try:
            yield
        finally:
            owner.close()


def guarded_submission(function):
    @wraps(function)
    def invoke(self, *args, **kwargs):
        with executor_operation(self.settings.database_url):
            if self.settings.database_url in _quiescing:
                raise ConflictError("local_runtime_stopping")
            self.ensure_collection_executor()
            return function(self, *args, **kwargs)

    return invoke


def quiesce_submissions(database_url, enabled):
    """Called under executor_operation by the managed local shutdown/update gate."""
    if enabled:
        _quiescing.add(database_url)
    else:
        _quiescing.discard(database_url)
