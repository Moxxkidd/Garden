"""Real sockets prove reuse without adopting response cookies."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.settings import get_settings
from app.schemas.scan import ScanOptions
from app.services.scan_network import HttpScanGateway, TargetNetworkPolicy


@pytest.mark.parametrize("ending", ["complete", "error"])
def test_task_reuses_connection_without_cookies_and_closes(ending):
    connections, cookies, closed = [], [], threading.Event()

    class Site(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            connections.append(self.client_address)

        def finish(self):
            super().finish()
            closed.set()

        def log_message(self, *args):
            pass

        def do_GET(self):
            cookies.append(self.headers.get("Cookie"))
            self.send_response(200)
            self.send_header("Set-Cookie", "identity=must-not-follow")
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gateway = HttpScanGateway(TargetNetworkPolicy(get_settings()))
    try:
        try:
            with gateway.task_scope():
                for i in range(3):
                    gateway.fetch(f"http://127.0.0.1:{server.server_port}/{i}", ScanOptions())
                if ending == "error":
                    raise RuntimeError("cancelled")
        except RuntimeError:
            if ending != "error":
                raise
        assert len(connections) == 1
        assert cookies == [None, None, None]
        assert closed.wait(2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
