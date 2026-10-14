"""Active scan work shares the configured process-wide execution bound."""

from threading import Event

import httpx

from app.core.settings import get_settings
from app.schemas.scan import ScanOptions
from app.services.scan_application import ScanApplicationService
from tests.test_url_scan_pipeline import _service


def test_two_scan_services_share_one_configured_worker(tmp_path):
    first_started, second_started, release = Event(), Event(), Event()

    def respond(request):
        if request.url.path == "/first":
            first_started.set()
            assert release.wait(10), "test did not release first request"
        else:
            second_started.set()
        return httpx.Response(200, headers={"content-type": "text/html"}, text="asset")

    fixture = _service(tmp_path, respond)
    settings = get_settings().model_copy(update={"scan_max_concurrent_tasks": 1})
    first = ScanApplicationService(settings=settings, pipeline=fixture.pipeline)
    second = ScanApplicationService(settings=settings, pipeline=fixture.pipeline)
    try:
        a = first.start_scan("http://localhost:8111/first", ScanOptions())
        assert first_started.wait(5)
        b = second.start_scan("http://localhost:8111/second", ScanOptions())
        assert not second_started.wait(0.3), "second service exceeded global worker limit"
        release.set()
        first.shutdown()
        assert second_started.wait(5)
        second.shutdown()
        assert first.get_scan(a.id).status == "completed"
        assert second.get_scan(b.id).status == "completed"
    finally:
        release.set()
        first.shutdown()
        second.shutdown()
        fixture.shutdown()
