"""Real SIGKILL at full scan transaction boundaries; loopback synthetic input only."""

import argparse
import json
import os
import select
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
parser = argparse.ArgumentParser()
parser.add_argument("--child", choices=["scan", "startup", "resume"])
parser.add_argument("--root", type=Path)
parser.add_argument("--phase")
parser.add_argument("--url")
parser.add_argument("--resume-without-restart", action="store_true")
args = parser.parse_args()


def raw_view(root):
    with sqlite3.connect("file:" + str(root / "scan.db") + "?mode=ro", uri=True) as db:
        return {
            "assets": db.execute("select count(*) from scan_assets where scan_run_id=1").fetchone()[
                0
            ],
            "evidence": db.execute(
                "select count(*) from scan_evidence where scan_run_id=1"
            ).fetchone()[0],
            "attempts": db.execute(
                "select state,count(*) from collection_attempts where queue_id=1 group by state"
            ).fetchall(),
            "work": db.execute(
                "select state,count(*) from collection_work where queue_id=1 group by state"
            ).fetchall(),
            "lease_until": db.execute(
                "select lease_until from collection_queues where id=1"
            ).fetchone()[0],
        }


def child():
    os.environ["GARDEN_HOME"] = str(args.root / "home")
    os.environ["GARDEN_DATABASE_URL"] = "sqlite:///" + str(args.root / "scan.db")
    os.environ["GARDEN_ALLOW_NON_LOCAL_TARGETS"] = "false"
    os.chdir(args.root)
    from app.db.bootstrap import get_engine, init_database, session_scope, upgrade_database
    from app.schemas.collection_queue import CollectionOptions
    from app.schemas.scan import ScanOptions
    from app.services.collection_queue import CollectionQueueService
    from app.services.scan_application import InlineScanDispatcher, ScanApplicationService

    upgrade_database(os.environ["GARDEN_DATABASE_URL"])
    init_database(os.environ["GARDEN_DATABASE_URL"])
    service = ScanApplicationService(dispatcher=InlineScanDispatcher())
    try:
        if args.child == "scan":
            from sqlalchemy import event
            from sqlalchemy.orm import Session

            def stop():
                print("CHECKPOINT", flush=True)
                time.sleep(3600)

            if args.phase == "uncommitted_evidence":

                def inserted(conn, cursor, statement, parameters, context, many):
                    if statement.lstrip().upper().startswith("INSERT INTO SCAN_EVIDENCE "):
                        stop()

                event.listen(get_engine(), "after_cursor_execute", inserted)
            elif args.phase == "committed_observation":

                def committed(session):
                    with sqlite3.connect(
                        "file:" + str(args.root / "scan.db") + "?mode=ro", uri=True
                    ) as db:
                        observed = db.execute(
                            "select count(*) from collection_work "
                            "where queue_id=1 and state='observed'"
                        ).fetchone()[0]
                    if observed == 1:
                        stop()

                event.listen(Session, "after_commit", committed)
            service.start_scan(
                args.url,
                ScanOptions(
                    durable=CollectionOptions(max_requests=1, min_interval_ms=0), max_pages=1
                ),
            )
            raise AssertionError("scan passed crash boundary without stopping")
        elif args.child == "startup":
            recovered = service.interrupt_active_scans()
            with session_scope() as session:
                queue = CollectionQueueService().get(session, 1)
            print(json.dumps({"recovered": recovered, "queue": queue}), flush=True)
        else:
            from app.schemas.collection_resume import CollectionResumeRequest
            from app.services.collection_resume import CollectionResumeService

            resumes = CollectionResumeService(scans=service)
            request = CollectionResumeRequest(
                queue_id=1, options=CollectionOptions(max_requests=1, min_interval_ms=0)
            )
            with session_scope() as session:
                preview = resumes.preview(session, request)
            with session_scope() as session:
                run_id = resumes.start(session, request, preview["token"])
            with session_scope() as session:
                print(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "queue": CollectionQueueService().for_run(session, run_id)[0],
                        }
                    ),
                    flush=True,
                )
    finally:
        service.shutdown()


