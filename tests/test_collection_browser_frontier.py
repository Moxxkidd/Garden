"""Public browser frontier adapter returns the final admitted work state."""

import time

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.schemas.scan import ScanOptions
from app.services.collection_browser import CollectionBrowser
from app.services.collection_queue import CollectionQueueService
from tests.helpers.project_assets import source_run
from tests.test_url_scan_pipeline import _service


def test_browser_frontier_does_not_report_depth_suppressed_work_as_accepted(
    seeded_records, tmp_path
):
    service = _service(tmp_path, lambda request: None)
    try:
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            run = source_run(session, target)
            run.status = "running"
            context = run.contexts[0]
            options = ScanOptions(max_depth=0, durable=CollectionOptions())
            queues = CollectionQueueService()
            qid = queues.create(session, run.id, context.id, options.durable)["id"]
            session.commit()
            browser = CollectionBrowser(
                service.pipeline, session, run, context, options, time.monotonic() + 60
            )
            assert not browser.enqueue(target.base_url.rstrip("/") + "/too-deep", depth=1)
            assert queues.get(session, qid)["counts"] == {"suppressed": 1}
            assert queues.list_work(session, qid)["items"][0]["reason"] == "max_depth"
            assert queues.get(session, qid)["metrics"]["requests_used"] == 0
    finally:
        service.shutdown()
