"""Single-machine executor ownership; construction and reads create no files."""

import os
import threading
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.errors import ConflictError, InputValidationError

_owners = {}
_mutex = threading.RLock()


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
        import fcntl

        with _mutex:
            if self.held:
                return
            if self.pid != os.getpid():
                raise ConflictError("collection_executor_forked")
            key = (self.pid, self.key)
            if key in _owners:
                _owners[key][1] += 1
            else:
                descriptor = None
                if self.key:
                    descriptor = os.open(self.key, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        os.close(descriptor)
                        raise ConflictError("collection_executor_busy") from None
                _owners[key] = [descriptor, 1]
            self.held = True

    def close(self):
        import fcntl

        with _mutex:
            if not self.held or self.pid != os.getpid():
                return
            self.held = False
            key = (self.pid, self.key)
            owner = _owners[key]
            owner[1] -= 1
            if not owner[1]:
                if owner[0] is not None:
                    fcntl.flock(owner[0], fcntl.LOCK_UN)
                    os.close(owner[0])
                del _owners[key]

    def __del__(self):
        # Kernel ownership also disappears if the entire process terminates abruptly.
        try:
            self.close()
        except Exception:
            pass
