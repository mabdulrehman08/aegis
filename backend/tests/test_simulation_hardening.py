import threading

import pytest

from backend.app.simulation.models import ReplanRequest, ResetRequest
from backend.app.simulation.store import SimulationError, SimulationStore
from test_simulation_support import (
    BASE, FRAME, FakeClock, approve_and_dispatch, assessment, current, decide, dispatch, make_client,
    observation, proposal, reset, state,
)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(clock):
    with make_client(SimulationStore(clock=clock)) as test_client:
        yield test_client


def track_casualty_001(client, clock, obs_id="obs-1", **overrides):
    stamp = clock.iso()
    body = observation(obs_id, "cam1-trk-1", 4.0, 8.0, observed_at=stamp,
                       first_seen_at=overrides.pop("first_seen_at", stamp),
                       last_seen_at=stamp, **overrides)
    assert client.post(f"{BASE}/observations", json=body).status_code == 201
    linked = client.post(f"{BASE}/associations", json={
        "track_id": "cam1-trk-1", "casualty_id": "casualty-001", "actor": "operator-1"})
    assert linked.status_code == 201
    return linked.json()["state"]


def test_proposal_expiry_is_bounded_by_observation_freshness(client, clock):
    reset(client)
    snapshot = track_casualty_001(client, clock)
    prop = current(snapshot)
    assert "casualty-001" in {a["casualty_id"] for a in prop["assignments"]}
    assert prop["expires_at"] == clock.iso(30)
    approval_id = decide(client, prop, "a").json()["result"]["id"]
    clock.advance(31)
    assert dispatch(client, prop, approval_id, "d").status_code == 409
    after = state(client)
    assert after["dispatches"] == []
    assert next(a for a in after["approvals"] if a["id"] == approval_id)["status"] != "active"
    fresh = current(after)
    assert fresh["revision"] == after["scene_revision"] > prop["revision"]
    assert "casualty-001" not in {a["casualty_id"] for a in fresh["assignments"]}
    assert "observation_stale" in after["last_recommendation"]["reasons"]["casualty-001"]


def test_snapshot_applies_time_transition_without_mutation(client, clock):
    reset(client)
    before = track_casualty_001(client, clock)
    clock.advance(31)
    after = state(client)
    assert after["scene_revision"] == before["scene_revision"] + 1
    assert proposal(after, before["current_proposal_id"])["status"] in {"expired", "superseded"}
    assert after["last_recommendation"]["trigger"] == "observation_freshness_changed"
    assert state(client)["scene_revision"] == after["scene_revision"]


def test_restoring_stale_track_with_identical_values_replans(client, clock):
    reset(client)
    first_seen = clock.iso()
    track_casualty_001(client, clock)
    clock.advance(31)
    stale = state(client)
    restored = observation("obs-2", "cam1-trk-1", 4.0, 8.0, observed_at=clock.iso(),
                           first_seen_at=first_seen, last_seen_at=clock.iso())
    response = client.post(f"{BASE}/observations", json=restored)
    snapshot = response.json()["state"]
    assert snapshot["scene_revision"] == stale["scene_revision"] + 1
    assert "casualty-001" in {a["casualty_id"] for a in current(snapshot)["assignments"]}


def test_every_authoritative_position_change_bumps_revision(client, clock):
    reset(client)
    first_seen = clock.iso()
    revision = track_casualty_001(client, clock)["scene_revision"]
    for step in range(1, 6):
        clock.advance(1)
        moved = observation(f"obs-m{step}", "cam1-trk-1", 4.0 + 0.01 * step, 8.0,
                            observed_at=clock.iso(), first_seen_at=first_seen,
                            last_seen_at=clock.iso())
        snapshot = client.post(f"{BASE}/observations", json=moved).json()["state"]
        assert snapshot["scene_revision"] == revision + step
    casualty = next(c for c in snapshot["scenario"]["casualties"] if c["id"] == "casualty-001")
    assert casualty["position"]["x_m"] == pytest.approx(4.05)
    clock.advance(1)
    heartbeat = observation("obs-hb", "cam1-trk-1", 4.05, 8.0, observed_at=clock.iso(),
                            first_seen_at=first_seen, last_seen_at=clock.iso())
    same = client.post(f"{BASE}/observations", json=heartbeat).json()["state"]
    assert same["scene_revision"] == snapshot["scene_revision"]
    nudged = client.patch(f"{BASE}/responders/responder-001",
                          json={"position": {"frame_id": FRAME, "x_m": 0.01, "y_m": 0.0}})
    assert nudged.json()["state"]["scene_revision"] == same["scene_revision"] + 1


