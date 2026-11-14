import pytest

from hardware.bridge.bridge import Bridge
from hardware.bridge.protocol import ProtocolError, format_assign, parse_device_line
from hardware.bridge.sim_device import SimulatedDevice
from hardware.bridge.transport import LoopbackTransport


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def rig():
    clock, device = Clock(), SimulatedDevice("responder-device-test")
    transport = LoopbackTransport(device, clock=clock)
    reports: list[tuple[str, str]] = []
    bridge = Bridge(lambda: transport, clock=clock,
                    on_report=lambda kind, a, at: reports.append((kind, a.token)))
    assert bridge.connect()
    return clock, device, transport, bridge, reports


def test_full_flow_reports_each_milestone_once(rig):
    clock, device, _, bridge, reports = rig
    bridge.assign("cmd-1", "A", "high")
    bridge.poll()
    assert device.display == "A" and device.led == "blinking" and device.urgent

    device.short_press(clock.t)
    bridge.poll()
    assert device.led == "on"
    device.long_press(clock.t)
    bridge.poll()
    bridge.poll()
    assert device.state == "IDLE"
    assert reports == [("delivered", "cmd-1"), ("acknowledged", "cmd-1"), ("completed", "cmd-1")]


def test_lost_ack_is_retried_and_reported_once(rig):
    clock, device, _, bridge, reports = rig
    bridge.assign("cmd-1", "A", "normal")
    bridge.poll()
    device.drop_next_reports = 1  # first ACK line never reaches the laptop
    device.short_press(clock.t)
    bridge.poll()
    assert [k for k, _ in reports] == ["delivered"]
    clock.t += 1.1
    bridge.poll()
    clock.t += 1.1
    bridge.poll()
    assert [k for k, _ in reports] == ["delivered", "acknowledged"]


def test_repeated_report_counts_as_duplicate(rig):
    _, device, _, bridge, reports = rig
    bridge.assign("cmd-1", "A", "normal")
    bridge.poll()
    device.short_press()
    device._outbox.append("ACK cmd-1")
    bridge.poll()
    assert [k for k, _ in reports] == ["delivered", "acknowledged"]
    assert bridge.stats.duplicate_reports == 1


def test_reports_for_cancelled_or_unknown_commands_are_ignored(rig):
    _, device, _, bridge, reports = rig
    bridge.assign("cmd-1", "A", "normal")
    bridge.poll()
    bridge.cancel()
    bridge.poll()
    assert device.state == "IDLE"
    device._outbox.append("ACK cmd-1")
    device._outbox.append("ACK cmd-stale")
    bridge.poll()
    assert [k for k, _ in reports] == ["delivered"]


def test_reconnect_reshows_assignment_or_clears_stale_display(rig):
    _, device, transport, bridge, _ = rig
    bridge.assign("cmd-1", "B", "normal")
    transport.connected = False
    bridge.poll()
    assert not bridge.connected and bridge.stats.reconnects == 1

    transport.connected = True
    device._clear()  # device rebooted
    assert bridge.connect()
    assert device.display == "B"

    transport.connected = False
    bridge.poll()
    bridge.cancel()  # cleared while unplugged
    transport.connected = True
    assert bridge.connect()
    assert device.display == "B"  # not wiped until the caller confirms nothing should show
    bridge.clear_stale()
    assert device.state == "IDLE"  # RESET once confirmed


def test_assign_while_disconnected_is_shown_on_connect():
    device = SimulatedDevice()
    transport = LoopbackTransport(device)
    transport.connected = False
    bridge = Bridge(lambda: transport)
    assert not bridge.connect()
    bridge.assign("cmd-9", "C3", "normal")
    transport.connected = True
    assert bridge.connect()
    assert device.display == "C3"


def test_device_busy_until_finished(rig):
    _, _, _, bridge, _ = rig
    bridge.assign("cmd-1", "A", "normal")
    with pytest.raises(RuntimeError):
        bridge.assign("cmd-2", "B", "normal")


def test_protocol_rejects_unsafe_tokens():
    with pytest.raises(ProtocolError):
        format_assign("cmd 1", "C1", "high")
    with pytest.raises(ProtocolError):
        format_assign("cmd-1", "C1", "urgent")
    with pytest.raises(ProtocolError):
        format_assign("cmd-1", "TOOLONG", "high")
    assert parse_device_line("  ") is None
    assert parse_device_line("ACK cmd-1").command_id == "cmd-1"
