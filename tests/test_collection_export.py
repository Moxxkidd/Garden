import csv
import io
import json

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.services.collection_export import export_collection
from app.services.collection_queue import CollectionQueueService
from tests.helpers.project_assets import source_run


def test_collection_exports_preserve_metadata_for_empty_filters(seeded_records, tmp_path):
    queues = CollectionQueueService()
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        target.base_url = "http://localhost:8111/"
        run = source_run(session, target)
        queue_id = queues.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
    with session_scope() as session:
        queues.enqueue(session, queue_id, "http://localhost:8111/a?token=fixture-secret")
        for state in (None, "uncertain"):
            document = json.loads(export_collection(session, queue_id, "json", state=state))
            assert document["schema_version"] == "1"
            assert document["queue_scope"] == "known_work_only"
            assert document["filters"]["state"] == state
            assert document["total"] == (1 if state is None else 0)
            assert document["queue"]["metrics"]["requests_used"] == 0
            text = export_collection(session, queue_id, "csv", state=state)
            rows = list(csv.DictReader(io.StringIO(text)))
            assert rows[0]["row_type"] == "metadata"
            assert json.loads(rows[0]["metadata"])["total"] == document["total"]
            markdown = export_collection(session, queue_id, "markdown", state=state)
            assert "未覆盖不表示不存在" in markdown
            for output in (json.dumps(document), text, markdown):
                assert "fixture-secret" not in output
                assert "private_ref" not in output
                assert "lease_token" not in output
    with session_scope() as session:
        token = queues.lease(session, queue_id)
        item = queues.claim(session, queue_id, token)
        queues.stop_work(session, queue_id, item["id"], token, "retry_after", retry_at=float("inf"))
        exported = json.loads(export_collection(session, queue_id, "json"))
        assert exported["items"][0]["not_before"] is None
        assert exported["items"][0]["retry_time_unrepresentable"]
