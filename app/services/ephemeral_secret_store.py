"""Owner-only short-lived storage for interactive coverage secrets."""

from __future__ import annotations

import os
import secrets
import stat
import time
from pathlib import Path

from app.core.errors import InputValidationError


class EphemeralSecretStore:
    """Write secret values atomically and expose only opaque local references."""

    _scheme = "ephemeral-file://"

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def write(self, value: str) -> str:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        token = secrets.token_urlsafe(24)
        destination = self.root / f"{token}.secret"
        temporary = self.root / f"{token}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            destination.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)
        return f"{self._scheme}{destination}"

    def path_for(self, reference: str) -> Path:
        if not reference.startswith(self._scheme):
            raise InputValidationError("Temporary secret reference scheme is invalid.")
        root_mode = self.root.stat().st_mode if self.root.exists() else None
        if root_mode is not None and (
            not stat.S_ISDIR(root_mode) or stat.S_IMODE(root_mode) & 0o077
        ):
            raise InputValidationError("Temporary secret root permissions are too broad.")
        candidate = Path(reference.removeprefix(self._scheme)).expanduser()
        absolute = candidate.absolute()
        try:
            relative = absolute.relative_to(self.root)
        except ValueError as error:
            raise InputValidationError(
                "Temporary secret reference is outside the private root."
            ) from error
        cursor = self.root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise InputValidationError("Temporary secret reference cannot use symlinks.")
        resolved = absolute.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise InputValidationError("Temporary secret reference is outside the private root.")
        if resolved.exists():
            mode = resolved.stat().st_mode
            if not stat.S_ISREG(mode):
                raise InputValidationError("Temporary secret reference is not a regular file.")
            if stat.S_IMODE(mode) & 0o077:
                raise InputValidationError("Temporary secret file permissions are too broad.")
        return resolved

    def read(self, reference: str) -> str:
        return self.path_for(reference).read_text(encoding="utf-8")

    def delete(self, reference: str) -> None:
        self.path_for(reference).unlink(missing_ok=True)

    def purge_expired(self, max_age_seconds: int = 900) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        cutoff = time.time() - max_age_seconds
        deleted: list[str] = []
        for candidate in sorted(self.root.glob("*.secret")):
            if candidate.is_symlink():
                continue
            try:
                if candidate.stat().st_mtime > cutoff:
                    continue
                candidate.unlink()
            except FileNotFoundError:
                continue
            deleted.append(f"{self._scheme}{candidate}")
        return tuple(deleted)
