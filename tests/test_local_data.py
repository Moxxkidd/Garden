import json
import stat
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core.errors import ConflictError
from app.core.settings import get_settings
from app.db.bootstrap import session_scope
from app.models.auth_session import AuthSession
from app.models.target import Target
from app.schemas.projects import ProjectQuery, ProjectReviewRequest
from app.services.local_data import LocalDataService
from app.services.project_catalog import ProjectCatalogService
from app.services.project_curation import ProjectCurationService
from tests.helpers.project_assets import project_fixture, source_run
from tests.test_project_comparison import ingest


def test_private_backup_restore_and_reference_safe_cleanup(seeded_records, tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    service = LocalDataService(database_url=get_settings().database_url, storage_directory=storage)
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        ingest(session, project, entry, source_run(session, target))
        aid = ProjectCatalogService().list(session, project.id, ProjectQuery()).items[0]["id"]
        curator = ProjectCurationService()
        curator.annotate(session, project.id, aid, ["待交接"], "合成备注", project.revision)
        curator.review(
            session,
            project.id,
            ProjectReviewRequest(
                asset_ids=[aid], state="keep", reason="人工保留", expected_revision=project.revision
            ),
        )
        private = session.get(AuthSession, seeded_records["auth_session"].id)
        # Move the synthetic material into the managed storage only for this fixture.
        ref = storage / "session_payloads" / "session-fixture.json"
        ref.parent.mkdir()
        ref.write_bytes(Path(private.storage_ref).read_bytes())
        private.storage_ref = str(ref)
    from app.schemas.delivery import DeliveryExportRequest
    from app.services.delivery_jobs import ExportJobService
    from tests.test_delivery_jobs import HeldExecutor

    exports = ExportJobService(base_directory=storage / "exports", dispatcher=HeldExecutor())
    with session_scope() as session:
        job = exports.submit(session, project.id, DeliveryExportRequest())
    with pytest.raises(ConflictError, match="active_writers"):
        service.backup(tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()
    with session_scope() as session:
        exports.cancel(session, job["id"])
    # This dispatcher records an admitted task but does not run it; cancel is public.
    with session_scope() as session:
        ready = exports.submit(session, project.id, DeliveryExportRequest(), inline=True)
        artifact = exports.artifact(session, ready["id"])[0]
        assert artifact.exists()
    exports.shutdown()
    backup = service.backup(tmp_path / "private-backup")
    assert backup["private"] is True
    for p in (tmp_path / "private-backup").rglob("*"):
        assert stat.S_IMODE(p.stat().st_mode) == (0o700 if p.is_dir() else 0o600)
    restored = service.restore(tmp_path / "private-backup", tmp_path / "restored-home")
    assert restored["active_directory_changed"] is False
    engine = create_engine(restored["database_url"])
    with engine.connect() as db:
        assert db.execute(text("select count(*) from project_observations")).scalar() == 1
        assert db.execute(text("select note from project_annotations")).scalar() == "合成备注"
        assert db.execute(text("select review_state from project_annotations")).scalar() == "keep"
        new_ref = Path(db.execute(text("select storage_ref from auth_sessions")).scalar())
        assert new_ref.is_relative_to(tmp_path / "restored-home")
        assert json.loads(new_ref.read_text())["cookies"]["demo_auth"] == "seeded-token"
    engine.dispose()
    with pytest.raises(ConflictError):
        service.restore(tmp_path / "private-backup", tmp_path / "restored-home")
    orphan = ref.parent / "session-orphan.json"
    orphan.write_text("synthetic orphan")
    preview = service.cleanup_preview(retention_days=0)
    assert str(ref) not in [x["path"] for x in preview["items"]]
    assert str(orphan) in [x["path"] for x in preview["items"]]
    # Reference adoption after preview must invalidate it without deleting anything.
    with session_scope() as session:
        session.get(AuthSession, seeded_records["auth_session"].id).storage_ref = str(orphan)
    with pytest.raises(ConflictError, match="cleanup_preview_changed"):
        service.cleanup_confirm(preview["token"])
    assert orphan.exists() and ref.exists()
    with session_scope() as session:
        session.get(AuthSession, seeded_records["auth_session"].id).storage_ref = str(ref)
    preview = service.cleanup_preview(retention_days=0)
    before_files = {p: p.stat().st_size for p in storage.rglob("*") if p.is_file()}
    result = service.cleanup_confirm(preview["token"])
    removed = {p for p in before_files if not p.exists()}
    assert artifact in removed
    assert result["count"] == len(removed)
    assert result["bytes"] == sum(before_files[p] for p in removed)
    assert result["bytes"] == preview["bytes"]
    assert result["count"] == preview["count"]
    assert result["count"] > 1
    assert ref.exists() and not orphan.exists()
