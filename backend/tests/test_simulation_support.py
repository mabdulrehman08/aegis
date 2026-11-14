"""Shared fixtures for SIMULATION API tests (deterministic clock)."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.simulation.store import SimulationStore

BASE = "/api/v1/simulations/demo"
START = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
FRAME = "demo-floor-1"


class FakeClock:
    def __init__(self, now: datetime = START):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now

    def iso(self, offset: float = 0) -> str:
        return (self.now + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


def make_client(store: SimulationStore) -> TestClient:
    application = create_app()
    application.state.simulation_store = store
    return TestClient(application)


def reset(client: TestClient, base: str = BASE, body: dict | None = None) -> dict:
    response = client.post(f"{base}/reset", json=body)
    assert response.status_code == 201, response.text
    return response.json()["state"]


def state(client: TestClient, base: str = BASE) -> dict:
    response = client.get(f"{base}/state")
    assert response.status_code == 200, response.text
    return response.json()


def current(snapshot: dict) -> dict:
    assert snapshot["current_proposal_id"] is not None, snapshot["last_recommendation"]
    return next(p for p in snapshot["proposals"] if p["id"] == snapshot["current_proposal_id"])


def proposal(snapshot: dict, proposal_id: str) -> dict:
    return next(p for p in snapshot["proposals"] if p["id"] == proposal_id)


def decide(client, prop: dict, key: str, decision: str = "approve", revision: int | None = None,
           actor: str = "operator-1"):
    return client.post(
        f"{BASE}/proposals/{prop['id']}/decision",
        json={"proposal_revision": revision or prop["revision"], "decision": decision,
              "actor": actor},
        headers={"Idempotency-Key": key},
    )


def dispatch(client, prop: dict, approval_id: str, key: str, actor: str = "operator-1"):
    return client.post(
        f"{BASE}/dispatches",
        json={"proposal_id": prop["id"], "proposal_revision": prop["revision"],
              "approval_id": approval_id, "actor": actor},
        headers={"Idempotency-Key": key},
    )


def approve_and_dispatch(client, snapshot: dict, suffix: str = "1") -> dict:
    prop = current(snapshot)
    approval = decide(client, prop, f"approve-{suffix}")
    assert approval.status_code == 201, approval.text
    response = dispatch(client, prop, approval.json()["result"]["id"], f"dispatch-{suffix}")
    assert response.status_code == 201, response.text
    return response.json()


def observation(obs_id: str, track_id: str, x: float, y: float, *, confidence: float = 0.9,
                observed_at: str, first_seen_at: str, last_seen_at: str,
                track_state: str = "visible") -> dict:
    return {
        "id": obs_id, "source": "cv", "observed_at": observed_at,
        "position": {"frame_id": FRAME, "x_m": x, "y_m": y},
        "kind": "person_detected", "confidence": confidence,
        "track": {"track_id": track_id, "state": track_state,
                  "first_seen_at": first_seen_at, "last_seen_at": last_seen_at},
    }


def assessment(severity: str, at: str, by: str = "medic-1") -> dict:
    return {"severity": severity, "source": "human", "assessed_by": by, "assessed_at": at}
