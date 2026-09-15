"""HTTP contracts for the anonymous submission preview."""

import socket
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.values = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and "name" in attrs:
            self.values[attrs["name"]] = attrs.get("value", "")


class HoldingDispatcher:
    def __init__(self):
        self.submissions = []

    def submit(self, scan_run_id, callback):
        self.submissions.append(scan_run_id)

    def shutdown(self):
        pass


@pytest.fixture
def browser(app, monkeypatch):
    with TestClient(app) as client:
        dispatcher = HoldingDispatcher()
        app.state.scan_service.dispatcher = dispatcher

        def no_network(*args, **kwargs):
            pytest.fail("preview or held submission contacted the network")

        monkeypatch.setattr(socket, "getaddrinfo", no_network)
        yield client, app.state.scan_service, dispatcher


def test_preview_displays_budget_without_creating_task(browser):
    client, service, dispatcher = browser
    response = client.post(
        "/scans/preview",
        data={
            "url": "http://127.0.0.1:8080/?token=private",
            "max_pages": "7",
            "max_depth": "1",
            "retry_attempts": "0",
        },
    )
    assert response.status_code == 200
    assert "开始匿名扫描" in response.text
    assert "登录身份：不使用（仅匿名）" in response.text
    assert "登录状态：本次尚未验证" not in response.text
    assert Inputs(response.text).values["max_pages"] == "7"
    assert service.list_scans() == []
    assert dispatcher.submissions == []


def test_confirm_creates_exact_preview_once(browser):
    from app.db.bootstrap import session_scope
    from app.models.scan_run import ScanRun

    client, service, dispatcher = browser
    response = client.post(
        "/scans/preview",
        data={
            "url": "http://127.0.0.1:8080/",
            "max_pages": "7",
            "max_depth": "1",
            "retry_attempts": "0",
            "request_timeout_seconds": "3.5",
        },
    )
    values = Inputs(response.text).values
    assert values.get("preview_token")
    assert "127.0.0.1" not in values["preview_token"]
    first = client.post("/scans/confirm", data=values, follow_redirects=False)
    assert first.status_code == 303
    second = client.post("/scans/confirm", data=values, follow_redirects=False)
    assert second.headers["location"] == first.headers["location"]
    assert len(service.list_scans()) == 1
    assert len(dispatcher.submissions) == 1
    with session_scope() as session:
        run = session.get(ScanRun, dispatcher.submissions[0])
        assert run.options["max_pages"] == 7
        assert run.options["max_depth"] == 1
        assert run.options["request_timeout_seconds"] == 3.5
        assert run.options["retry_attempts"] == 0


@pytest.mark.parametrize("change", ["url", "max_pages", "token", "missing", "expired", "settings"])
def test_confirmation_rejects_changed_or_stale_preview(browser, monkeypatch, change):
    from app.api.routes import scan_preview_pages

    client, service, dispatcher = browser
    preview = client.post("/scans/preview", data={"url": "http://127.0.0.1:8080/"})
    values = Inputs(preview.text).values
    if change == "url":
        values["url"] = "http://127.0.0.1:8081/"
    elif change == "max_pages":
        values["max_pages"] = "8"
    elif change == "token":
        values["preview_token"] = "bogus"
    elif change == "missing":
        values.pop("preview_token", None)
    elif change == "expired":
        now = scan_preview_pages.time.time()
        monkeypatch.setattr(scan_preview_pages.time, "time", lambda: now + 601)
    else:
        service.settings.scan_request_timeout_seconds = 8
    response = client.post("/scans/confirm", data=values, follow_redirects=False)
    assert response.status_code == 409
    assert "重新预览" in response.text
    assert service.list_scans() == []
    assert dispatcher.submissions == []


def test_edit_preserves_values_and_invalid_input_is_inline(browser):
    client, service, _ = browser
    values = {"url": 'http://127.0.0.1:8080/?q="<script>x</script>', "max_pages": "7"}
    preview = client.post("/scans/preview", data=values)
    edit = client.post("/scans/edit", data=Inputs(preview.text).values)
    assert edit.status_code == 200
    assert Inputs(edit.text).values["url"] == values["url"]
    assert Inputs(edit.text).values["max_pages"] == "7"
    assert "<script>x</script>" not in edit.text
    assert 'action="/scans/preview"' in edit.text
    invalid = client.post("/scans/preview", data={**values, "max_pages": "999"})
    assert invalid.status_code == 422
    assert 'aria-invalid="true"' in invalid.text
    assert 'id="error-max_pages"' in invalid.text
    assert Inputs(invalid.text).values["max_pages"] == "999"
    assert service.list_scans() == []


def test_homepage_uses_native_preview_form(browser):
    client, _, _ = browser
    response = client.get("/")
    assert 'action="/scans/preview"' in response.text
    assert "<details" in response.text
    assert 'name="request_timeout_seconds"' in response.text
    assert "预览扫描" in response.text


@pytest.mark.parametrize("url", ["", "ftp://127.0.0.1/", "http://127.0.0.1:bad/"])
def test_invalid_entry_cannot_preview_or_confirm(browser, url):
    client, service, dispatcher = browser
    for path in ("/scans/preview", "/scans/confirm"):
        response = client.post(path, data={"url": url, "preview_token": "forged"})
        assert response.status_code == 422
        assert 'aria-invalid="true"' in response.text
        assert 'id="error-url"' in response.text
        assert "开始匿名扫描" not in response.text
    assert not service.list_scans()
    assert not dispatcher.submissions
