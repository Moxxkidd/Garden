import json

import httpx

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanRun
from app.schemas.scan import ScanOptions
from tests.test_url_scan_pipeline import _service


def test_sources_flow_to_observations_and_private_seed_storage(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="""
                <a href="/next">next</a><form method="POST" action="/submit"></form>
                <script src="/app.js"></script>""",
            )
        if request.url.path == "/sitemap.xml":
            return httpx.Response(
                200,
                headers={"content-type": "application/xml"},
                text="<urlset><url><loc>http://127.0.0.1/map</loc></url></urlset>",
            )
        if request.url.path == "/app.js":
            return httpx.Response(
                200,
                headers={"content-type": "application/javascript"},
                text='fetch("/hidden?token=JS_SECRET")',
            )
        return httpx.Response(200, headers={"content-type": "text/html"}, text="ok")

    service = _service(tmp_path, handler)
    run = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(
            sitemap_enabled=True,
            js_enabled=True,
            seed_input="http://127.0.0.1/seed?token=SEED_SECRET",
        ),
    )
    assert set(calls) == {"/", "/next", "/app.js", "/sitemap.xml", "/map", "/seed"}
    with session_scope() as session:
        record = session.get(ScanRun, run.id)
        metadata = record.asset_metadata
        assert metadata["version"] == 2 and metadata["complete"]
        assert {"form_action", "js_literal"} <= {c["source_kind"] for c in metadata["candidates"]}
        assert len(metadata["observations"]) >= 5
        assert record.discovery_input_ref
        assert "SEED_SECRET" not in json.dumps(record.options)
        assert "SECRET" not in json.dumps(metadata)
        assert metadata["stats"]["sitemap"]["response_observed"] >= 1
    assert "SEED_SECRET" not in str(service.get_reuse_configuration(run.id))


def test_form_candidate_is_not_requested_and_sources_survive_low_page_budget(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="""
            <form action="/delete" method="get"></form><a href="/later">Later</a>""",
        )

    run = _service(tmp_path, handler).start_scan("http://127.0.0.1/", ScanOptions(max_pages=1))
    assert calls == ["/"]
    with session_scope() as session:
        metadata = session.get(ScanRun, run.id).asset_metadata
        assert len(metadata["candidates"]) == 2
        assert {c["reason"] for c in metadata["candidates"]} == {"declaration_only", "max_pages"}


def test_browser_routes_enter_catalog_without_inventing_route_http_responses(tmp_path):
    from app.schemas.assets import AssetQuery
    from app.services.asset_catalog import AssetCatalogService
    from tests.test_discovery_browser import site

    with site() as (base, requests):
        service = _service(tmp_path, lambda request: httpx.Response(500))
        run = service.start_scan(
            base + "/",
            ScanOptions(
                collection_mode="browser", render_wait_ms=100, max_pages=3, max_browser_requests=30
            ),
        )
    assert ("GET", "/delayed") in requests
    assert not any(method == "POST" for method, path in requests)
    with session_scope() as session:
        rows = AssetCatalogService().select(session, AssetQuery(source="scan", run_id=run.id)).items
        views = [r for r in rows if r.route_url]
        assert len({r.route_url for r in views}) >= 2
        assert all(r.status_codes == [] for r in views)
        groups = AssetCatalogService().select(
            session, AssetQuery(source="scan", run_id=run.id, view="grouped")
        )
        assert sum("#" in g.url for g in groups.items) >= 2


def test_candidate_cap_is_persisted_and_warned(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/one">one</a><a href="/two">two</a>',
        )

    run = _service(tmp_path, handler).start_scan("http://127.0.0.1/", ScanOptions(max_candidates=1))
    with session_scope() as session:
        stored = session.get(ScanRun, run.id)
        assert stored.asset_metadata["truncated"]
        assert not stored.asset_metadata["complete"]
    assert any(f.code == "discovery_incomplete" for f in run.failures)


