"""Regression for the review finding "unconfirmed ACK cleared after retry exhaustion".

Invariants:
  1. An acknowledgement made while the device link is down is never dropped,
     however long the outage lasts (the old firmware gave up after 10 retries).
  2. Completion cannot bypass an unconfirmed acknowledgement: a hold while the
     ACK is unconfirmed is ignored, and the backend never records `completed`
     without `acknowledged`.
"""

from pathlib import Path
import re

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from hardware.bridge.backend import BackendSync
from hardware.bridge.bridge import Bridge
from hardware.bridge.sim_device import SimulatedDevice
from hardware.bridge.transport import LoopbackTransport

BASE = "/api/v1/simulations/demo"
FIRMWARE = Path(__file__).parents[1] / "firmware" / "aegis_responder" / "aegis_responder.ino"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class Rig:
    def __init__(self) -> None:
        self.client = TestClient(create_app())
        self.clock = Clock()
        self.device = SimulatedDevice("responder-device-01", link="wifi")
        self.transport = LoopbackTransport(self.device, clock=self.clock)
        self.bridge = Bridge(lambda: self.transport, clock=self.clock)
        self.sync = BackendSync(self.bridge, self.request, scenario_id="demo", responder_id="responder-001")
        assert self.bridge.connect()

    def request(self, method, path, body, headers):
        response = self.client.request(method, path, json=body, headers=headers)
        return response.status_code, (response.json() if response.content else None)

    def step(self, seconds: float = 0.0) -> None:
        self.clock.t += seconds
        self.bridge.poll()
        if self.bridge.connected:
            self.sync.refresh()
            self.sync.flush()
            self.bridge.poll()

    def dispatch(self) -> str:
        self.client.post(f"{BASE}/reset", json={"mode": "simulation"})
        snap = self.client.get(f"{BASE}/state").json()
        prop = next(p for p in snap["proposals"] if p["id"] == snap["current_proposal_id"])
        approval = self.client.post(f"{BASE}/proposals/{prop['id']}/decision", json={
            "proposal_revision": prop["revision"], "decision": "approve", "actor": "op", "mode": "simulation"},
            headers={"Idempotency-Key": "a"}).json()
        out = self.client.post(f"{BASE}/dispatches", json={
            "proposal_id": prop["id"], "proposal_revision": prop["revision"],
            "approval_id": approval["result"]["id"], "actor": "op", "mode": "simulation"},
            headers={"Idempotency-Key": "d"}).json()
        return next(a["id"] for a in out["result"]["assignments"] if a["responder_id"] == "responder-001")

    def record(self, assignment_id: str) -> dict:
        state = self.client.get(f"{BASE}/state").json()
        return next(a for a in state["assignments"] if a["id"] == assignment_id)

    def device_event_counts(self, assignment_id: str) -> dict[str, int]:
        events = self.client.get(f"{BASE}/events?limit=1000").json()["events"]
        counts = {"delivered": 0, "acknowledged": 0, "completed": 0}
        for e in events:
            if e["type"].startswith("simulation.device_") and e["payload"]["assignment"]["id"] == assignment_id:
                counts[e["type"].removeprefix("simulation.device_")] += 1
        return counts

    def go_out_of_range(self) -> None:
        self.transport.connected = False
        self.step()  # bridge notices the dead link

    def come_back(self) -> None:
        self.transport.connected = True
        assert self.bridge.connect()


@pytest.fixture
def rig():
    return Rig()


@pytest.mark.parametrize("outage_s", [5, 30, 120])  # the old firmware gave up after 10 s
def test_ack_during_outage_is_delivered_after_reconnect(rig, outage_s):
    assignment = rig.dispatch()
    rig.step()
    assert rig.record(assignment)["status"] == "delivered"

    rig.go_out_of_range()
    rig.device.short_press(rig.clock.t)  # responder accepts while out of range
    for _ in range(outage_s):  # device keeps retrying the unconfirmed ACK every second
        rig.clock.t += 1
        rig.device.tick(rig.clock.t)
    assert rig.device._pending_kind == "ACK", "unconfirmed ACK must stay pending"
    assert rig.record(assignment)["status"] == "delivered"

    rig.come_back()
    rig.step(1.1)
    rig.step()
    assert rig.record(assignment)["status"] == "acknowledged"
    assert rig.device._pending_kind == ""  # confirmed by the bridge
    assert rig.device_event_counts(assignment) == {"delivered": 1, "acknowledged": 1, "completed": 0}


