"""File locks and permissions for local, current-user Garden data."""

import os
from contextlib import contextmanager


@contextmanager
def local_file_lock(path, *, blocking):
    if os.name == "nt":
        from app.core.windows_files import windows_file_lock

        with windows_file_lock(path, blocking=blocking):
            yield
        return
    import fcntl

    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(descriptor, flags)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def private_file_mode(handle):
    # Windows uses the ACL inherited from the selected private data folder.
    if os.name != "nt":
        os.fchmod(handle.fileno(), 0o600)
