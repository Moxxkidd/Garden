"""Strict protected payloads with content proof and atomic file publication."""

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.core.errors import InputValidationError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class CollectionStorage:
    def __init__(self, directory=None):
        # Do not create directories merely to construct a read-only service.
        self.directory = Path(directory) if directory else None

    @contextmanager
    def _directory(self, write=False):
        if self.directory is None:
            from app.cli.paths import formal_runtime_paths

            paths = formal_runtime_paths()
            self.directory = (paths.storage_dir if paths else Path.cwd() / "data") / "collection"
        # Keep each parent open while resolving the next component. O_NOFOLLOW
        # on the payload alone does not stop a symlink in its parent path.
        path = Path(os.path.abspath(self.directory))
        descriptor = None
        try:
            descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
            for component in path.parts[1:]:
                if write:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = child
            if write:
                os.fchmod(descriptor, 0o700)
            yield descriptor
        except OSError:
            raise InputValidationError("collection_storage_unavailable") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def write(self, payload):
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
        if len(encoded) > 2 * 1024 * 1024:
            raise InputValidationError("collection_payload_too_large")
        ref = uuid4().hex + ".json"
        with self._directory(write=True) as directory:
            descriptor = os.open(
                ref, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.fsync(directory)
            except BaseException:
                os.unlink(ref, dir_fd=directory)
                raise
        return ref, hashlib.sha256(encoded).hexdigest()

    def read(self, ref, expected_digest):
        try:
            if not re.fullmatch(r"[0-9a-f]{32}\.json", ref):
                raise ValueError
            with self._directory() as directory:
                descriptor = os.open(
                    ref, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
                with os.fdopen(descriptor, "rb") as handle:
                    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                        raise ValueError
                    raw = handle.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_digest:
                raise ValueError
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (OSError, ValueError, TypeError, InputValidationError):
            raise InputValidationError("collection_payload_unavailable") from None
