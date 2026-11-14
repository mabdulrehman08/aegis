import pytest

from backend.app.simulation.store import SimulationStore
from test_simulation_support import (
    BASE, FRAME, FakeClock, approve_and_dispatch, assessment, current, decide, dispatch,
    make_client, observation, proposal, reset, state,
)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(clock):
    with make_client(SimulationStore(clock=clock)) as test_client:
        yield test_client


def simulate(client, assignment_id, kind, key=None, **extra):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(f"{BASE}/assignments/{assignment_id}/simulate-device-event",
                       json={"kind": kind, **extra}, headers=headers)


def test_reset_loads_demo_with_synthetic_map_and_pending_proposal(client):
    snapshot = reset(client)
    assert snapshot["mode"] == "simulation"
    grid = snapshot["scenario"]["scene_map"]["occupancy_grid"]
    assert (grid["width"], grid["height"], grid["resolution_m"]) == (24, 20, 1.0)
    assert snapshot["scenario"]["scene_map"]["revision"] == "sim-demo-map-1"
    assert snapshot["required_capabilities"] == {
        "casualty-001": [], "casualty-002": [], "casualty-003": []}
    prop = current(snapshot)
    assert prop["status"] == "pending_approval"
    assert prop["revision"] == snapshot["scene_revision"]
    assert prop["preserved_assignments"] == []
    assert len(prop["assignments"]) == 2
    assert all(a["route"]["map_revision"] == "sim-demo-map-1" for a in prop["assignments"])


def test_demo_is_lazily_available_and_unknown_scenarios_404(client):
    assert state(client)["scenario_id"] == "demo"
    assert client.get("/api/v1/simulations/other/state").status_code == 404
    assert client.post("/api/v1/simulations/other/reset").status_code == 404


def test_custom_synthetic_scenario_must_match_path(client):
    scenario = state(client)["scenario"]
    scenario["id"] = "drill"
    assert client.post("/api/v1/simulations/other/reset",
                       json={"scenario": scenario}).status_code == 422
    snapshot = reset(client, "/api/v1/simulations/drill", {"scenario": scenario})
    assert snapshot["scenario_id"] == "drill"
    scenario["synthetic"] = False
    assert client.post("/api/v1/simulations/drill/reset",
                       json={"scenario": scenario}).status_code == 422


def test_full_lifecycle_and_event_order(client, clock):
    snapshot = reset(client)
    created = approve_and_dispatch(client, snapshot)
    dispatched = created["result"]
    assert dispatched["dispatch"]["real_device_effect"] is False
    after = created["state"]
    busy = {r["id"]: r["status"] for r in after["scenario"]["responders"]}
    assert busy == {"responder-001": "busy", "responder-002": "busy"}
    # Active work is never re-proposed; nobody is free for casualty-003.
    assert after["current_proposal_id"] is None
    assert after["last_recommendation"]["proposal_id"] is None

    record = next(a for a in dispatched["assignments"] if a["responder_id"] == "responder-001")
    for kind in ("delivered", "acknowledged", "completed"):
        clock.advance(1)
        response = simulate(client, record["id"], kind)
        assert response.status_code == 201, response.text
    final = response.json()["state"]
    finished = next(a for a in final["assignments"] if a["id"] == record["id"])
    assert finished["status"] == "completed"
    assert finished["delivered_at"] < finished["acknowledged_at"] < finished["completed_at"]
    released = next(r for r in final["scenario"]["responders"] if r["id"] == "responder-001")
    assert released["status"] == "available"
    assert record["casualty_id"] in final["resolved_casualty_ids"]
    follow_up = current(final)
    assert [a["casualty_id"] for a in follow_up["assignments"]] == ["casualty-003"]
    assert {a["casualty_id"] for a in follow_up["preserved_assignments"]} == {"casualty-002"}

    page = client.get(f"{BASE}/events", params={"after_sequence": 0, "limit": 1000}).json()
    sequences = [event["sequence"] for event in page["events"]]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
    assert len({event["event_id"] for event in page["events"]}) == len(sequences)
    assert all(event["mode"] == "simulation" for event in page["events"])
    assert all(event["type"].startswith("simulation.") for event in page["events"])
    types = [event["type"] for event in page["events"]]
    for expected in ("simulation.dispatch_created", "simulation.device_delivered",
                     "simulation.device_acknowledged", "simulation.device_completed",
                     "simulation.responder_released"):
        assert expected in types
    assert not any(t.startswith(("dispatch.", "device.")) for t in types)


