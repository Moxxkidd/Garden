import os
import stat

import pytest

from app.core.errors import InputValidationError
from app.services.ephemeral_secret_store import EphemeralSecretStore
from app.services.secret_resolver import SecretResolver


def test_ephemeral_secret_is_private_atomic_and_not_in_reference(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    secret = "coverage-user-secret"

    ref = store.write(secret)
    path = store.path_for(ref)

    assert ref.startswith("ephemeral-file://")
    assert secret not in ref
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.read(ref) == secret
    assert not list(path.parent.glob("*.tmp"))


def test_delete_is_idempotent(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    ref = store.write("temporary")
    path = store.path_for(ref)

    store.delete(ref)
    store.delete(ref)

    assert not path.exists()


def test_purge_expired_deletes_old_secret_and_returns_reference(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    ref = store.write("expired")
    path = store.path_for(ref)
    os.utime(path, (0, 0))

    deleted = store.purge_expired(max_age_seconds=900)

    assert deleted == (ref,)
    assert not path.exists()


@pytest.mark.parametrize("ref", ["ephemeral-file:///etc/passwd", "env://SECRET"])
def test_store_rejects_outside_or_wrong_scheme(tmp_path, ref) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")

    with pytest.raises(InputValidationError):
        store.read(ref)


def test_store_rejects_symlink_even_when_target_is_inside_root(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    real_ref = store.write("hidden")
    real_path = store.path_for(real_ref)
    link = real_path.parent / "link.secret"
    link.symlink_to(real_path)

    with pytest.raises(InputValidationError):
        store.read(f"ephemeral-file://{link}")


def test_store_rejects_symlinked_parent_inside_root(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    real_ref = store.write("hidden")
    real_path = store.path_for(real_ref)
    alias = real_path.parent / "alias"
    alias.symlink_to(real_path.parent, target_is_directory=True)

    with pytest.raises(InputValidationError):
        store.read(f"ephemeral-file://{alias / real_path.name}")


def test_store_rejects_group_or_world_readable_secret(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    ref = store.write("hidden")
    store.path_for(ref).chmod(0o644)

    with pytest.raises(InputValidationError):
        store.read(ref)


def test_store_rejects_group_or_world_accessible_root(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    ref = store.write("hidden")
    store.root.chmod(0o755)

    with pytest.raises(InputValidationError):
        store.read(ref)


def test_secret_resolver_reads_ephemeral_reference(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    ref = store.write("hidden-value")

    assert SecretResolver(ephemeral_store=store).resolve(ref) == "hidden-value"
