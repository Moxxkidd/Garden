import pytest

from app.core.errors import ConflictError, TargetPolicyError
from app.db.bootstrap import session_scope
from app.services.targets import DuplicateAction, TargetService


def test_target_import_skip_mode_is_idempotent(tmp_path) -> None:
    service = TargetService()
    import_file = tmp_path / "targets.json"
    import_file.write_text(
        """
[
  {
    "name": "import-demo",
    "base_url": "http://localhost:9400",
    "type": "web",
    "owner": "import-team",
    "tags": ["demo", "local"],
    "status": "active"
  }
]
""".strip(),
        encoding="utf-8",
    )

    with session_scope() as session:
        first = service.import_file(session, import_file, DuplicateAction.SKIP)
        second = service.import_file(session, import_file, DuplicateAction.SKIP)

    assert first.imported == 1
    assert second.skipped == 1


def test_target_import_update_mode_updates_existing_record(tmp_path) -> None:
    service = TargetService()
    import_file = tmp_path / "targets.yaml"
    import_file.write_text(
        """
targets:
  - name: update-demo
    base_url: http://localhost:9401
    type: web
    owner: initial-owner
    tags: [demo]
    status: active
""".strip(),
        encoding="utf-8",
    )

    updated_file = tmp_path / "targets-updated.yaml"
    updated_file.write_text(
        """
targets:
  - name: update-demo
    base_url: http://localhost:9401
    type: admin
    owner: updated-owner
    tags: [demo, refreshed]
    status: paused
""".strip(),
        encoding="utf-8",
    )

    with session_scope() as session:
        service.import_file(session, import_file, DuplicateAction.SKIP)
        result = service.import_file(session, updated_file, DuplicateAction.UPDATE)
        target = service.list(session)[0]

    assert result.updated == 1
    assert target.owner == "updated-owner"
    assert target.status == "paused"
    assert target.tags == ["demo", "refreshed"]


def test_target_import_fail_on_duplicate_raises_conflict(tmp_path) -> None:
    service = TargetService()
    import_file = tmp_path / "targets.yaml"
    import_file.write_text(
        """
targets:
  - name: duplicate-demo
    base_url: http://localhost:9402
    type: web
    owner: import-team
    tags: []
    status: active
""".strip(),
        encoding="utf-8",
    )

    with session_scope() as session:
        service.import_file(session, import_file, DuplicateAction.SKIP)
        with pytest.raises(ConflictError):
            service.import_file(session, import_file, DuplicateAction.FAIL)


def test_target_import_enforces_safe_target_policy(tmp_path) -> None:
    service = TargetService()
    import_file = tmp_path / "targets.yaml"
    import_file.write_text(
        """
targets:
  - name: remote-demo
    base_url: https://example.com
    type: web
    owner: import-team
    tags: []
    status: active
""".strip(),
        encoding="utf-8",
    )

    with session_scope() as session:
        with pytest.raises(TargetPolicyError):
            service.import_file(session, import_file, DuplicateAction.SKIP)
