"""Responder device over WiFi: real TCP sockets, simulated Arduino on the far end."""

import socket
import threading
import time

import pytest

import hardware.bridge.bridge as bridge_module
from hardware.bridge.bridge import Bridge
from hardware.bridge.sim_device import SimulatedDevice
from hardware.bridge.transport import TcpDeviceServer, TransportError


class WifiDevice:
    """A SimulatedDevice speaking the line protocol over a TCP client socket,
    like the firmware does after joining the hotspot."""

    def __init__(self, port: int) -> None:
        self.device = SimulatedDevice("responder-device-01", link="wifi")
        self.port = port
        self.silent = False  # walked out of range: link stays open but nothing gets through
        self.sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def connect(self) -> None:
        self.close()
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        self.sock.settimeout(0.02)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        buffer = b""
        sock = self.sock
        while not self._stop.is_set():
            try:
                chunk = sock.recv(1024)
                if not chunk:
                    return
                buffer += chunk
            except socket.timeout:
                pass
            except OSError:
                return
            *lines, buffer = buffer.split(b"\n")
            if not self.silent:
                for line in lines:
                    self.device.receive_line(line.decode().strip(), time.monotonic())
            self.device.tick(time.monotonic())
            out = self.device.read_lines()
            if out and not self.silent:
                try:
                    sock.sendall("".join(f"{l}\n" for l in out).encode())
                except OSError:
                    return

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self.sock:
            self.sock.close()


def wait_for(condition, timeout=3.0, poll=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if poll:
            poll()
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def wifi():
    server = TcpDeviceServer("127.0.0.1", 0)
    with pytest.raises(TransportError):
        server.accept()  # nothing has joined yet; must not block
    device = WifiDevice(server.address[1])
    reports: list[str] = []
    bridge = Bridge(server.accept, on_report=lambda kind, a, at: reports.append(kind))
    yield server, device, bridge, reports
    device.close()
    server.close()


def connect(bridge: Bridge) -> bool:
    return wait_for(lambda: bridge.connected or bridge.connect(), timeout=3)


def test_device_joins_over_wifi_and_full_flow_works(wifi):
    _, device, bridge, reports = wifi
    device.connect()
    assert connect(bridge)
    assert bridge.link == "wifi" and bridge.device_id == "responder-device-01"

    bridge.assign("e1-sim-command-0001", "A", "high")
    assert wait_for(lambda: reports == ["delivered"], poll=bridge.poll)
    assert device.device.display == "A" and device.device.urgent

    device.device.short_press(time.monotonic())
    assert wait_for(lambda: reports == ["delivered", "acknowledged"], poll=bridge.poll)
    # Like the firmware, a hold only counts once the bridge has confirmed the press.
    assert wait_for(lambda: not device.device._pending_kind, poll=bridge.poll)
    device.device.long_press(time.monotonic())
    assert wait_for(lambda: device.device.state == "IDLE", poll=bridge.poll)
    assert reports == ["delivered", "acknowledged", "completed"]


def test_out_of_range_is_detected_and_press_survives_reconnect(wifi, monkeypatch):
    monkeypatch.setattr(bridge_module, "LINK_TIMEOUT_S", 0.6)
    monkeypatch.setattr(bridge_module, "PING_EVERY_S", 0.1)
    _, device, bridge, reports = wifi
    device.connect()
    assert connect(bridge)
    bridge.assign("e1-sim-command-0001", "A", "normal")
    assert wait_for(lambda: reports == ["delivered"], poll=bridge.poll)

    device.silent = True  # responder walks out of range...
    device.device.short_press(time.monotonic())  # ...and accepts the task there
    assert wait_for(lambda: not bridge.connected, poll=bridge.poll), "dead link not detected"
    assert reports == ["delivered"]

    device.silent = False  # back in range: firmware opens a fresh connection
    device.connect()
    assert connect(bridge)
    assert wait_for(lambda: reports == ["delivered", "acknowledged"], poll=bridge.poll)
    assert bridge.stats.reconnects >= 1
    assert device.device.display == "A"  # assignment kept, not reset


def test_newest_connection_replaces_a_half_open_one(wifi):
    server, device, bridge, _ = wifi
    stale = socket.create_connection(("127.0.0.1", server.address[1]))  # old link after roaming
    device.connect()
    try:
        assert connect(bridge)
        assert bridge.device_id == "responder-device-01"
    finally:
        stale.close()


def test_server_reports_when_hotspot_address_is_missing():
    server = TcpDeviceServer("192.0.2.123", 7777)  # TEST-NET address not on this machine
    with pytest.raises(TransportError):
        server.accept()
    assert "hotspot" in (server.bind_error or "")
