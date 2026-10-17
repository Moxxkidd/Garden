"""Synthetic 10k queue persistence experiment; never accesses a target."""

import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.db.bootstrap import init_database, reset_database_state, session_scope, upgrade_database
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanRun
from app.schemas.collection_queue import CollectionOptions
from app.services.collection_queue import CollectionQueueService
from app.services.collection_storage import CollectionStorage


def verify(root):
    init_database("sqlite:///" + str(root / "queue.db"))
    queues = CollectionQueueService(storage=CollectionStorage(root / "private"))
    with session_scope() as session:
        ids = []
        for page in range(1, 51):
            data = queues.list_work(session, 1, page=page, page_size=200)
            assert data["total"] == 10000
            assert len(data["items"]) == 200
            ids.extend(item["id"] for item in data["items"])
        assert len(set(ids)) == 10000 and ids == sorted(ids)
        assert queues.list_work(session, 1, page=51, page_size=200)["items"] == []
        assert queues.list_work(session, 1, state="pending")["total"] == 10000
        for i in [0, 4999, 9999]:
            assert (
                queues.request(session, 1, ids[i])["url"]
                == f"http://localhost:8111/asset-{i}?token=FIXTURE"
            )
        view = queues.get(session, 1)
        assert view["counts"] == {"pending": 10000}
        assert view["metrics"]["requests_used"] == 0
        assert view["metrics"]["evidence_bytes"] == 0
        assert not view["coverage_complete"] and view["truncated"]
    reset_database_state()
    return {"work": len(ids), "pages": 50, "requests_sent": 0, "coverage_complete": False}


if len(sys.argv) > 1 and sys.argv[1] == "--verify":
    print(json.dumps(verify(Path(sys.argv[2]))))
else:
    root = Path(
        tempfile.mkdtemp(
            prefix="garden-v08-queue-scale-",
        )
    ).resolve()
    url = "sqlite:///" + str(root / "queue.db")
    upgrade_database(url)
    init_database(url)
    queues = CollectionQueueService(storage=CollectionStorage(root / "private"))
    with session_scope() as session:
        run = ScanRun(
            mode="quick",
            input_url="http://localhost:8111/",
            normalized_url="http://localhost:8111/",
            options={},
            status="queued",
        )
        session.add(run)
        session.flush()
        context = ScanContext(scan_run_id=run.id, kind="anonymous", context_key="anonymous")
        session.add(context)
        session.flush()
        qid = queues.create(session, run.id, context.id, CollectionOptions(max_work=10000))["id"]
    begin = time.perf_counter()
    with session_scope() as session:
        ids = []
        for i in range(10000):
            row = queues.enqueue(session, qid, f"http://localhost:8111/asset-{i}?token=FIXTURE")
            assert row["state"] == "pending" and "FIXTURE" not in row["url"]
            ids.append(row["id"])
            if (i + 1) % 1000 == 0:
                session.commit()
                print("inserted", i + 1, flush=True)
        insertion = time.perf_counter() - begin
        start = time.perf_counter()
        for i in range(10000):
            assert (
                queues.enqueue(session, qid, f"http://localhost:8111/asset-{i}?token=FIXTURE")["id"]
                == ids[i]
            )
        duplicate = time.perf_counter() - start
        assert (
            queues.enqueue(session, qid, "http://localhost:8111/overflow")["reason"] == "max_work"
        )
    reset_database_state()
    start = time.perf_counter()
    child = subprocess.run(
        [sys.executable, __file__, "--verify", str(root)],
        text=True,
        capture_output=True,
        check=True,
    )
    summary = {
        "environment": sys.version,
        "root": str(root),
        "insert_seconds": insertion,
        "dedup_seconds": duplicate,
        "restart_verify_seconds": time.perf_counter() - start,
        "max_python_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
        "database_bytes": (root / "queue.db").stat().st_size,
        "private_files": len(list((root / "private").iterdir())),
        "verification": json.loads(child.stdout),
    }
    Path(".verification").mkdir(exist_ok=True)
    Path(".verification/queue-scale-result.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