def test_decision_validation_and_exact_revision(client):
    prop = current(reset(client))
    url = f"{BASE}/proposals/{prop['id']}/decision"
    body = {"proposal_revision": prop["revision"], "decision": "approve", "actor": "operator-1"}
    assert client.post(url, json=body).status_code == 422  # Idempotency-Key required
    for bad in ({**body, "mode": "real"}, {**body, "approved_by": "x"},
                {k: v for k, v in body.items() if k != "actor"}):
        assert client.post(url, json=bad, headers={"Idempotency-Key": "a"}).status_code == 422
    assert decide(client, prop, "stale", revision=prop["revision"] + 1).status_code == 409
    first = decide(client, prop, "k1")
    assert first.status_code == 201
    record = first.json()["result"]
    assert record["operator_id"] == "operator-1" and record["authenticated"] is False
    replay = decide(client, prop, "k1")
    assert replay.json()["duplicate"] is True and replay.json()["result"]["id"] == record["id"]
    assert decide(client, prop, "k2").status_code == 409  # already decided
    assert decide(client, prop, "k1", decision="reject").status_code == 409  # key reuse


def test_dispatch_consumes_approval_exactly_once(client):
    prop = current(reset(client))
    approval_id = decide(client, prop, "approve").json()["result"]["id"]
    first = dispatch(client, prop, approval_id, "d1")
    assert first.status_code == 201
    replay = dispatch(client, prop, approval_id, "d1")
    assert replay.status_code == 201 and replay.json()["duplicate"] is True
    assert replay.json()["result"]["dispatch"]["id"] == first.json()["result"]["dispatch"]["id"]
    again = dispatch(client, prop, approval_id, "d2")
    assert again.status_code == 409
    snapshot = state(client)
    assert len(snapshot["dispatches"]) == 1 and len(snapshot["assignments"]) == 2
    assert next(a for a in snapshot["approvals"] if a["id"] == approval_id)["status"] == "consumed"
    missing = client.post(f"{BASE}/dispatches", json={
        "proposal_id": prop["id"], "proposal_revision": prop["revision"],
        "approval_id": approval_id, "actor": "operator-1"})
    assert missing.status_code == 422


def test_rejected_proposal_cannot_dispatch(client):
    prop = current(reset(client))
    rejection = decide(client, prop, "r", decision="reject").json()["result"]
    assert rejection["status"] == "recorded"
    assert dispatch(client, prop, rejection["id"], "d").status_code == 409
    assert state(client)["dispatches"] == []


def test_expired_proposal_and_approval_are_rejected(client, clock):
    prop = current(reset(client))
    clock.advance(121)
    assert decide(client, prop, "late").status_code == 409
    assert proposal(state(client), prop["id"])["status"] == "expired"

    prop = current(client.post(f"{BASE}/replan", json={"actor": "operator-1"}).json()["state"])
    approval_id = decide(client, prop, "on-time").json()["result"]["id"]
    clock.advance(121)
    assert dispatch(client, prop, approval_id, "too-late").status_code == 409
    snapshot = state(client)
    assert next(a for a in snapshot["approvals"] if a["id"] == approval_id)["status"] == "expired"
    assert snapshot["dispatches"] == []


