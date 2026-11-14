"""Behaviour on bad networks: captive portals, overloaded proxies, outages."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

import pytest

from hardware.bridge.backend import BackendSync, BackendUnavailable, http_requester
from hardware.bridge.bridge import Bridge
from hardware.bridge.sim_device import SimulatedDevice
from hardware.bridge.transport import LoopbackTransport


def serve(status: int, body: bytes, content_type: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize("status,body,ctype", [
    (200, b"<html>Sign in to puvisitor</html>", "text/html"),  # captive portal
    (503, b"<html>Service Unavailable</html>", "text/html"),   # overloaded proxy
    (502, b"", "text/plain"),
])
def test_non_json_or_gateway_errors_mean_unreachable(status, body, ctype):
    server = serve(status, body, ctype)
    try:
        request = http_requester(f"http://127.0.0.1:{server.server_port}", timeout_s=2)
        with pytest.raises(BackendUnavailable):
            request("GET", "/api/v1/simulations/demo/state", None, {})
    finally:
        server.shutdown()


def test_unreachable_host_raises_quickly():
    request = http_requester("http://127.0.0.1:9", timeout_s=0.5)  # nothing listens on port 9
    with pytest.raises(BackendUnavailable):
        request("GET", "/health", None, {})


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_polling_backs_off_during_outage_and_recovers():
    clock, calls = Clock(), []
    up = {"value": False}

    def requester(method, path, body, headers):
        calls.append(clock.t)
        if not up["value"]:
            raise BackendUnavailable("down")
        return 200, {"epoch": 1, "assignments": [], "scenario": {"casualties": []}}

    bridge = Bridge(lambda: LoopbackTransport(SimulatedDevice()), clock=clock)
    sync = BackendSync(bridge, requester, scenario_id="demo", responder_id="responder-001", clock=clock)

    for _ in range(400):  # 40 simulated seconds of the main loop
        sync.tick()
        clock.t += 0.1
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    assert len(calls) < 20, "must not hammer an unreachable backend"
    assert max(gaps) <= 5.2

    up["value"] = True
    before = len(calls)
    for _ in range(60):  # backend back: reconnects and returns to fast polling
        sync.tick()
        clock.t += 0.1
    assert sync.stats.last_state_ok
    assert len(calls) - before >= 5
