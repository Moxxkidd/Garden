"""Fixed-budget discovery through the real scan and queue interfaces."""

import httpx
import pytest

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanRun
from app.schemas.collection_queue import CollectionOptions
from app.schemas.scan import ScanOptions
from tests.test_url_scan_pipeline import _service


@pytest.mark.parametrize("durable", [False, True])
def test_diversity_reaches_function_pages_before_detail_instances(tmp_path, durable):
    sent = []

    def handle(request):
        sent.append(request.url.path)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "".join(f'<a href="/detail/{i}">detail</a>' for i in range(1, 21))
                + '<a href="/settings">settings</a><a href="/search">search</a>'
            )
            if request.url.path == "/"
            else "ok",
        )

    service = _service(tmp_path, handle)
    try:
        result = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(
                discovery_strategy="diversity",
                max_pages=5,
                seed_input="http://localhost:8123/seed",
                durable=CollectionOptions(max_requests=5, min_interval_ms=0) if durable else None,
            ),
        )
        assert sent == ["/", "/detail/1", "/seed", "/settings", "/search"]
        with session_scope() as session:
            metadata = session.get(ScanRun, result.id).asset_metadata
            assert len(metadata["candidates"]) == 19
            assert all(c["reason"] != "pending" for c in metadata["candidates"])
            assert any(
                c.get("scheduling_reason") == "repeat_family" for c in metadata["candidates"]
            ), str([(c["reason"], c.get("scheduling_reason")) for c in metadata["candidates"]])
    finally:
        service.shutdown()


@pytest.mark.parametrize("durable", [False, True])
def test_sampling_preserves_exact_queries_and_unrequested_dates(tmp_path, durable):
    sent = []

    def handle(request):
        sent.append(request.url.raw_path.decode())
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                '<a href="/news/2026-01-01">a</a><a href="/news/2026-02-01">b</a>'
                '<a href="/news/2026-03-01">c</a><a href="/list?page=1">p1</a>'
                '<a href="/list?page=2">p2</a><a href="/list?page=3">p3</a>'
                '<a href="/list?sort=recent">sort</a>'
            )
            if request.url.path == "/"
            else "ok",
        )

    service = _service(tmp_path, handle)
    try:
        run = service.start_scan(
            "http://localhost:8123/",
            ScanOptions(
                discovery_strategy="diversity",
                max_variants=2,
                durable=CollectionOptions(max_variants=2, min_interval_ms=0) if durable else None,
            ),
        )
        assert set(sent) == {
            "/",
            "/news/2026-01-01",
            "/news/2026-02-01",
            "/list?page=1",
            "/list?page=2",
            "/list?sort=recent",
        }
        with session_scope() as session:
            metadata = session.get(ScanRun, run.id).asset_metadata
            assert {c["reason"] for c in metadata["candidates"]} == {"max_variants"}
            assert len(metadata["candidates"]) == 2
        if not durable:
            assert [(gap.reason, gap.count) for gap in run.coverage_gaps] == [("max_variants", 2)]
    finally:
        service.shutdown()


@pytest.mark.parametrize(
    "strategy,order", [(None, ["/", "/page", "/app.js"]), ("diversity", ["/", "/app.js", "/page"])]
)
def test_source_discovery_order_includes_resources_without_changing_legacy(
    tmp_path, strategy, order
):
    sent = []

    def handle(request):
        sent.append(request.url.path)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<script src="/app.js"></script><a href="/page">page</a>'
            if request.url.path == "/"
            else "ok",
        )

    service = _service(tmp_path, handle)
    try:
        service.start_scan("http://localhost:8123/", ScanOptions(discovery_strategy=strategy))
        assert sent == order
    finally:
        service.shutdown()