def test_hold_cannot_bypass_unconfirmed_ack(rig):
    assignment = rig.dispatch()
    rig.step()
    rig.go_out_of_range()
    rig.device.short_press(rig.clock.t)
    rig.device.long_press(rig.clock.t)  # held while the ACK is still unconfirmed
    for _ in range(60):
        rig.clock.t += 1
        rig.device.tick(rig.clock.t)
    out = rig.device.read_lines()
    assert not any(line.startswith("DONE") for line in out), "DONE must not be sent before ACK is confirmed"

    rig.come_back()
    rig.step(1.1)
    rig.step()
    rec = rig.record(assignment)
    assert rec["status"] == "acknowledged" and rec["completed_at"] is None

    rig.device.long_press(rig.clock.t)  # now the hold counts
    rig.step()
    rig.step()
    rec = rig.record(assignment)
    assert rec["status"] == "completed"
    assert rec["delivered_at"] <= rec["acknowledged_at"] <= rec["completed_at"]
    assert rig.device_event_counts(assignment) == {"delivered": 1, "acknowledged": 1, "completed": 1}


def test_bridge_blocks_completed_without_acknowledged(rig):
    """Defense in depth: a misbehaving device sending DONE without ACK is not forwarded."""
    assignment = rig.dispatch()
    rig.step()
    token = rig.bridge.active.token
    rig.device._outbox.append(f"DONE {token}")  # protocol violation: no ACK first
    rig.step()
    rec = rig.record(assignment)
    assert rec["status"] == "delivered" and rec["completed_at"] is None
    assert any("blocked" in r for r in rig.sync.stats.rejected)


def test_ack_survives_bridge_restart_between_press_and_hold(rig):
    assignment = rig.dispatch()
    rig.step()
    rig.device.short_press(rig.clock.t)
    rig.step()
    assert rig.record(assignment)["status"] == "acknowledged"

    # A fresh bridge process: it learns the acknowledgement from the backend.
    rig.bridge = Bridge(lambda: rig.transport, clock=rig.clock)
    rig.sync = BackendSync(rig.bridge, rig.request, scenario_id="demo", responder_id="responder-001")
    assert rig.bridge.connect()
    rig.step()
    rig.device.long_press(rig.clock.t)
    rig.step()
    rig.step()
    assert rig.record(assignment)["status"] == "completed"
    assert rig.sync.stats.rejected == []


def test_firmware_keeps_unconfirmed_reports_and_gates_done():
    """Guards the C++ firmware itself against the original bug coming back."""
    source = FIRMWARE.read_text()
    retry = re.search(r"void retryPendingReport\(\) \{(.*?)\n\}", source, re.S).group(1)
    assert "pendingKind[0] = '\\0'" not in retry, "retry must never clear an unconfirmed report"
    assert "MAX_RETRIES" not in source and "no_host_ok" not in source
    long_press = re.search(r"void onLongPress\(\) \{(.*?)\n\}", source, re.S).group(1)
    assert "state == ACKED" in long_press and "pendingKind[0] == '\\0'" in long_press, \
        "DONE must require an ACKED state with no unconfirmed ACK"
    ok_handler = re.search(r'strcmp\(t\[0\], "OK"\) == 0\) \{(.*?)\n  \}', source, re.S).group(1)
    assert "strcmp(t[1], pendingKind) == 0" in ok_handler and "strcmp(t[2], commandId) == 0" in ok_handler, \
        "only an OK for the exact pending report and command may clear it"
