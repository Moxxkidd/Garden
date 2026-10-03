"""Discovery submission transports and private preview seed retention."""

# ruff: noqa: F811
from types import SimpleNamespace

from test_scan_preview_pages import Inputs, browser  # noqa: F401
from typer.testing import CliRunner

from app.api.routes import scan_preview_pages
from app.cli.main import app as cli_app

SECRET = "http://127.0.0.1:8080/private?token=IMPORT_SECRET"


def test_preview_holds_seed_privately_and_confirms_exact_options(browser, monkeypatch):
    client, service, _ = browser
    received = []
    monkeypatch.setattr(
        service,
        "start_scan",
        lambda url, options: received.append(options) or SimpleNamespace(id=123),
    )
    response = client.post(
        "/scans/preview",
        data={
            "url": "http://127.0.0.1:8080/",
            "seed_input": SECRET,
            "collection_mode": "browser",
            "sitemap_enabled": "true",
            "js_enabled": "true",
            "max_browser_requests": "17",
            "render_wait_ms": "20",
        },
    )
    assert response.status_code == 200
    assert "IMPORT_SECRET" not in response.text
    values = Inputs(response.text).values
    assert values.get("seed_ref") and not values.get("seed_input")
    confirm = client.post("/scans/confirm", data=values, follow_redirects=False)
    assert confirm.status_code == 303
    assert received[0].seed_input == SECRET
    assert received[0].collection_mode == "browser"
    assert received[0].sitemap_enabled and received[0].js_enabled
    assert received[0].max_browser_requests == 17


def test_seed_edit_retains_private_reference_and_expiry_fails(browser, monkeypatch):
    client, _, _ = browser
    response = client.post(
        "/scans/preview", data={"url": "http://127.0.0.1:8080/", "seed_input": SECRET}
    )
    values = Inputs(response.text).values
    edit = client.post("/scans/edit", data=values)
    assert "IMPORT_SECRET" not in edit.text
    assert Inputs(edit.text).values["seed_ref"] == values["seed_ref"]
    later = client.post("/scans/preview", data=Inputs(edit.text).values)
    assert later.status_code == 200
    now = scan_preview_pages.time.time()
    monkeypatch.setattr(scan_preview_pages.time, "time", lambda: now + 901)
    expired = client.post("/scans/confirm", data=Inputs(later.text).values)
    assert expired.status_code == 409
    assert "IMPORT_SECRET" not in expired.text


def test_changed_seed_reference_rejected(browser):
    client, _, _ = browser
    first = client.post(
        "/scans/preview", data={"url": "http://127.0.0.1:8080/", "seed_input": SECRET}
    )
    other = client.post(
        "/scans/preview", data={"url": "http://127.0.0.1:8080/", "seed_input": SECRET + "2"}
    )
    values = Inputs(first.text).values
    values["seed_ref"] = Inputs(other.text).values.get("seed_ref", "missing")
    assert client.post("/scans/confirm", data=values).status_code == 409


def test_invalid_preview_does_not_reflect_seed(browser):
    client, _, _ = browser
    response = client.post(
        "/scans/preview",
        data={"url": "http://127.0.0.1:8080/", "seed_input": SECRET, "max_pages": "9000"},
    )
    assert response.status_code == 422
    assert "IMPORT_SECRET" not in response.text


def test_direct_form_submits_discovery_options(browser, monkeypatch):
    client, service, _ = browser
    received = []
    monkeypatch.setattr(
        service,
        "start_scan",
        lambda url, options: received.append(options) or SimpleNamespace(id=123),
    )
    response = client.post(
        "/scans",
        data={
            "url": "http://127.0.0.1:8080/",
            "seed_input": SECRET,
            "seed_format": "urls",
            "sitemap_enabled": "true",
            "max_candidates": "80",
            "collection_mode": "browser",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert received[0].seed_input == SECRET
    assert received[0].sitemap_enabled
    assert received[0].max_candidates == 80
    assert received[0].collection_mode == "browser"


def test_cli_discovery_flags_pass_actual_seed_payload(monkeypatch, tmp_path):
    import app.cli.scan as scan_cli

    received = []
    seed = tmp_path / "private-seeds.txt"
    seed.write_text(SECRET, encoding="utf-8")
    monkeypatch.setattr(
        scan_cli,
        "WebRuntimeManager",
        lambda **kwargs: SimpleNamespace(
            ensure=lambda **kwargs: SimpleNamespace(base_url="http://127.0.0.1:8000")
        ),
    )
    monkeypatch.setattr(
        scan_cli,
        "LocalScanApi",
        lambda base_url: SimpleNamespace(
            start_scan=lambda url, options: received.append(options) or SimpleNamespace(id=1)
        ),
    )
    result = CliRunner().invoke(
        cli_app,
        [
            "scan",
            "http://127.0.0.1:8080/",
            "--detach",
            "--collection-mode",
            "browser",
            "--sitemap",
            "--js",
            "--max-candidates",
            "10",
            "--max-sitemap-documents",
            "3",
            "--max-sitemap-depth",
            "1",
            "--max-browser-requests",
            "18",
            "--render-wait-ms",
            "50",
            "--seed-file",
            str(seed),
            "--seed-format",
            "urls",
        ],
    )
    assert result.exit_code == 0, result.output
    assert received[0].seed_input == SECRET
    assert received[0].sitemap_enabled and received[0].js_enabled
    assert received[0].collection_mode == "browser"
    assert received[0].max_browser_requests == 18
    assert "IMPORT_SECRET" not in result.output
    assert str(seed) not in result.output


def test_cli_seed_read_error_does_not_expose_local_path(tmp_path):
    path = str(tmp_path / "sensitive-missing.txt")
    result = CliRunner().invoke(cli_app, ["scan", "http://127.0.0.1/", "--seed-file", path])
    assert result.exit_code != 0
    assert path not in result.output
    assert "sensitive-missing" not in result.output