def parent():
    root = Path(tempfile.mkdtemp(prefix="garden-v08-pipeline-crash-")).resolve()
    calls = []
    reached = threading.Event()
    release = threading.Event()

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            if self.path == "/awaiting_response" and calls.count(self.path) == 1:
                reached.set()
                release.wait(30)
            body = b"<p>synthetic fixture</p>"
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *unused):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    records = []

    def command(phase, mode):
        return [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            mode,
            "--root",
            str(root / phase),
            "--phase",
            phase,
            "--url",
            f"http://127.0.0.1:{server.server_port}/{phase}",
        ]

    def restart(phase, mode):
        result = subprocess.run(command(phase, mode), text=True, capture_output=True, timeout=40)
        (root / phase / f"{mode}-{time.time_ns()}.log").write_text(
            result.stderr + "\n" + result.stdout
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    try:
        for phase in ["awaiting_response", "uncommitted_evidence", "committed_observation"]:
            (root / phase).mkdir()
            error_log = (root / phase / "scan-stderr.log").open("w")
            process = subprocess.Popen(
                command(phase, "scan"), stdout=subprocess.PIPE, stderr=error_log, text=True
            )
            try:
                if phase == "awaiting_response":
                    assert reached.wait(20), "server never received attempt"
                else:
                    ready, _, _ = select.select([process.stdout], [], [], 20)
                    assert ready and process.stdout.readline().strip() == "CHECKPOINT", (
                        "worker missed boundary"
                    )
                process.kill()
                process.wait(timeout=5)
                assert process.returncode == -signal.SIGKILL
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
                error_log.close()
                release.set()
            snapshot = raw_view(root / phase)
            expected = 1 if phase == "committed_observation" else 0
            assert snapshot["assets"] == snapshot["evidence"] == expected, snapshot
            before = len(calls)
            immediate = restart(phase, "startup")
            assert len(calls) == before and immediate["recovered"] == 0
            records.append({"phase": phase, "snapshot": snapshot, "immediate": immediate})
            print(
                json.dumps({"phase": phase, "kill_exit": process.returncode, "snapshot": snapshot}),
                flush=True,
            )
        remaining = max(r["snapshot"]["lease_until"] for r in records) - time.time() + 0.2
        print(json.dumps({"waiting_for_real_lease_expiry_seconds": max(0, remaining)}), flush=True)
        if remaining > 0:
            time.sleep(remaining)
        for record in records:
            phase = record["phase"]
            before = len(calls)
            if args.resume_without_restart and phase != "committed_observation":
                resumed = restart(phase, "resume")
                assert len(calls) == before + 1
                assert resumed["queue"]["counts"] == {"observed": 1}
                assert raw_view(root / phase)["assets"] == raw_view(root / phase)["evidence"] == 0
                recovered = restart(phase, "startup")
                assert len(calls) == before + 1 and recovered["recovered"] == 0
                assert recovered["queue"]["counts"] == {"transferred": 1}
                record.update(resumed=resumed, recovered=recovered, no_second_startup=True)
                print(
                    json.dumps(
                        {
                            "phase": phase,
                            "no_second_startup": True,
                            "total_sends": calls.count("/" + phase),
                        }
                    ),
                    flush=True,
                )
                continue
            recovered = restart(phase, "startup")
            assert len(calls) == before and recovered["recovered"] == 1
            queue = recovered["queue"]
            expected = "observed" if phase == "committed_observation" else "uncertain"
            assert queue["counts"] == {expected: 1}, queue
            if phase != "committed_observation":
                resumed = restart(phase, "resume")
                assert len(calls) == before + 1 and resumed["queue"]["counts"] == {"observed": 1}, (
                    resumed
                )
                assert raw_view(root / phase)["assets"] == raw_view(root / phase)["evidence"] == 0
                record["resumed"] = resumed
            record["recovered"] = recovered
            print(
                json.dumps(
                    {
                        "phase": phase,
                        "recovered": recovered["recovered"],
                        "work": queue["counts"],
                        "total_sends": calls.count("/" + phase),
                    }
                ),
                flush=True,
            )
        output = {
            "root": str(root),
            "records": records,
            "actual_requests": calls,
            "resume_without_restart": args.resume_without_restart,
        }
        (REPO / ".verification").mkdir(exist_ok=True)
        (REPO / ".verification/full-pipeline-crash-result.json").write_text(
            json.dumps(output, indent=2)
        )
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    child() if args.child else parent()