def test_two_scene_changes_replan_and_require_fresh_approval(client):
    first = current(reset(client))
    stale_approval = decide(client, first, "a1").json()["result"]["id"]

    offline = client.patch(f"{BASE}/responders/responder-002", json={"status": "offline"})
    assert offline.status_code == 200
    snapshot = offline.json()["state"]
    assert proposal(snapshot, first["id"])["status"] == "superseded"
    assert next(a for a in snapshot["approvals"]
                if a["id"] == stale_approval)["status"] == "invalidated"
    second = current(snapshot)
    assert second["revision"] == snapshot["scene_revision"] > first["revision"]
    assert {a["responder_id"] for a in second["assignments"]} == {"responder-001"}

    scene_map = snapshot["scenario"]["scene_map"]
    scene_map["revision"] = "sim-demo-map-2"
    scene_map["occupancy_grid"]["cells"][5 * 24 + 5] = 100
    remapped = client.put(f"{BASE}/map", json=scene_map)
    assert remapped.status_code == 200
    snapshot = remapped.json()["state"]
    assert proposal(snapshot, second["id"])["status"] == "superseded"
    third = current(snapshot)
    assert all(a["route"]["map_revision"] == "sim-demo-map-2" for a in third["assignments"])

    assert dispatch(client, first, stale_approval, "old").status_code == 409
    assert decide(client, second, "a2").status_code == 409
    approve_and_dispatch(client, snapshot, "3")
    assert len(state(client)["dispatches"]) == 1


def test_active_task_survives_status_change_and_completion_keeps_offline(client, clock):
    created = approve_and_dispatch(client, reset(client))
    record = next(a for a in created["result"]["assignments"]
                  if a["responder_id"] == "responder-001")
    update = client.patch(f"{BASE}/responders/responder-001", json={"status": "offline"})
    assert update.json()["result"]["status_after_active_work"] == "offline"
    snapshot = update.json()["state"]
    assert next(a for a in snapshot["assignments"] if a["id"] == record["id"])["status"] == "dispatched"
    clock.advance(1)
    done = simulate(client, record["id"], "completed").json()["state"]
    responder = next(r for r in done["scenario"]["responders"] if r["id"] == "responder-001")
    assert responder["status"] == "offline"
    assert all(a["responder_id"] != "responder-001"
               for p in done["proposals"] if p["status"] == "pending_approval"
               for a in p["assignments"])


def test_device_event_dedup_correlation_and_out_of_order(client, clock):
    created = approve_and_dispatch(client, reset(client))
    record = created["result"]["assignments"][0]
    event = {
        "id": "dev-evt-1", "scenario_id": "demo", "device_id": record["device_id"],
        "dispatch_id": record["dispatch_id"], "command_id": record["command_id"],
        "casualty_id": record["casualty_id"], "responder_id": record["responder_id"],
        "kind": "acknowledged", "occurred_at": clock.iso(2), "mode": "simulation",
    }
    clock.advance(3)
    url = f"{BASE}/device-events"
    first = client.post(url, json=event)
    assert first.status_code == 201
    acked = next(a for a in first.json()["state"]["assignments"] if a["id"] == record["id"])
    assert acked["status"] == "acknowledged" and acked["delivered_at"] is None
    assert client.post(url, json=event).json()["duplicate"] is True
    assert client.post(url, json={**event, "occurred_at": clock.iso(1)}).status_code == 409
    assert client.post(url, json={**event, "id": "x", "mode": "real"}).status_code == 422
    assert client.post(url, json={**event, "id": "x", "device_id": "other"}).status_code == 409
    assert client.post(url, json={**event, "id": "x", "dispatch_id": "nope"}).status_code == 404
    assert client.post(url, json={**event, "id": "x", "severity": "minor"}).status_code == 422
    late = client.post(url, json={**event, "id": "dev-evt-2", "kind": "delivered",
                                  "occurred_at": clock.iso(-2)})
    assert late.status_code == 201
    final = next(a for a in late.json()["state"]["assignments"] if a["id"] == record["id"])
    assert final["status"] == "acknowledged" and final["delivered_at"] is not None


