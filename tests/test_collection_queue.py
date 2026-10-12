import json
import stat

import pytest

from app.core.errors import ConflictError, InputValidationError
from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.services.collection_queue import CollectionQueueService
from app.services.collection_storage import CollectionStorage
from tests.helpers.project_assets import source_run


def test_queue_persists_work_and_keeps_original_requests_private(seeded_records, tmp_path):
    storage = CollectionStorage(tmp_path / "private")
    service = CollectionQueueService(storage=storage)
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        queue = service.create(session, run.id, run.contexts[0].id, CollectionOptions())
        url = target.base_url.rstrip("/") + "/?token=FIXTURE_SECRET"
        work = service.enqueue(session, queue["id"], url)
        assert service.enqueue(session, queue["id"], url)["id"] == work["id"]
        qid, wid = queue["id"], work["id"]
    with session_scope() as session:
        reopened = CollectionQueueService(storage=CollectionStorage(tmp_path / "private"))
        result = reopened.list_work(session, qid)
        assert result["total"] == 1
        assert "FIXTURE_SECRET" not in json.dumps(result)
        assert reopened.request(session, qid, wid) == {"url": url, "method": "GET"}
        assert reopened.get(session, qid)["counts"] == {"pending": 1}
    for path in (tmp_path / "private").iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_private_payload_rejects_tampering_and_external_paths(tmp_path):
    storage = CollectionStorage(tmp_path / "private")
    ref, digest = storage.write({"url": "http://localhost/?token=FIXTURE"})
    assert storage.read(ref, digest)["url"].endswith("FIXTURE")
    (tmp_path / "private" / ref).write_text("{}")
    with pytest.raises(InputValidationError, match="collection_payload_unavailable"):
        storage.read(ref, digest)
    with pytest.raises(InputValidationError, match="collection_payload_unavailable"):
        storage.read("../outside", digest)


def test_downgrade_cannot_discard_durable_work(seeded_records, tmp_path):
    from app.core.settings import get_settings
    from tests.test_migrations import downgrade_database

    service = CollectionQueueService(storage=CollectionStorage(tmp_path / "private"))
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        service.create(session, run.id, run.contexts[0].id, CollectionOptions())
    with pytest.raises(RuntimeError, match="collection history"):
        downgrade_database(get_settings().database_url, "0009")


def test_lease_pause_and_lost_worker_preserve_uncertain_work(seeded_records, tmp_path):
    now = [100.0]
    service = CollectionQueueService(
        storage=CollectionStorage(tmp_path / "private"), clock=lambda: now[0]
    )
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = service.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        first = service.enqueue(session, qid, target.base_url + "/one")
        second = service.enqueue(session, qid, target.base_url + "/two")
        lease = service.lease(session, qid)
        with pytest.raises(ConflictError, match="collection_busy"):
            service.lease(session, qid)
        assert service.claim(session, qid, lease)["id"] == first["id"]
        service.pause(session, qid)
        assert service.claim(session, qid, lease) is None
        service.complete(session, qid, first["id"], lease)
        assert service.get(session, qid)["counts"] == {"observed": 1, "pending": 1}
    now[0] = 300
    with session_scope() as session:
        service.recover_expired(session)
        with pytest.raises(ConflictError, match="collection_lease_expired"):
            service.complete(session, qid, second["id"], lease)
        assert service.get(session, qid)["status"] == "paused"
        assert service.request(session, qid, second["id"])["url"].endswith("/two")


def test_expired_claim_is_uncertain_not_observed(seeded_records, tmp_path):
    now = [1.0]
    service = CollectionQueueService(
        storage=CollectionStorage(tmp_path / "private"), clock=lambda: now[0]
    )
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = service.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        service.enqueue(session, qid, target.base_url)
        lease = service.lease(session, qid)
        service.claim(session, qid, lease)
    now[0] = 200
    with session_scope() as session:
        assert service.recover_expired(session) == 1
        assert service.get(session, qid)["counts"] == {"uncertain": 1}
        assert not service.get(session, qid)["coverage_complete"]


def test_stale_lease_conflicts_and_never_claims_twice(seeded_records, tmp_path):
    from app.db.bootstrap import get_engine, get_session
    from app.services.project_transactions import ensure_read_snapshot

    with get_engine().connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    service = CollectionQueueService(storage=CollectionStorage(tmp_path / "private"))
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = service.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        service.enqueue(session, qid, target.base_url)
    with get_session() as stale:
        ensure_read_snapshot(stale)
        service.get(stale, qid)
        with session_scope() as winner:
            token = service.lease(winner, qid)
        with pytest.raises(ConflictError, match="collection_concurrent_update"):
            service.lease(stale, qid)
        stale.rollback()
        with pytest.raises(ConflictError, match="collection_busy"):
            service.lease(stale, qid)
    with session_scope() as session:
        assert service.claim(session, qid, token) is not None
        assert service.claim(session, qid, token) is None


def test_variant_suppression_retains_candidates_and_does_not_claim_coverage(
    seeded_records, tmp_path
):
    service = CollectionQueueService(storage=CollectionStorage(tmp_path / "private"))
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = service.create(
            session, run.id, run.contexts[0].id, CollectionOptions(max_variants=1, max_work=2)
        )["id"]
        first = service.enqueue(session, qid, target.base_url + "/list?page=1", source="html_link")
        repeated = service.enqueue(session, qid, target.base_url + "/list?page=1", source="sitemap")
        assert repeated["sources"] == ["html_link", "sitemap"]
        second = service.enqueue(session, qid, target.base_url + "/list?page=2")
        assert second["reason"] == "max_variants"
        assert second["id"] != first["id"]
        assert service.enqueue(session, qid, target.base_url + "/other")["reason"] == "max_work"
        assert service.get(session, qid)["truncated"]
        assert not service.get(session, qid)["coverage_complete"]


def test_worker_settling_after_pause_preserves_paused_state(seeded_records, tmp_path):
    service = CollectionQueueService(storage=CollectionStorage(tmp_path / "private"))
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        run = source_run(session, target)
        qid = service.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
        first = service.enqueue(session, qid, target.base_url + "/one")
        service.enqueue(session, qid, target.base_url + "/two")
        lease = service.lease(session, qid)
        service.claim(session, qid, lease)
        service.pause(session, qid)
        service.complete(session, qid, first["id"], lease)
        settled = service.close(session, qid, lease)
        assert settled["status"] == "paused"
        assert settled["counts"] == {"observed": 1, "pending": 1}
        assert not settled["coverage_complete"]
