"""Strict protected payloads with content proof and atomic file publication."""

import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from app.core.errors import InputValidationError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class CollectionStorage:
    def __init__(self, directory=None):
        # Do not create directories merely to construct a read-only service.
        self.directory = Path(directory) if directory else None

    def _directory(self, write=False):
        if self.directory is None:
            from app.cli.paths import formal_runtime_paths

            paths = formal_runtime_paths()
            self.directory = (paths.storage_dir if paths else Path.cwd() / "data") / "collection"
        if write:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.directory.chmod(0o700)
        return self.directory

    def write(self, payload):
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
        if len(encoded) > 2 * 1024 * 1024:
            raise InputValidationError("collection_payload_too_large")
        ref = uuid4().hex + ".json"
        path = self._directory(write=True) / ref
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return ref, hashlib.sha256(encoded).hexdigest()

    def read(self, ref, expected_digest):
        try:
            if not re.fullmatch(r"[0-9a-f]{32}\.json", ref):
                raise ValueError
            path = self._directory() / ref
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as handle:
                raw = handle.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_digest:
                raise ValueError
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (OSError, ValueError, TypeError):
            raise InputValidationError("collection_payload_unavailable") from None