def test_lost_or_occluded_report_must_retain_last_detection(client, clock):
    reset(client)
    first_seen = clock.iso()
    linked = track_casualty_001(client, clock)
    clock.advance(2)
    forged = observation("obs-lost-bad", "cam1-trk-1", 20.0, 18.0, confidence=0.1,
                         observed_at=clock.iso(), first_seen_at=first_seen,
                         last_seen_at=clock.iso(), track_state="lost")
    assert client.post(f"{BASE}/observations", json=forged).status_code == 409
    snapshot = state(client)
    casualty = next(c for c in snapshot["scenario"]["casualties"] if c["id"] == "casualty-001")
    assert (casualty["position"]["x_m"], casualty["position"]["y_m"]) == (4.0, 8.0)
    honest = observation("obs-lost", "cam1-trk-1", 4.0, 8.0, confidence=0.9,
                         observed_at=clock.iso(), first_seen_at=first_seen,
                         last_seen_at=first_seen, track_state="lost")
    accepted = client.post(f"{BASE}/observations", json=honest)
    assert accepted.status_code == 201
    after = accepted.json()["state"]
    assert after["tracks"][0]["latest_observation"]["track"]["state"] == "lost"
    assert after["scene_revision"] > linked["scene_revision"]
    assert "casualty-001" not in {a["casualty_id"] for a in current(after)["assignments"]}


def test_map_revision_cannot_be_reused_for_different_geometry(client):
    original = reset(client)["scenario"]["scene_map"]
    changed = {**original, "revision": "rev-b",
               "occupancy_grid": {**original["occupancy_grid"],
                                  "cells": list(original["occupancy_grid"]["cells"])}}
    changed["occupancy_grid"]["cells"][0] = 100
    assert client.put(f"{BASE}/map", json=changed).status_code == 200
    forged = {**changed, "revision": original["revision"]}
    assert client.put(f"{BASE}/map", json=forged).status_code == 409
    assert client.put(f"{BASE}/map", json={**changed, "obstacles": []}).status_code == 409
    restored = client.put(f"{BASE}/map", json=original)
    assert restored.status_code == 200
    assert restored.json()["state"]["scenario"]["scene_map"]["revision"] == original["revision"]
    reset(client)
    assert client.put(f"{BASE}/map", json={**changed, "revision": "rev-b2"}).status_code == 200


def test_simulated_device_helper_retry_reuses_stored_timestamp(client, clock):
    created = approve_and_dispatch(client, reset(client))
    record = created["result"]["assignments"][0]
    url = f"{BASE}/assignments/{record['id']}/simulate-device-event"
    clock.advance(1)
    first = client.post(url, json={"kind": "delivered"}, headers={"Idempotency-Key": "k"})
    assert first.status_code == 201
    stamp = first.json()["result"]["device_event"]["occurred_at"]
    clock.advance(10)
    keyed = client.post(url, json={"kind": "delivered"}, headers={"Idempotency-Key": "k"})
    assert keyed.status_code == 201 and keyed.json()["duplicate"] is True
    unkeyed = client.post(url, json={"kind": "delivered"})
    assert unkeyed.status_code == 201 and unkeyed.json()["duplicate"] is True
    assert unkeyed.json()["result"]["device_event"]["occurred_at"] == stamp
    events = client.get(f"{BASE}/events", params={"limit": 1000}).json()["events"]
    assert [e["type"] for e in events].count("simulation.device_delivered") == 1


def test_device_milestone_times_must_be_chronological(client, clock):
    created = approve_and_dispatch(client, reset(client))
    record = created["result"]["assignments"][0]
    url = f"{BASE}/assignments/{record['id']}/simulate-device-event"
    clock.advance(20)
    base = clock.iso(-20)
    from datetime import datetime, timedelta

    def at(seconds):
        return (datetime.fromisoformat(base.replace("Z", "+00:00"))
                + timedelta(seconds=seconds)).isoformat()

    assert client.post(url, json={"kind": "completed", "occurred_at": at(15)}).status_code == 201
    assert client.post(url, json={"kind": "acknowledged", "event_id": "late-ack",
                                  "occurred_at": at(16)}).status_code == 409
    assert client.post(url, json={"kind": "delivered", "occurred_at": at(10)}).status_code == 201
    assert client.post(url, json={"kind": "acknowledged", "event_id": "early-ack",
                                  "occurred_at": at(9)}).status_code == 409
    ok = client.post(url, json={"kind": "acknowledged", "occurred_at": at(12)})
    assert ok.status_code == 201
    final = next(a for a in ok.json()["state"]["assignments"] if a["id"] == record["id"])
    assert final["status"] == "completed"
    assert final["delivered_at"] < final["acknowledged_at"] < final["completed_at"]
    assert "late-ack" not in final["device_event_ids"]