def test_observations_carry_no_severity_and_never_create_casualties(client, clock):
    reset(client)
    body = observation("obs-1", "cam1-trk-7", 10.0, 16.0, observed_at=clock.iso(),
                       first_seen_at=clock.iso(), last_seen_at=clock.iso())
    assert client.post(f"{BASE}/observations",
                       json={**body, "severity": "immediate"}).status_code == 422
    accepted = client.post(f"{BASE}/observations", json=body)
    assert accepted.status_code == 201
    snapshot = accepted.json()["state"]
    assert len(snapshot["scenario"]["casualties"]) == 3
    assert snapshot["tracks"][0]["casualty_id"] is None
    assert snapshot["tracks"][0]["track_id"] == "cam1-trk-7"
    assert client.post(f"{BASE}/observations", json=body).json()["duplicate"] is True
    assert client.post(f"{BASE}/observations",
                       json={**body, "confidence": 0.1}).status_code == 409

    revision = snapshot["scene_revision"]
    linked = client.post(f"{BASE}/associations", json={
        "track_id": "cam1-trk-7", "casualty_id": "casualty-004", "create_casualty": True,
        "actor": "operator-1"})
    assert linked.status_code == 201
    snapshot = linked.json()["state"]
    created = next(c for c in snapshot["scenario"]["casualties"] if c["id"] == "casualty-004")
    assert created["medical_assessment"] is None
    assert snapshot["scene_revision"] > revision
    assert snapshot["tracks"][0]["casualty_id"] == "casualty-004"
    assert client.post(f"{BASE}/associations", json={
        "track_id": "cam1-trk-7", "casualty_id": "casualty-001",
        "actor": "operator-1"}).status_code == 409
    clock.advance(1)
    older = observation("obs-0", "cam1-trk-7", 10.0, 16.0, observed_at=clock.iso(-3),
                        first_seen_at=clock.iso(-4), last_seen_at=clock.iso(-4))
    assert client.post(f"{BASE}/observations", json=older).status_code == 409
    foreign = {**body, "id": "obs-9", "position": {"frame_id": "other", "x_m": 1, "y_m": 1}}
    assert client.post(f"{BASE}/observations", json=foreign).status_code == 422


def test_human_assessment_validation_and_replan(client, clock):
    first = current(reset(client))
    url = f"{BASE}/casualties/casualty-003/assessment"
    assert client.post(url, json={**assessment("immediate", clock.iso()),
                                  "source": "cv"}).status_code == 422
    assert client.post(url, json=assessment("immediate", clock.iso(60))).status_code == 422
    minor = client.post(f"{BASE}/casualties/casualty-001/assessment",
                        json=assessment("minor", clock.iso()))
    assert minor.status_code == 201
    assert proposal(minor.json()["state"], first["id"])["status"] == "superseded"
    recorded = client.post(url, json=assessment("immediate", clock.iso()))
    assert recorded.status_code == 201
    snapshot = recorded.json()["state"]
    # Unassessed keeps protected parity; the human "minor" label lowers allocation priority.
    assert {a["casualty_id"] for a in current(snapshot)["assignments"]} == {
        "casualty-002", "casualty-003"}
    assert next(c for c in snapshot["scenario"]["casualties"]
                if c["id"] == "casualty-002")["medical_assessment"] is None
    assert client.post(url, json=assessment("minor", clock.iso(-10))).status_code == 409
    assert client.post(f"{BASE}/casualties/nope/assessment",
                       json=assessment("minor", clock.iso())).status_code == 404


def test_required_capabilities_are_explicit(client):
    reset(client)
    response = client.put(f"{BASE}/casualties/casualty-001/required-capabilities",
                          json={"capabilities": ["aed"], "actor": "operator-1"})
    snapshot = response.json()["state"]
    assert snapshot["required_capabilities"]["casualty-001"] == ["aed"]
    assert all(a["casualty_id"] != "casualty-001" for a in current(snapshot)["assignments"])
    assert "required_capabilities_missing" in snapshot["last_recommendation"]["reasons"]["casualty-001"]
    equipped = client.patch(f"{BASE}/responders/responder-001", json={"capabilities": ["aed"]})
    assert "casualty-001" in {a["casualty_id"] for a in current(equipped.json()["state"])["assignments"]}


