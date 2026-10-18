"""Real loopback transport baseline, 20 bounded runs of 500 distinct URLs."""

import argparse
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["http", "browser"], required=True)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--per-round", type=int, default=500)
    parser.add_argument("--interval-ms", type=int, default=0)
    parser.add_argument(
        "--output", type=Path, default=Path(".verification/transport-scale-result.json")
    )
    args = parser.parse_args()
    if not 1 <= args.rounds <= 20 or not 1 <= args.per_round <= 500:
        parser.error("rounds must be 1..20 and per-round must be 1..500")
    if not 0 <= args.interval_ms <= 60000:
        parser.error("interval-ms must be 0..60000")
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    root = Path(
        tempfile.mkdtemp(
            prefix=f"garden-v08-{args.transport}-scale-",
        )
    ).resolve()
    os.environ["GARDEN_HOME"] = str(root / "home")
    os.environ["GARDEN_DATABASE_URL"] = "sqlite:///" + str(root / "scan.db")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True
    )
    source = {
        str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((repo / "app").rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }
    source_digest = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    os.chdir(root)

    from app.core.settings import get_settings
    from app.db.bootstrap import init_database, session_scope, upgrade_database
    from app.schemas.collection_queue import CollectionOptions
    from app.schemas.scan import ScanOptions
    from app.services.collection_queue import CollectionQueueService
    from app.services.scan_application import InlineScanDispatcher, ScanApplicationService
    from app.services.scan_network import HttpScanGateway, TargetNetworkPolicy
    from app.services.scan_pipeline import ScanPipeline
    from app.services.scan_reporting import ScanReportService

    seen = []
    received_at = []

    class Site(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *unused):
            pass

        def do_GET(self):
            seen.append(self.path)
            received_at.append(time.monotonic())
            if self.path.startswith("/round-"):
                batch = self.path.removeprefix("/round-")
                html = '<link rel="icon" href="data:,">' + "".join(
                    f'<a href="/asset-{batch}-{i}">{i}</a>' for i in range(args.per_round - 1)
                )
            else:
                html = '<link rel="icon" href="data:,"><p>synthetic asset</p>'
            raw = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    settings = get_settings()
    upgrade_database(settings.database_url)
    init_database(settings.database_url)
    policy = TargetNetworkPolicy(settings)
    service = ScanApplicationService(
        settings=settings,
        dispatcher=InlineScanDispatcher(),
        pipeline=ScanPipeline(
            policy=policy,
            gateway=HttpScanGateway(policy),
            report_service=ScanReportService(root / "reports"),
        ),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    start = time.perf_counter()
    cpu_start = time.process_time()
    rounds = []
    try:
        for batch in range(args.rounds):
            before = len(seen)
            tick = time.perf_counter()
            result = service.start_scan(
                f"http://127.0.0.1:{server.server_port}/round-{batch}",
                ScanOptions(
                    collection_mode=args.transport,
                    max_pages=args.per_round,
                    max_resources=2000,
                    max_browser_requests=2000,
                    max_candidates=10000,
                    max_depth=1,
                    render_wait_ms=0,
                    overall_timeout_seconds=1800,
                    durable=CollectionOptions(
                        max_requests=args.per_round,
                        max_work=args.per_round,
                        min_interval_ms=args.interval_ms,
                        retry_attempts=0,
                    ),
                ),
            )
            with session_scope() as session:
                queue = CollectionQueueService().for_run(session, result.id)[0]
            row = {
                "run_id": result.id,
                "seconds": time.perf_counter() - tick,
                "actual_requests": len(seen) - before,
                "status": result.status,
                "queue": queue,
            }
            rounds.append(row)
            print(
                json.dumps(
                    {
                        "round": batch + 1,
                        **{k: row[k] for k in ["run_id", "seconds", "actual_requests", "status"]},
                        "counts": queue["counts"],
                    }
                ),
                flush=True,
            )
            assert len(seen) - before == args.per_round, row
            assert queue["metrics"]["requests_used"] == args.per_round, row
            assert queue["counts"] == {"observed": args.per_round}, row
            assert queue["coverage_complete"], row
    finally:
        service.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        elapsed = time.perf_counter() - start
        timing_fields = ("queued_seconds", "active_seconds", "wait_seconds", "cooldown_seconds")
        timing_recorded = bool(rounds) and all(
            row["queue"]["metrics"].get(key) is not None for row in rounds for key in timing_fields
        )
        output = {
            "transport": args.transport,
            "root": str(root),
            "python": sys.version,
            "rounds": rounds,
            "total_seconds": elapsed,
            "unique_requested_urls": len(set(seen)),
            "actual_requests": len(seen),
            "requests_per_second": len(seen) / elapsed,
            "max_python_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
            "platform": platform.platform(),
            "source_sha256": source_digest,
            "base_commit": revision.stdout.strip() if revision.returncode == 0 else None,
            "database_bytes": (root / "scan.db").stat().st_size,
            "explicit_interval_ms": args.interval_ms,
            "wait_timing_measured": timing_recorded,
            "timing_source": "persisted collector wall-time samples",
            "timing_seconds": {
                key: sum(row["queue"]["metrics"][key] for row in rounds)
                if timing_recorded
                else None
                for key in timing_fields
            },
            "parent_python_cpu_seconds": time.process_time() - cpu_start,
            "server_min_receive_gap_seconds": min(
                (b - a for a, b in zip(received_at[:-1], received_at[1:], strict=True)),
                default=None,
            ),
        }
        output_path.write_text(json.dumps(output, indent=2))
        print(json.dumps({k: v for k, v in output.items() if k != "rounds"}), flush=True)


if __name__ == "__main__":
    main()
