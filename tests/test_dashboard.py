import threading
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from browser_fleet.dashboard import DashboardHandler


@pytest.fixture
def dashboard():
    handler = partial(DashboardHandler, manifest={"name": "test", "browsers": []}, ports={})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()
    server.server_close()
    thread.join()


def test_dashboard_rejects_dns_rebinding(dashboard):
    client = HTTPConnection("127.0.0.1", dashboard)
    client.request("GET", "/fleet.json", headers={"Host": "evil.example"})
    assert client.getresponse().status == 403
    client.close()


def test_dashboard_serves_only_public_ui_assets(dashboard):
    for path in ("/../deployment.json", "/id_ed25519", "/viewer-token"):
        client = HTTPConnection("127.0.0.1", dashboard)
        client.request("GET", path)
        assert client.getresponse().status == 404
        client.close()
    client = HTTPConnection("127.0.0.1", dashboard)
    client.request("GET", "/fleet.json")
    response = client.getresponse()
    assert response.status == 200
    assert response.getheader("Cache-Control") == "no-store"
    assert "frame-ancestors 'none'" in response.getheader("Content-Security-Policy")
    client.close()
