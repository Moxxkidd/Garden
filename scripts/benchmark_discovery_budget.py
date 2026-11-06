"""Deterministic local corpus: dates/details, functional pages, Sitemap, JS and Hash routes."""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--strategy", choices=["legacy", "diversity"], default="legacy")
    parser.add_argument("--mode", choices=["http", "hybrid"], default="http")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo, output = args.source_root.resolve(), args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(repo))
    root = Path(tempfile.mkdtemp(prefix="garden-v120-budget-"))
    os.environ["GARDEN_HOME"] = str(root / "home")
    os.environ["GARDEN_DATABASE_URL"] = "sqlite:///" + str(root / "scan.db")
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

    sent = []
    dates = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(60)]
    paths = [
        *("/news/" + d for d in dates),
        *(f"/detail/{i}" for i in range(60)),
        *(f"/feature-{i}" for i in range(40)),
    ]
    html = '<link rel="icon" href="data:,">' + "".join(f'<a href="{p}">page</a>' for p in paths)
    html += '<a href="#/screen">route</a><script src="/app.js"></script>'
    js = (
        "fetch('/dynamic'); const routes = [{path:'/declared'}]; "
        "window.onhashchange = () => fetch('/route-view');"
    )

    class Site(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            sent.append(self.path)
            if self.path == "/":
                body, kind = html, "text/html"
            elif self.path == "/app.js":
                body, kind = js, "application/javascript"
            elif self.path == "/sitemap.xml":
                body = (
                    "<urlset>"
                    + "".join(
                        f"<url><loc>http://127.0.0.1:{self.server.server_port}/mapped-{i}</loc></url>"
                        for i in range(5)
                    )
                    + "</urlset>"
                )
                kind = "application/xml"
            else:
                body, kind = '<link rel="icon" href="data:,"><p>synthetic</p>', "text/html"
            raw = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", kind)
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
    try:
        tick = time.perf_counter()
        run = service.start_scan(
            f"http://127.0.0.1:{server.server_port}/",
            ScanOptions(
                collection_mode=args.mode,
                discovery_strategy="diversity" if args.strategy == "diversity" else None,
                sitemap_enabled=True,
                js_enabled=True,
                max_pages=50,
                max_resources=50,
                max_depth=2,
                render_wait_ms=100,
                overall_timeout_seconds=90,
                durable=CollectionOptions(max_requests=50, min_interval_ms=0, retry_attempts=0),
            ),
        )
        seconds = time.perf_counter() - tick
        with session_scope() as session:
            queue = CollectionQueueService().for_run(session, run.id)[0]
        families = [
            (
                re.sub(r"/(?:\d{4}-\d{2}-\d{2}|\d+)(?=/|$)", "/{number}", urlsplit(p).path),
                tuple(sorted(k for k, _ in parse_qsl(urlsplit(p).query))),
            )
            for p in sent
        ]
        unique = len(set(families))
        result = dict(
            strategy=args.strategy,
            mode=args.mode,
            budget=50,
            actual_requests=len(sent),
            observed_families=unique,
            repeated_family_requests=len(sent) - unique,
            observed_function_pages=sorted(
                p for p in set(sent) if p.startswith(("/feature-", "/mapped-", "/imported"))
            ),
            seconds=seconds,
            status=run.status,
            hybrid=(run.discovery_summary or {}).get("hybrid"),
            queue_counts=queue["counts"],
            metrics=queue["metrics"],
            requested_paths=sent,
            python=sys.version,
            platform=platform.platform(),
            base_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip(),
            source_sha256=hashlib.sha256(
                b"".join(p.read_bytes() for p in sorted((repo / "app").rglob("*.py")))
            ).hexdigest(),
            corpus_sha256=hashlib.sha256((html + js).encode()).hexdigest(),
        )
        assert result["actual_requests"] == queue["metrics"]["requests_used"] <= 50, result
        assert run.status != "failed", run.error_message
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "strategy",
                        "mode",
                        "actual_requests",
                        "observed_families",
                        "repeated_family_requests",
                        "seconds",
                    )
                }
            ),
            flush=True,
        )
    finally:
        service.shutdown()
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
