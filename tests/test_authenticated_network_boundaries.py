from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.core.errors import TargetPolicyError
from app.core.settings import Settings
from app.integrations.auth.http_adapter import HttpLoginAdapter
from app.integrations.auth.playwright_adapter import SyncPlaywrightGateway
from app.integrations.inventory.playwright_gateway import SyncPlaywrightInventoryGateway
from app.models.credential_profile import CredentialProfile
from app.models.enums import SessionType
from app.models.target import Target
from app.schemas.auth import HttpLoginConfig, PlaywrightLoginConfig
from app.schemas.inventory import InventoryBuildControls
from app.services.authenticated_network import AuthenticatedNetworkGuard
from app.services.scan_network import TargetNetworkPolicy


class _RedirectHandler(BaseHTTPRequestHandler):
    redirect_to = ""
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        type(self).hits.append(self.path)
        if self.path in {"/", "/login"} and self.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b'<form><input name="username"><input type="password" name="password">'
            b'<button type="submit">Login</button></form>'
        )

    def log_message(self, format: str, *args: object) -> None:
        return None


@contextmanager
def _server(handler_type: type[_RedirectHandler]):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _target(base_url: str) -> Target:
    return Target(id=1, name="boundary", base_url=base_url, type="web", owner="appsec")


def _credential() -> CredentialProfile:
    return CredentialProfile(
        id=1,
        target_id=1,
        name="user",
        role="user",
        auth_type="password",
        username="user@example.test",
        secret_ref="ephemeral-file:///redacted",
        login_config_path="inline://redacted",
    )


def test_http_login_blocks_cross_origin_redirect_before_secret_replay() -> None:
    attacker_hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "127.0.0.1":
            return httpx.Response(307, headers={"location": "http://attacker.test/steal"})
        attacker_hits.append(request.content.decode("utf-8", errors="replace"))
        return httpx.Response(200, json={"authenticated": True})

    adapter = HttpLoginAdapter(transport=httpx.MockTransport(handler))
    config = HttpLoginConfig.model_validate(
        {
            "adapter": "http",
            "login_request": {
                "method": "POST",
                "url": "/login",
                "body": {"password": "{{ password }}"},
                "expected_status": 200,
            },
            "validate_request": {"method": "GET", "url": "/me"},
        }
    )

    result = adapter.login(
        _target("http://127.0.0.1:9400"),
        _credential(),
        "do-not-forward",
        config,
    )

    assert result.success is False
    assert attacker_hits == []
    assert "do-not-forward" not in (result.last_error or "")


def test_authenticated_guard_rejects_link_local_destination() -> None:
    guard = AuthenticatedNetworkGuard(
        TargetNetworkPolicy(Settings(allow_private_targets=True, allow_non_local_targets=True))
    )

    with pytest.raises(TargetPolicyError):
        guard.ensure_allowed("http://169.254.169.254/", "http://169.254.169.254/latest")


def test_playwright_login_blocks_cross_origin_redirect_before_loading_form() -> None:
    class Attacker(_RedirectHandler):
        hits = []

    class Origin(_RedirectHandler):
        hits = []

    with _server(Attacker) as attacker_url:
        Origin.redirect_to = f"{attacker_url}/steal"
        with _server(Origin) as origin_url:
            gateway = SyncPlaywrightGateway()
            config = PlaywrightLoginConfig.model_validate(
                {
                    "adapter": "playwright",
                    "login_url": "/login",
                    "validate_url": "/",
                    "auto_detect_selectors": True,
                }
            )
            with pytest.raises(TargetPolicyError):
                gateway.login(config, _target(origin_url), "user", "do-not-forward")

    assert Attacker.hits == []


def test_authenticated_inventory_blocks_cross_origin_redirect_before_request() -> None:
    class Attacker(_RedirectHandler):
        hits = []

    class Origin(_RedirectHandler):
        hits = []

    with _server(Attacker) as attacker_url:
        Origin.redirect_to = f"{attacker_url}/outside"
        with _server(Origin) as origin_url:
            gateway = SyncPlaywrightInventoryGateway()
            with pytest.raises(TargetPolicyError):
                gateway.collect(
                    _target(origin_url),
                    f"{origin_url}/",
                    InventoryBuildControls(max_pages=1),
                    SessionType.PLAYWRIGHT_STORAGE_STATE,
                    {"storage_state": {"cookies": [], "origins": []}},
                )

    assert Attacker.hits == []
