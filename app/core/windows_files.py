"""Win32 handles for local locks and protected files."""

from contextlib import ExitStack, contextmanager
from pathlib import Path

import ntsecuritycon
import pywintypes
import win32con
import win32file
import winerror


@contextmanager
def open_local_path(path, access, creation, *, directory=False):
    flags = win32file.FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= win32con.FILE_FLAG_BACKUP_SEMANTICS
    try:
        handle = win32file.CreateFile(
            str(path),
            access,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            None,
            creation,
            flags,
            None,
        )
        try:
            attributes = win32file.GetFileInformationByHandle(handle)[0]
            if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT or (
                bool(attributes & win32con.FILE_ATTRIBUTE_DIRECTORY) != directory
            ):
                raise OSError(f"Unsupported local path: {path}")
            yield handle
        finally:
            handle.Close()
    except pywintypes.error as error:
        raise OSError(f"{error.funcname}: {error.strerror}") from error


@contextmanager
def windows_file_lock(path, *, blocking):
    with open_local_path(
        path, win32con.GENERIC_READ | win32con.GENERIC_WRITE, win32con.OPEN_ALWAYS
    ) as handle:
        flags = win32con.LOCKFILE_EXCLUSIVE_LOCK
        if not blocking:
            flags |= win32con.LOCKFILE_FAIL_IMMEDIATELY
        offset = pywintypes.OVERLAPPED()
        try:
            win32file.LockFileEx(handle, flags, 1, 0, offset)
        except pywintypes.error as error:
            if error.winerror == winerror.ERROR_LOCK_VIOLATION:
                raise BlockingIOError from error
            raise
        try:
            yield
        finally:
            win32file.UnlockFileEx(handle, 1, 0, offset)


@contextmanager
def hold_local_directory(path, *, create):
    # Hold each directory without delete sharing while resolving the next
    # component. Junctions, symlinks and other reparse points are rejected.
    with ExitStack() as stack:
        directory = Path(path.anchor)
        for component in path.parts[1:]:
            directory /= component
            if create:
                directory.mkdir(exist_ok=True)
            stack.enter_context(
                open_local_path(
                    directory,
                    ntsecuritycon.FILE_READ_ATTRIBUTES,
                    win32con.OPEN_EXISTING,
                    directory=True,
                )
            )
        yield directory


def read_local_file(path, limit):
    with open_local_path(path, win32con.GENERIC_READ, win32con.OPEN_EXISTING) as handle:
        _, data = win32file.ReadFile(handle, limit)
        return data