def test_explicit_replan_idempotency_key_is_reserved_while_in_flight(clock):
    started, release = threading.Event(), threading.Event()
    from optimization.solver import optimize

    def slow(request):
        started.set()
        assert release.wait(5)
        return optimize(request)

    store = SimulationStore(clock=clock, solver=slow, auto_recommend=False)
    store.reset("demo", ResetRequest())
    body = ReplanRequest(actor="operator-1", reason="first")
    outcome = {}
    worker = threading.Thread(target=lambda: outcome.setdefault(
        "response", store.replan("demo", body, "key-1")))
    worker.start()
    assert started.wait(5)
    with pytest.raises(SimulationError) as in_flight:
        store.replan("demo", body, "key-1")
    assert in_flight.value.status_code == 409
    with pytest.raises(SimulationError) as different:
        store.replan("demo", ReplanRequest(actor="operator-1", reason="second"), "key-1")
    assert different.value.status_code == 409
    release.set()
    worker.join(5)
    response = outcome["response"]
    replay = store.replan("demo", body, "key-1")
    assert replay.duplicate is True and replay.result["id"] == response.result["id"]
    assert len([p for p in store.snapshot("demo").proposals]) == 1


def test_reset_keeps_event_sequence_and_revision_monotonic(client):
    first = reset(client)
    approve_and_dispatch(client, first)
    second = reset(client)
    assert second["epoch"] == first["epoch"] + 1
    assert second["scene_revision"] > first["scene_revision"]
    assert second["sequence"] > first["sequence"]
    assert second["dispatches"] == [] and second["assignments"] == []
    statuses = {r["status"] for r in second["scenario"]["responders"]}
    assert statuses == {"available"}
    old = first["proposals"][0]
    assert decide(client, old, "after-reset").status_code == 404


def test_future_assessment_becoming_effective_invalidates_approval(client, clock):
    reset(client)
    for casualty_id in ("casualty-002", "casualty-003"):
        response = client.post(f"{BASE}/casualties/{casualty_id}/assessment",
                               json=assessment("immediate", clock.iso(-10)))
        assert response.status_code == 201
    response = client.post(f"{BASE}/casualties/casualty-001/assessment",
                           json=assessment("minor", clock.iso(4)))
    assert response.status_code == 201
    prop = current(response.json()["state"])
    assert {a["casualty_id"] for a in prop["assignments"]} == {"casualty-001", "casualty-002"}
    assert prop["expires_at"] == clock.iso(4)
    approval_id = decide(client, prop, "a").json()["result"]["id"]
    clock.advance(5)
    assert dispatch(client, prop, approval_id, "d").status_code == 409
    after = state(client)
    assert after["dispatches"] == []
    assert next(a for a in after["approvals"] if a["id"] == approval_id)["status"] != "active"
    assert {a["casualty_id"] for a in current(after)["assignments"]} == {
        "casualty-002", "casualty-003"}


def test_future_assessment_in_supplied_reset_scenario_transitions(client, clock):
    scenario = state(client)["scenario"]
    labels = {"casualty-001": ("minor", 4), "casualty-002": ("immediate", -10),
              "casualty-003": ("immediate", -10)}
    for casualty in scenario["casualties"]:
        severity, offset = labels[casualty["id"]]
        casualty["medical_assessment"] = assessment(severity, clock.iso(offset))
    snapshot = reset(client, body={"scenario": scenario})
    prop = current(snapshot)
    assert {a["casualty_id"] for a in prop["assignments"]} == {"casualty-001", "casualty-002"}
    assert prop["expires_at"] == clock.iso(4)
    clock.advance(5)
    after = state(client)
    assert after["scene_revision"] == snapshot["scene_revision"] + 1
    assert {a["casualty_id"] for a in current(after)["assignments"]} == {
        "casualty-002", "casualty-003"}


def test_late_automatic_solve_never_replaces_newer_proposal(clock):
    from optimization.solver import optimize

    from backend.app.simulation.models import ResponderUpdateRequest

    gate = {"block": False}
    started, release = threading.Event(), threading.Event()

    def gated(request):
        if gate["block"]:
            gate["block"] = False
            started.set()
            assert release.wait(5)
        return optimize(request)

    store = SimulationStore(clock=clock, solver=gated)
    store.reset("demo", ResetRequest())
    gate["block"] = True
    worker = threading.Thread(target=store.update_responder, args=(
        "demo", "responder-002",
        ResponderUpdateRequest(position={"frame_id": FRAME, "x_m": 21.0, "y_m": 0.0})))
    worker.start()
    assert started.wait(5)
    explicit = store.replan("demo", ReplanRequest(actor="operator-1")).result
    release.set()
    worker.join(5)
    snapshot = store.snapshot("demo")
    assert snapshot.current_proposal_id == explicit["id"]
    assert next(p for p in snapshot.proposals if p.id == explicit["id"]).status == "pending_approval"
