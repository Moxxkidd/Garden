import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI

from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.collection_queue import CollectionOptions
from app.services.collection_queue import CollectionQueueService
from tests.helpers.project_assets import source_run
from tests.test_scan_preview_e2e import _serve
from tests.test_url_scan_pipeline import _service


@pytest.mark.parametrize("phase", ["claimed", "sent", "acknowledged"])
def test_killed_process_preserves_durable_work_and_restart_never_sends(
    seeded_records, tmp_path, phase
):
    site = FastAPI()
    seen = []

    @site.get("/")
    def home():
        seen.append("request")
        return {"fixture": True}

    with _serve(site) as base:
        queues = CollectionQueueService()
        with session_scope() as session:
            target = session.get(Target, seeded_records["target"].id)
            target.base_url = base + "/"
            run = source_run(session, target)
            run.status = "running"
            qid = queues.create(session, run.id, run.contexts[0].id, CollectionOptions())["id"]
            queues.enqueue(session, qid, base + "/")
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.fixtures.collection_worker", str(qid), phase],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONPATH": "."},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready, _, _ = select.select([process.stdout], [], [], 15)
            assert ready, "worker did not reach checkpoint"
            line = process.stdout.readline().strip()
            assert line == "CHECKPOINT", process.stderr.read() if not line else line
            process.kill()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
        initial_sends = 0 if phase == "claimed" else 1
        assert len(seen) == initial_sends
        reopened = CollectionQueueService(clock=lambda: time.time() + 121)
        with session_scope() as session:
            assert reopened.recover_expired(session) == 1
            status = reopened.get(session, qid)
            assert status["counts"] == {"observed" if phase == "acknowledged" else "uncertain": 1}
            assert status["metrics"]["requests_used"] == initial_sends
            assert status["metrics"]["possibly_sent"] == (1 if phase == "sent" else 0)
        scans = _service(tmp_path, lambda request: None)
        try:
            assert scans.interrupt_active_scans() == 1
            assert scans.interrupt_active_scans() == 0
            assert len(seen) == initial_sends
        finally:
            scans.shutdown()
