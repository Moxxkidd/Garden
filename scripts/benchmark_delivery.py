"""One 1k/10k synthetic export comparison; each measurement uses a fresh process."""

import argparse
import gc
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import time
import tracemalloc
import types
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, insert

from app.core.settings import clear_settings_cache
from app.db.bootstrap import (
    get_engine,
    init_database,
    reset_database_state,
    session_scope,
    upgrade_database,
)
from app.models.project import Project, ProjectEntry, ProjectRun
from app.models.project_asset import ProjectAsset, ProjectObservation
from app.models.target import Target
from app.schemas.assets import AssetRecord
from app.schemas.projects import ProjectQuery

BASELINE = "9c93cf5f25bef1ddc9085bc9cf8ff3e10c8992cf"


def fixture(directory, count):
    """History-only data: no live scanner, identity secrets, or network access."""
    directory.mkdir(parents=True, exist_ok=False)
    url = "sqlite+pysqlite:///" + str((directory / "fixture.db").resolve())
    os.environ["GARDEN_DATABASE_URL"] = url
    clear_settings_cache()
    upgrade_database(url)
    init_database(url)
    with session_scope() as session:
        target = Target(
            name="synthetic",
            base_url="https://fixture.invalid/",
            type="web",
            owner="fixture",
            tags=[],
            status="active",
        )
        session.add(target)
        session.flush()
        project = Project(name="Synthetic delivery", description="No live target requests")
        session.add(project)
        session.flush()
        entry = ProjectEntry(
            project_id=project.id,
            target_id=target.id,
            name="history",
            entry_url=target.base_url,
            origin="https://fixture.invalid",
            target_base_url=target.base_url,
        )
        session.add(entry)
        session.flush()
        run = ProjectRun(
            project_id=project.id,
            entry_id=entry.id,
            source="scan",
            run_id=1,
            status="complete",
            snapshot={
                "scope": {
                    "source": "scan",
                    "run_id": 1,
                    "target_id": target.id,
                    "entry_url": target.base_url,
                    "status": "completed",
                    "completeness": "incomplete",
                    "coverage_note": "Synthetic bounded history; no target was contacted.",
                    "contexts": [
                        {"kind": "anonymous", "status": "completed", "completeness": "incomplete"}
                    ],
                    "source_url": "/scans/1",
                },
                "comparison_metadata": {"budget": {"max_browser_requests": count}},
            },
        )
        session.add(run)
        session.flush()
        now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        for start in range(0, count, 200):
            assets, observations = [], []
            for i in range(start, min(start + 200, count)):
                aid = f"{i:032x}"
                address = (
                    target.base_url
                    + f"warehouse/invoices/{i}/"
                    + "long-readable-segment/" * 5
                    + "details?locale=[REDACTED]"
                )
                assets.append(
                    dict(
                        id=aid,
                        project_id=project.id,
                        natural_key=str(i),
                        site="https://fixture.invalid",
                        method="GET",
                        kind="page",
                        original_type="page",
                        url=address,
                    )
                )
                record = AssetRecord(
                    asset_id=f"scan:1:asset:{i}",
                    record_id=i,
                    record_type="scan_asset",
                    kind="page",
                    original_type="page",
                    url=address,
                    method="GET",
                    site="https://fixture.invalid",
                    status_codes=[200],
                    observation="response_observed",
                    context="anonymous",
                    context_completeness="incomplete",
                    first_seen=now,
                    last_seen=now,
                    provenance_note="Synthetic saved observation",
                    source_url="/scans/1",
                    evidence_ids=[i],
                ).model_dump(mode="json")
                observations.append(
                    dict(
                        project_id=project.id,
                        project_run_id=run.id,
                        asset_id=aid,
                        display_asset_id=aid,
                        source_key=record["asset_id"],
                        identity_key="target:1:anonymous",
                        seen_at=now,
                        first_seen=now,
                        search_text=f"item {i}",
                        record=record,
                    )
                )
            session.execute(insert(ProjectAsset), assets)
            session.execute(insert(ProjectObservation), observations)
        pid = project.id
    reset_database_state()
    return url, pid


def measure(kind, url, pid, directory):
    os.environ["GARDEN_DATABASE_URL"] = url
    clear_settings_cache()
    init_database(url)
    source = subprocess.check_output(["git", "show", f"{BASELINE}:app/services/asset_delivery.py"])
    legacy = types.ModuleType("garden_materialized_delivery_baseline")
    exec(compile(source, "baseline_asset_delivery.py", "exec"), legacy.__dict__)
    from app.schemas.delivery import DeliveryExportRequest
    from app.services.delivery_jobs import ExportJobService
    from app.services.scan_application import InlineScanDispatcher

    row_queries = {"count": 0, "max_limit": 0}

    def bounded_rows(connection, cursor, statement, parameters, context, many):
        if cursor.description and any(c[0] == "record" for c in cursor.description):
            assert " LIMIT " in statement, statement
            limit = parameters[-2]
            assert 0 < limit <= 200, limit
            row_queries["count"] += 1
            row_queries["max_limit"] = max(row_queries["max_limit"], limit)

    service = ExportJobService(
        base_directory=directory / "exports", dispatcher=InlineScanDispatcher()
    )
    if kind == "chunked":
        event.listen(get_engine(), "after_cursor_execute", bounded_rows)
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    if kind == "materialized":
        with session_scope() as session:
            body = legacy.AssetDeliveryService().export(session, pid, ProjectQuery(), "json")
        size = len(body.encode())
    else:
        with session_scope() as session:
            job = service.submit(session, pid, DeliveryExportRequest(format="json"), inline=True)
            assert job["status"] == "ready", job
            path, _ = service.artifact(session, job["id"])
        size = path.stat().st_size
    elapsed = time.perf_counter() - start
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
        1 if sys.platform == "darwin" else 1024
    )
    service.shutdown()
    reset_database_state()
    return dict(
        kind=kind,
        elapsed_seconds=round(elapsed, 3),
        python_peak_bytes=peak,
        process_peak_rss_bytes=rss,
        output_bytes=size,
        row_queries=row_queries if kind == "chunked" else None,
        baseline_source_sha256=hashlib.sha256(source).hexdigest(),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--worker", choices=["materialized", "chunked"])
    parser.add_argument("--database")
    parser.add_argument("--project-id", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(measure(args.worker, args.database, args.project_id, args.directory)))
        return
    report = dict(
        baseline_sha=BASELINE,
        candidate_head=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        dirty=bool(subprocess.check_output(["git", "status", "--porcelain"])),
        python=sys.version,
        platform=platform.platform(),
        comparison=(
            "Legacy materializer at baseline SHA and file jobs share this catalog/schema; "
            "fresh process each, one sample per 1000/10000 size. "
            "Python allocation peak and whole-process RSS are separate."
        ),
        samples=[],
    )
    for count in (1000, 10000):
        directory = args.directory.resolve() / str(count)
        url, pid = fixture(directory, count)
        sample = dict(records=count)
        for kind in ("materialized", "chunked"):
            result = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--worker",
                    kind,
                    "--database",
                    url,
                    "--project-id",
                    str(pid),
                    "--directory",
                    str(directory),
                ],
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "PYTHONPATH": str(Path.cwd())},
            )
            sample[kind] = json.loads(result.stdout)
        report["samples"].append(sample)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
