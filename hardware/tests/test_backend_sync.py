"""The bridge against Prince's real simulation backend (in-process FastAPI app)."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from hardware.bridge.backend import BackendSync, BackendUnavailable, device_label
from hardware.bridge.bridge import Bridge
from hardware.bridge.sim_device import SimulatedDevice
from hardware.bridge.transport import LoopbackTransport

BASE = "/api/v1/simulations/demo"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Rig:
    def __init__(self) -> None:
        self.client = TestClient(create_app())
        self.device = SimulatedDevice("arduino-test")
        self.transport = LoopbackTransport(self.device)
        self.bridge = Bridge(lambda: self.transport)
        self.backend_up = True
        self.sync = BackendSync(self.bridge, self.request, scenario_id="demo",
                                responder_id="responder-001")
        assert self.bridge.connect()

    def request(self, method, path, body, headers):
        if not self.backend_up:
            raise BackendUnavailable("simulated outage")
        response = self.client.request(method, path, json=body, headers=headers)
        return response.status_code, (response.json() if response.content else None)

    def step(self) -> None:
        """One bridge iteration: device lines, then backend state and outbox."""
        self.bridge.poll()
        self.sync.refresh()
        self.sync.flush()
        self.bridge.poll()

    def state(self) -> dict:
        return self.client.get(f"{BASE}/state").json()

    def reset(self) -> dict:
        response = self.client.post(f"{BASE}/reset", json={"mode": "simulation"})
        assert response.status_code == 201, response.text
        return response.json()["state"]

    def approve_and_dispatch(self, key: str = "1") -> list[dict]:
        snapshot = self.state()
        prop = next(p for p in snapshot["proposals"] if p["id"] == snapshot["current_proposal_id"])
        approval = self.client.post(
            f"{BASE}/proposals/{prop['id']}/decision",
            json={"proposal_revision": prop["revision"], "decision": "approve",
                  "actor": "demo-command", "mode": "simulation"},
            headers={"Idempotency-Key": f"approve-{key}"})
        assert approval.status_code == 201, approval.text
        dispatched = self.client.post(
            f"{BASE}/dispatches",
            json={"proposal_id": prop["id"], "proposal_revision": prop["revision"],
                  "approval_id": approval.json()["result"]["id"], "actor": "demo-command",
                  "mode": "simulation"},
            headers={"Idempotency-Key": f"dispatch-{key}"})
        assert dispatched.status_code == 201, dispatched.text
        return dispatched.json()["result"]["assignments"]

    def mine(self) -> dict:
        return next(a for a in self.state()["assignments"] if a["responder_id"] == "responder-001")


@pytest.fixture
def rig():
    r = Rig()
    r.reset()
    return r


def test_pending_unapproved_proposal_is_never_shown(rig):
    assert rig.state()["current_proposal_id"] is not None
    rig.step()
    assert rig.device.state == "IDLE"
    assert rig.bridge.active is None


def test_physical_loop_drives_backend_lifecycle(rig):
    rig.approve_and_dispatch()
    rig.step()
    record = rig.mine()
    assert rig.device.display == device_label(record["casualty_id"])
    assert record["status"] == "delivered"
    assert rig.sync.stats.posted == 1

    rig.device.short_press()
    rig.step()
    assert rig.mine()["status"] == "acknowledged"

    rig.device.long_press()
    rig.step()
    rig.step()
    final = rig.mine()
    assert final["status"] == "completed"
    assert final["delivered_at"] <= final["acknowledged_at"] <= final["completed_at"]
    assert final["casualty_id"] in rig.state()["resolved_casualty_ids"]
    assert rig.device.state == "IDLE"
    assert rig.sync.stats.rejected == []
    events = [e["type"] for e in rig.client.get(f"{BASE}/events?limit=1000").json()["events"]]
    for kind in ("delivered", "acknowledged", "completed"):
        assert f"simulation.device_{kind}" in events


def test_immediate_casualty_alerts_with_high_urgency(rig):
    snapshot = rig.state()
    prop = next(p for p in snapshot["proposals"] if p["id"] == snapshot["current_proposal_id"])
    casualty = next(a["casualty_id"] for a in prop["assignments"] if a["responder_id"] == "responder-001")
    response = rig.client.post(f"{BASE}/casualties/{casualty}/assessment", json={
        "severity": "immediate", "source": "human", "assessed_by": "demo-command",
        "assessed_at": now_iso()})
    assert response.status_code == 201, response.text
    rig.approve_and_dispatch()
    rig.step()
    assert rig.device.state == "ALERTING"
    assert rig.device.urgent == (rig.mine()["casualty_id"] == casualty)


def test_backend_outage_queues_events_in_order(rig):
    rig.approve_and_dispatch()
    rig.step()
    assert rig.mine()["status"] == "delivered"

    rig.backend_up = False
    rig.device.short_press()
    rig.bridge.poll()
    rig.device.long_press()
    rig.bridge.poll()
    rig.bridge.poll()
    assert [p.event["kind"] for p in rig.sync.outbox] == ["acknowledged", "completed"]

    rig.backend_up = True
    rig.sync.outbox[0].next_try = 0  # skip the retry delay
    rig.sync.flush()
    assert not rig.sync.outbox
    assert rig.mine()["status"] == "completed"


def test_reset_clears_device(rig):
    rig.approve_and_dispatch()
    rig.step()
    assert rig.device.state == "ALERTING"
    rig.reset()
    rig.step()
    assert rig.device.state == "IDLE"
    assert rig.bridge.active is None


def test_operator_completion_clears_device_and_late_press_is_not_posted(rig):
    rig.approve_and_dispatch()
    rig.step()
    record = rig.mine()
    response = rig.client.post(f"{BASE}/assignments/{record['id']}/simulate-device-event",
                               json={"kind": "completed"})
    assert response.status_code == 201, response.text
    rig.step()
    assert rig.device.state == "IDLE"
    rig.device.short_press()  # nothing shown, so nothing to acknowledge
    rig.step()
    assert rig.sync.stats.posted == 1 and rig.sync.stats.rejected == []


def test_retried_event_is_identical_and_deduplicated(rig):
    rig.approve_and_dispatch()
    rig.step()
    queued = []
    rig.sync.on_event = lambda event, result: queued.append((event, result))
    rig.device.short_press()
    rig.bridge.poll()
    event, result = queued[-1]
    assert result == "accepted"
    replay = rig.client.post(f"{BASE}/device-events", json=event,
                             headers={"Idempotency-Key": event["id"]})
    assert replay.status_code == 201 and replay.json()["duplicate"] is True


@pytest.mark.parametrize("casualty_id,label", [
    ("casualty-003", "C3"), ("casualty-actor-a", "A"), ("casualty-actor-b", "B"),
    ("x-pt9", "PT9"), ("x-weird", "?"),  # 5 characters do not fit the matrix
])
def test_device_label(casualty_id, label):
    assert device_label(casualty_id) == label
