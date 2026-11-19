"""Read-only process liveness for persisted local runtime records."""

import os


def check_process(pid):
    """Raise ProcessLookupError when the PID is gone, leaving access errors visible."""
    if not 0 < pid <= 2**31 - 1:
        raise ProcessLookupError(pid)
    if os.name != "nt":
        os.kill(pid, 0)
        return

    # On Windows os.kill(pid, 0) terminates the process with exit code zero.
    import pywintypes
    import win32api
    import win32con
    import win32event
    import winerror

    try:
        handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid)
        try:
            if win32event.WaitForSingleObject(handle, 0) == win32con.WAIT_OBJECT_0:
                raise ProcessLookupError(pid)
        finally:
            handle.Close()
    except pywintypes.error as error:
        if error.winerror == winerror.ERROR_INVALID_PARAMETER:
            raise ProcessLookupError(pid) from error
        if error.winerror == winerror.ERROR_ACCESS_DENIED:
            raise PermissionError(pid) from error
        raise OSError(f"{error.funcname}: {error.strerror}") from error