def test_explicit_replan_and_explanation(client):
    first = current(reset(client))
    response = client.post(f"{BASE}/replan", json={"actor": "operator-1", "reason": "check",
                                                   "solver": "nearest"})
    assert response.status_code == 201
    second = response.json()["result"]
    assert second["engine"] == "nearest" and second["id"] != first["id"]
    assert proposal(response.json()["state"], first["id"])["status"] == "superseded"
    explained = client.get(f"{BASE}/proposals/{second['id']}/explanation")
    assert explained.status_code == 200
    assert explained.json()["mode"] == "simulation"
    assert explained.json()["explanation"]["facts"]
    assert client.get(f"{BASE}/proposals/nope/explanation").status_code == 404


def test_solver_result_for_stale_revision_is_discarded(clock):
    from optimization.solver import optimize

    holder = {}
    calls = []

    def racing_solver(request):
        calls.append(request.scene_revision)
        if len(calls) == 2:  # a scene change lands while this solve runs
            holder["store"].update_responder(
                "demo", "responder-002",
                __import__("backend.app.simulation.models", fromlist=["x"])
                .ResponderUpdateRequest(status="offline"))
        return optimize(request)

    store = SimulationStore(clock=clock, solver=racing_solver)
    holder["store"] = store
    with make_client(store) as client:
        reset(client)
        client.post(f"{BASE}/replan", json={"actor": "operator-1"})
        snapshot = state(client)
    prop = current(snapshot)
    assert prop["revision"] == snapshot["scene_revision"]
    assert {a["responder_id"] for a in prop["assignments"]} == {"responder-001"}


def test_solver_failure_records_error_without_proposal(clock):
    def broken(request):
        raise ValueError("boom")

    with make_client(SimulationStore(clock=clock, solver=broken)) as client:
        snapshot = reset(client)
    assert snapshot["current_proposal_id"] is None
    assert "boom" in snapshot["last_recommendation"]["error"]


def test_websocket_snapshot_order_and_reconnect(client):
    reset(client)
    with client.websocket_connect(f"{BASE}/ws") as socket:
        snapshot = socket.receive_json()
        assert snapshot["type"] == "simulation.snapshot" and snapshot["mode"] == "simulation"
        assert snapshot["sequence"] == snapshot["payload"]["sequence"]
        client.patch(f"{BASE}/responders/responder-002", json={"status": "offline"})
        received = [socket.receive_json() for _ in range(4)]
    sequences = [event["sequence"] for event in received]
    assert sequences == list(range(snapshot["sequence"] + 1, snapshot["sequence"] + 5))
    assert received[0]["type"] == "simulation.responder_updated"

    reset(client)
    with client.websocket_connect(f"{BASE}/ws") as socket:
        again = socket.receive_json()
    assert again["type"] == "simulation.snapshot"
    assert again["sequence"] > sequences[-1]
    assert again["payload"]["epoch"] == 2
    assert again["event_id"] != snapshot["event_id"]


def test_websocket_resyncs_with_snapshot_after_gap(clock):
    with make_client(SimulationStore(clock=clock, max_events=5)) as client:
        reset(client)
        with client.websocket_connect(f"{BASE}/ws") as socket:
            first = socket.receive_json()
            for index in range(4):
                client.patch(f"{BASE}/responders/responder-001",
                             json={"position": {"frame_id": FRAME, "x_m": index, "y_m": 1.0}})
            seen = socket.receive_json()
            while seen["type"] != "simulation.snapshot":
                assert seen["sequence"] > first["sequence"]
                seen = socket.receive_json()
        assert seen["sequence"] >= first["sequence"] + 4


def test_unknown_websocket_scenario_is_refused(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/simulations/nope/ws") as socket:
            socket.receive_json()
