"""Explicit connection checks exercise real loopback HTTP and SOCKS handshakes."""

import json
import socketserver
import threading

import pytest
from typer.testing import CliRunner

from app.cli.main import app
from app.core.settings import clear_settings_cache


@pytest.mark.parametrize("mode", ["direct", "socks", "unavailable", "http_error"])
def test_explicit_connection_check_once_without_scan(tmp_path, monkeypatch, mode):
    requests = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            if mode == "socks":
                assert self.request.recv(3) == b"\x05\x01\x00"
                self.request.sendall(b"\x05\x00")
                data = self.request.recv(1024)
                assert data[:3] == b"\x05\x01\x00"
                self.request.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50")
            data = self.request.recv(4096)
            requests.append(data)
            code = b"403 Forbidden" if mode == "http_error" else b"200 OK"
            self.request.sendall(
                b"HTTP/1.1 " + code + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{tmp_path}/untouched/db.sqlite")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    monkeypatch.setenv("GARDEN_SCAN_NO_PROXY", "")
    monkeypatch.setenv(
        "GARDEN_SCAN_PROXY_URL",
        ""
        if mode in {"direct", "http_error"}
        else f"socks5://127.0.0.1:{server.server_address[1] if mode == 'socks' else 1}",
    )
    clear_settings_cache()
    try:
        result = CliRunner().invoke(
            app,
            ["connection-test", f"http://127.0.0.1:{server.server_address[1]}/fixture", "--json"],
        )
        assert result.exit_code == (0 if mode in {"direct", "socks"} else 1), result.output
        payload = json.loads(result.stdout)
        expected = {
            "direct": "ok",
            "socks": "ok",
            "unavailable": "proxy_unavailable",
            "http_error": "target_http_error",
        }
        assert payload["code"] == expected[mode]
        assert len(requests) == (0 if mode == "unavailable" else 1)
        assert not (tmp_path / "untouched").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_scan_reports_missing_socks_support_without_retry(monkeypatch):
    import httpx

    from app.core.settings import Settings
    from app.schemas.scan import ScanOptions
    from app.services.scan_network import FetchError, HttpScanGateway, TargetNetworkPolicy

    calls = []

    def missing_support(**kwargs):
        calls.append(kwargs)
        raise ImportError("socksio missing")

    monkeypatch.setattr(httpx, "Client", missing_support)
    gateway = HttpScanGateway(
        TargetNetworkPolicy(
            Settings(GARDEN_SCAN_PROXY_URL="socks5://127.0.0.1:1", GARDEN_SCAN_NO_PROXY="")
        )
    )
    with pytest.raises(FetchError) as caught:
        gateway.fetch("http://127.0.0.1/", ScanOptions())
    assert caught.value.code == "proxy_dependency_missing"
    assert len(calls) == 1
