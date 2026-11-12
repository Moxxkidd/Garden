"""Synthetic SQLite service-query benchmark; never reads a user database."""

import json
import platform
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import insert, text

from app.db.bootstrap import init_database, reset_database_state, session_scope, upgrade_database
from app.models.project import Project, ProjectEntry, ProjectRun
from app.models.project_asset import ProjectAsset, ProjectObservation
from app.models.target import Target
from app.schemas.projects import ProjectQuery
from app.services.project_catalog import ProjectCatalogService
from app.services.project_workbench import ProjectWorkbenchService


def main():
    benchmark_dir = tempfile.TemporaryDirectory(prefix="garden-v130-bench-")
    url = "sqlite:///" + str(Path(benchmark_dir.name) / "fixture.db")
    upgrade_database(url)
    init_database(url)
    with session_scope() as s:
        t = Target(
            name="fixture",
            base_url="http://localhost:8111",
            type="web",
            owner="fixture",
            tags=[],
            status="active",
        )
        s.add(t)
        s.flush()
        p = Project(name="scale", description="")
        s.add(p)
        s.flush()
        e = ProjectEntry(
            project_id=p.id,
            target_id=t.id,
            name="fixture",
            entry_url=t.base_url,
            origin=t.base_url,
            target_base_url=t.base_url,
        )
        s.add(e)
        s.flush()
        r = ProjectRun(project_id=p.id, entry_id=e.id, source="scan", run_id=1, status="complete")
        s.add(r)
        s.flush()
        s.execute(
            insert(ProjectAsset),
            [
                dict(
                    id=f"{i:032x}",
                    project_id=p.id,
                    natural_key=str(i),
                    site=t.base_url,
                    method="GET",
                    kind="page" if i % 3 else "static",
                    original_type="page",
                    url=f"{t.base_url}/item/{i}?lang=en",
                )
                for i in range(10000)
            ],
        )
        s.execute(
            insert(ProjectObservation),
            [
                dict(
                    project_id=p.id,
                    project_run_id=r.id,
                    asset_id=f"{i:032x}",
                    display_asset_id=f"{i:032x}",
                    source_key=str(i),
                    identity_key="anonymous",
                    seen_at=datetime(2026, 11, 1),
                    first_seen=datetime(2026, 11, 1),
                    search_text=f"item {i}",
                    record={
                        "status_codes": [200],
                        "url": f"{t.base_url}/item/{i}",
                        "source_url": "/scans/1",
                    },
                )
                for i in range(10000)
            ],
        )
        pid = p.id
    out = {
        "assets": 10000,
        "python": sys.version,
        "platform": platform.platform(),
        "iterations": 15,
        "samples": {},
    }
    with session_scope() as s:
        c = ProjectCatalogService()
        for name, query in [
            ("page", ProjectQuery(page=50)),
            ("filter", ProjectQuery(q="item 99", kind="page")),
            ("core", ProjectQuery(collection="core")),
            ("templates", ProjectQuery()),
        ]:
            times = []
            for _ in range(15):
                start = time.perf_counter()
                result = (
                    ProjectWorkbenchService().templates(s, pid, query)
                    if name == "templates"
                    else c.list(s, pid, query)
                )
                times.append((time.perf_counter() - start) * 1000)
            out["samples"][name] = {
                "matched": result.matched,
                "median_ms": statistics.median(times),
                "p95_ms": sorted(times)[-1],
            }
        q = c._observations(pid, ProjectQuery(q="item 99", kind="page"))
        sql = str(q.compile(s.get_bind(), compile_kwargs={"literal_binds": True}))
        out["plan"] = [list(r) for r in s.execute(text("EXPLAIN QUERY PLAN " + sql))]
    reset_database_state()
    benchmark_dir.cleanup()
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