def test_sitemap_cannot_follow_external_index_or_send_form(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/sitemap.xml":
            return httpx.Response(
                200,
                headers={"content-type": "application/xml"},
                text="""
            <sitemapindex><sitemap><loc>http://outside.test/secret.xml</loc></sitemap></sitemapindex>""",
            )
        return httpx.Response(200, headers={"content-type": "text/html"}, text="ok")

    run = _service(tmp_path, handler).start_scan(
        "http://127.0.0.1/", ScanOptions(sitemap_enabled=True)
    )
    assert calls == ["http://127.0.0.1/", "http://127.0.0.1/sitemap.xml"]
    assert any(f.code == "discovery_incomplete" for f in run.failures)


def test_discovery_request_attempts_include_redirects_and_retries(tmp_path):
    calls = []

    def handler(request):
        path = request.url.path
        calls.append(path)
        if path == "/" and calls.count("/") == 1:
            return httpx.Response(503)
        if path == "/":
            return httpx.Response(302, headers={"location": "/home"})
        if path == "/child" and calls.count("/child") == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/child">child</a>' if path == "/home" else "ok",
        )

    run = _service(tmp_path, handler).start_scan("http://127.0.0.1/", ScanOptions(retry_attempts=1))
    assert len(calls) == 5
    assert sum(s["request_attempts"] for s in run.discovery_summary["stats"].values()) == 5


def test_redirect_final_candidate_is_not_reported_as_unrequested(tmp_path):
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/old">old</a><a href="/final">final</a>'
            if request.url.path == "/"
            else "ok",
        )

    run = _service(tmp_path, handler).start_scan("http://127.0.0.1/", ScanOptions(max_pages=2))
    with session_scope() as session:
        record = session.get(ScanRun, run.id)
        assert record.asset_metadata["candidates"] == []
        final = next(a for a in record.assets if a.url.endswith("/final"))
        assert final.attributes["discovery"]["sources"][0]["kind"] == "html_link"


def test_entry_redirect_does_not_expand_starting_origin(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://outside.test/"})

    _service(tmp_path, handler).start_scan("http://127.0.0.1/")
    assert calls == ["http://127.0.0.1/"]


def test_private_discovery_inputs_affect_comparison_without_public_values(tmp_path):
    from app.services.scan_comparison import _options

    service = _service(tmp_path, lambda request: httpx.Response(200, text="ok"))
    ids = [
        service.start_scan("http://127.0.0.1/", ScanOptions(seed_input=seed)).id
        for seed in [
            "http://127.0.0.1/a?token=ONE",
            "http://127.0.0.1/a?token=TWO",
            "http://127.0.0.1/a?token=ONE",
        ]
    ]
    with session_scope() as session:
        options = [_options(session.get(ScanRun, run_id)) for run_id in ids]
        assert all(options)
        assert options[0] == options[2] and options[0] != options[1]
        assert "ONE" not in json.dumps(options) and "TWO" not in json.dumps(options)


def test_redirect_aliases_discovered_after_response_are_not_candidates(tmp_path):
    def handler(request):
        path = request.url.path
        if path in {"/old", "/middle"}:
            return httpx.Response(
                302, headers={"location": "/middle" if path == "/old" else "/final"}
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/old">Old</a>'
            if path == "/"
            else '<a href="/final">Self</a><a href="/middle">Alias</a>',
        )

    run = _service(tmp_path, handler).start_scan("http://127.0.0.1/", ScanOptions())
    with session_scope() as session:
        metadata = session.get(ScanRun, run.id).asset_metadata
        assert not metadata["candidates"]
        assert sum(s["response_observed"] for s in metadata["stats"].values()) == 2


def test_imported_query_is_redacted_in_persisted_evidence_and_report(tmp_path):
    from pathlib import Path

    from sqlalchemy import select

    from app.models.scan_run import ScanEvidence

    run = _service(tmp_path, lambda request: httpx.Response(200, text="ok")).start_scan(
        "http://127.0.0.1/", ScanOptions(seed_input="http://127.0.0.1/seed?token=IMPORTED_SECRET")
    )
    with session_scope() as session:
        evidence = session.scalars(
            select(ScanEvidence).where(ScanEvidence.scan_run_id == run.id)
        ).all()
        assert "IMPORTED_SECRET" not in str([(e.title, e.source_url) for e in evidence])
    assert "IMPORTED_SECRET" not in Path(run.report_path).read_text()
