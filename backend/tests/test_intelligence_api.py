import hashlib

import pytest

from backend.app.services.evaluation_reports import REPORT_FILES, REPORT_ROOT
from backend.app.simulation.store import SimulationStore
from test_simulation_support import (
    BASE, FRAME, FakeClock, approve_and_dispatch, decide, make_client, reset, state,
)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(clock):
    with make_client(SimulationStore(clock=clock)) as test_client:
        yield test_client


CALIBRATED = {"mode": "simulation", "actor": "operator-1",
              "mapping_mode": "calibrated_world",
              "calibration_ref": "lab-calibration-2026-11-13"}
ZONES = {"mode": "simulation", "actor": "operator-1", "mapping_mode": "predefined_zones",
         "zones": {"doorway": {"frame_id": FRAME, "x_m": 10.0, "y_m": 16.0}}}


def intelligence(client, base=BASE):
    response = client.get(f"{base}/intelligence")
    assert response.status_code == 200, response.text
    return response.json()


def set_policy(client, body, base=BASE, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.put(f"{base}/intelligence/policy", json=body, headers=headers)


def by_id(snapshot, casualty_id):
    return next(c for c in snapshot["casualties"] if c["casualty_id"] == casualty_id)


def register(client, camera_id, body):
    response = client.put(f"{BASE}/cameras/{camera_id}", json=body)
    assert response.status_code == 200, response.text


def camera_report(clock, obs_id="o1", track_id="t1", confidence=0.9, **location):
    stamp = clock.iso()
    return {"mode": "simulation", "id": obs_id, "observed_at": stamp,
            "confidence": confidence,
            "track": {"track_id": track_id, "state": "visible",
                      "first_seen_at": stamp, "last_seen_at": stamp},
            **location}


def observe_and_associate(client, clock, camera_id, registration, casualty_id,
                          obs_id="o1", track_id="t1", confidence=0.9, **location):
    register(client, camera_id, registration)
    posted = client.post(f"{BASE}/cameras/{camera_id}/observations",
                         json=camera_report(clock, obs_id, track_id, confidence,
                                            **location))
    assert posted.status_code == 201, posted.text
    linked = client.post(f"{BASE}/associations",
                         json={"track_id": posted.json()["result"]["track_id"],
                               "casualty_id": casualty_id, "actor": "operator-1"})
    assert linked.status_code == 201, linked.text


def compare(client, payload, base=BASE):
    return client.post(f"{base}/intelligence/comparisons", json=payload)


def comparison_payload(**overrides):
    payload = {"scenarios": [{"id": "s1", "label": "make responder-001 busy",
                              "changes": [{"kind": "responder_availability",
                                           "responder_id": "responder-001",
                                           "status": "busy"}]}]}
    payload.update(overrides)
    return payload


def events(client, base=BASE):
    return client.get(f"{base}/events",
                      params={"after_sequence": 0, "limit": 1000}).json()


def demo_positions(snapshot):
    return {c["id"]: c["position"] for c in snapshot["scenario"]["casualties"]}


def test_intelligence_defaults_are_legacy_and_typed(client):
    before = reset(client)
    snapshot = intelligence(client)
    assert snapshot["mode"] == "simulation"
    assert snapshot["schema_version"] == "1.0"
    assert snapshot["policy"] == {"mode": "simulation", "temporal_gate": False,
                                  "decay_gates_eligibility": False,
                                  "allow_zone_estimates": True,
                                  "confidence_half_life_seconds": 30.0,
                                  "stability_preference": False,
                                  "objective_policy": "distance_first",
                                  "stability_travel_budget_m": None,
                                  "version": 0, "updated_by": None,
                                  "updated_at": None}
    assert snapshot["thresholds"] == {"freshness_seconds": 30.0, "min_confidence": 0.5,
                                      "position_tolerance_m": 0.25}
    current = next(p for p in before["proposals"]
                   if p["id"] == before["current_proposal_id"])
    assert [(item["casualty_id"], item["responder_id"])
            for item in snapshot["previous_recommendations"]] == [
               (item["casualty_id"], item["responder_id"])
               for item in current["assignments"]]
    assert snapshot["previous_recommendation_proposal_id"] == current["id"]
    assert "advisory" in snapshot["interpretation"].lower()
    assert all(entry["temporal_gate_applied"] is False
               and entry["exclusion_reasons"] == []
               for entry in snapshot["casualties"])
    reasoning = snapshot["proposal"]
    assert reasoning["objective_policy"] == "distance_first"
    assert reasoning["stability_budget_enforced"] is False
    assert isinstance(reasoning["objective_tiers"], list)
    assert isinstance(reasoning["objective_values"], dict)
    assert isinstance(reasoning["previous_assignment_outcomes"], list)
    assert isinstance(reasoning["applied_assignment_exclusions"], dict)


def test_intelligence_without_proposal_maps_typed_views(clock):
    store = SimulationStore(clock=clock, auto_recommend=False)
    with make_client(store) as quiet:
        reset(quiet)
        snapshot = intelligence(quiet)
        assert snapshot["proposal"] is None
        assert snapshot["previous_recommendations"] == []
        assert len(snapshot["casualties"]) == 3


def test_policy_change_bumps_revision_invalidates_approval_and_emits(client):
    before = reset(client)
    revision = before["scene_revision"]
    prop = next(p for p in before["proposals"]
                if p["id"] == before["current_proposal_id"])
    approval = decide(client, prop, "approve-intel", actor="operator-1")
    assert approval.status_code == 201, approval.text

    response = set_policy(client, {"actor": "operator-1", "temporal_gate": True})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["changed"] is True
    assert result["policy"]["version"] == 1
    assert result["policy"]["updated_by"] == "operator-1"
    assert result["previous_policy"]["version"] == 0

    after = response.json()["state"]
    assert after["scene_revision"] == revision + 1
    assert after["current_proposal_id"] != prop["id"]
    stored = next(a for a in after["approvals"]
                  if a["id"] == approval.json()["result"]["id"])
    assert stored["status"] == "invalidated"

    page = events(client)
    updated = [e for e in page["events"]
               if e["type"] == "simulation.intelligence_policy_updated"]
    assert len(updated) == 1
    assert updated[0]["payload"]["actor"] == "operator-1"
    assert updated[0]["payload"]["policy"]["temporal_gate"] is True
    assert "simulation.approval_invalidated" in {e["type"] for e in page["events"]}

    snapshot = intelligence(client)
    assert snapshot["policy"]["version"] == 1
    assert snapshot["policy"]["temporal_gate"] is True


def test_identical_policy_is_a_duplicate_without_effect(client):
    before = reset(client)
    body = {"actor": "operator-1", "temporal_gate": True}
    first = set_policy(client, body, key="policy-1")
    assert first.status_code == 200, first.text
    revision = first.json()["state"]["scene_revision"]
    second = set_policy(client, body, key="policy-2")
    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is True
    assert second.json()["result"]["changed"] is False
    assert second.json()["state"]["scene_revision"] == revision
    assert before["scene_revision"] + 1 == revision


def test_policy_validation_rejects_relaxed_or_mismatched_objectives(client):
    reset(client)
    assert set_policy(client, {"actor": "operator-1",
                               "objective_policy": "stability_first"}).status_code == 422
    assert set_policy(client, {"actor": "operator-1", "stability_preference": True,
                               "objective_policy": "bounded_travel_stability",
                               }).status_code == 422
    assert set_policy(client, {"temporal_gate": True}).status_code == 422
    assert set_policy(client, {"actor": "operator-1", "mode": "real"}).status_code == 422


def test_temporal_gate_excludes_stale_new_work_with_prefixed_reasons(client, clock):
    positions = demo_positions(reset(client))
    observe_and_associate(client, clock, "cam-a", CALIBRATED, "casualty-001",
                          position=positions["casualty-001"])
    observe_and_associate(client, clock, "cam-b", CALIBRATED, "casualty-002",
                          obs_id="o2", track_id="t2",
                          position=positions["casualty-002"])
    clock.advance(60)

    stale = intelligence(client)
    first = by_id(stale, "casualty-001")
    assert first["uncertainty"]["eligible_for_new_assignment"] is False
    assert "observation_stale" in first["uncertainty"]["reasons"]
    assert first["uncertainty"]["raw_confidence"] == 0.9
    assert first["temporal_gate_applied"] is False
    assert any("temporal_gate is off" in warning for warning in stale["warnings"])

    assert set_policy(client, {"actor": "operator-1",
                               "temporal_gate": True}).status_code == 200
    gated = intelligence(client)
    first = by_id(gated, "casualty-001")
    second = by_id(gated, "casualty-002")
    third = by_id(gated, "casualty-003")
    assert first["temporal_gate_applied"] is True
    assert all(reason.startswith("temporal_gate:")
               for reason in first["exclusion_reasons"])
    assert "temporal_gate:observation_stale" in first["exclusion_reasons"]
    assert first["active_assignment_id"] is None
    assert second["temporal_gate_applied"] is True
    assert third["uncertainty"]["eligible_for_new_assignment"] is True
    assert third["temporal_gate_applied"] is False

    assigned = {item["casualty_id"]
                for proposal in state(client)["proposals"]
                if proposal["id"] == state(client)["current_proposal_id"]
                for item in proposal["assignments"]}
    assert "casualty-003" in assigned
    assert "casualty-001" not in assigned and "casualty-002" not in assigned


def test_active_work_is_preserved_past_freshness(client, clock):
    positions = demo_positions(reset(client))
    observe_and_associate(client, clock, "cam-a", CALIBRATED, "casualty-001",
                          position=positions["casualty-001"])
    observe_and_associate(client, clock, "cam-b", CALIBRATED, "casualty-002",
                          obs_id="o2", track_id="t2",
                          position=positions["casualty-002"])
    approve_and_dispatch(client, state(client))
    clock.advance(60)
    assert set_policy(client, {"actor": "operator-1",
                               "temporal_gate": True}).status_code == 200

    snapshot = intelligence(client)
    for casualty_id in ("casualty-001", "casualty-002"):
        entry = by_id(snapshot, casualty_id)
        assert entry["active_assignment_id"] is not None
        assert entry["uncertainty"]["eligible_for_new_assignment"] is False
        assert entry["temporal_gate_applied"] is False
        assert entry["exclusion_reasons"] == []
        assert any("preserved" in warning for warning in entry["warnings"])


def test_time_only_decay_invalidates_approvals_while_fresh_survives(client, clock):
    positions = demo_positions(reset(client))
    observe_and_associate(client, clock, "cam-a", CALIBRATED, "casualty-001",
                          position=positions["casualty-001"])
    assert set_policy(client, {"actor": "operator-1",
                               "temporal_gate": True}).status_code == 200
    fresh = state(client)
    prop = next(p for p in fresh["proposals"]
                if p["id"] == fresh["current_proposal_id"])
    approval = decide(client, prop, "approve-fresh", actor="operator-1")
    assert approval.status_code == 201, approval.text
    clock.advance(10)
    assert intelligence(client)["policy"]["temporal_gate"] is True
    kept = next(a for a in state(client)["approvals"]
                if a["id"] == approval.json()["result"]["id"])
    assert kept["status"] == "active"

    positions = demo_positions(reset(client))
    observe_and_associate(client, clock, "cam-a", CALIBRATED, "casualty-001",
                          position=positions["casualty-001"])
    assert set_policy(client, {"actor": "operator-1", "temporal_gate": True,
                               "decay_gates_eligibility": True,
                               "confidence_half_life_seconds": 10.0}).status_code == 200
    gated = state(client)
    prop = next(p for p in gated["proposals"]
                if p["id"] == gated["current_proposal_id"])
    approval = decide(client, prop, "approve-decay", actor="operator-1")
    assert approval.status_code == 201, approval.text
    clock.advance(10)

    snapshot = intelligence(client)
    entry = by_id(snapshot, "casualty-001")
    assert "decayed_confidence_below_threshold" in entry["uncertainty"]["reasons"]
    assert "temporal_gate:decayed_confidence_below_threshold" in entry["exclusion_reasons"]
    stored = next(a for a in state(client)["approvals"]
                  if a["id"] == approval.json()["result"]["id"])
    assert stored["status"] == "invalidated"
    assert stored["invalidated_reason"] == "observation_freshness_changed"
    assert state(client)["current_proposal_id"] != prop["id"]


def test_reject_and_reset_clear_previous_preferences(client):
    snapshot = reset(client)
    prop = next(p for p in snapshot["proposals"]
                if p["id"] == snapshot["current_proposal_id"])
    rejected = decide(client, prop, "reject-intel", decision="reject",
                      actor="operator-1")
    assert rejected.status_code == 201, rejected.text
    cleared = intelligence(client)
    assert cleared["previous_recommendations"] == []
    assert cleared["previous_recommendation_proposal_id"] is None

    assert set_policy(client, {"actor": "operator-1",
                               "temporal_gate": True}).status_code == 200
    assert intelligence(client)["policy"]["version"] == 1
    fresh = reset(client)
    restored = intelligence(client)
    assert restored["policy"]["version"] == 0
    assert restored["policy"]["temporal_gate"] is False
    current = next(p for p in fresh["proposals"]
                   if p["id"] == fresh["current_proposal_id"])
    assert restored["previous_recommendation_proposal_id"] == current["id"]
    assert restored["previous_recommendation_proposal_id"] != prop["id"]
    assert [(item["casualty_id"], item["responder_id"])
            for item in restored["previous_recommendations"]] == [
               (item["casualty_id"], item["responder_id"])
               for item in current["assignments"]]


def test_expired_pending_proposal_becomes_previous_with_reasoning(client, clock):
    snapshot = reset(client)
    prop = next(p for p in snapshot["proposals"]
                if p["id"] == snapshot["current_proposal_id"])
    clock.advance(200)
    expired = intelligence(client)
    assert expired["proposal"]["proposal_id"] == prop["id"]
    assert expired["proposal"]["status"] == "expired"
    assert expired["previous_recommendation_proposal_id"] == prop["id"]
    assert [(item["casualty_id"], item["responder_id"])
            for item in expired["previous_recommendations"]] == [
               (item["casualty_id"], item["responder_id"])
               for item in prop["assignments"]]


def test_zone_estimates_carry_provenance_and_opt_in_exclusion(client, clock):
    reset(client)
    observe_and_associate(client, clock, "cam-z", ZONES, "casualty-001",
                          zone_id="doorway")
    snapshot = intelligence(client)
    entry = by_id(snapshot, "casualty-001")
    assert entry["zone_id"] == "doorway"
    assert entry["zone_reference_position"] == {"frame_id": FRAME, "x_m": 10.0,
                                                "y_m": 16.0}
    assert entry["uncertainty"]["location_evidence"] == "cv_zone_estimate"
    assert entry["uncertainty"]["location_state"] == "estimated"
    assert entry["uncertainty"]["raw_confidence"] == 0.9
    assert any("reference point" in warning for warning in entry["warnings"])

    assert set_policy(client, {"actor": "operator-1", "temporal_gate": True,
                               "allow_zone_estimates": False}).status_code == 200
    gated = intelligence(client)
    entry = by_id(gated, "casualty-001")
    assert entry["temporal_gate_applied"] is True
    assert "temporal_gate:zone_estimate_not_allowed" in entry["exclusion_reasons"]


def test_comparisons_are_nonmutating_and_current(client):
    reset(client)
    before = state(client)
    before_events = events(client)
    response = compare(client, comparison_payload())
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["mode"] == "simulation"
    assert report["read_only"] is True
    assert report["installs_proposal"] is False
    assert report["authorizes_dispatch"] is False
    assert report["context"] == "current"
    assert report["stale_reasons"] == []
    assert report["source"]["policy"]["temporal_gate"] is False
    assert report["source"]["epoch"] == before["epoch"]
    assert report["source"]["scene_revision"] == before["scene_revision"]
    assert len(report["analysis"]["scenarios"]) == 1
    outcome = report["analysis"]["scenarios"][0]
    assert outcome["scenario_id"] == "s1"
    assert outcome["assumption_differences"] != []
    assert isinstance(outcome["route_changes"], list)
    assert "advisory" in report["interpretation"].lower()

    after = state(client)
    assert after["scene_revision"] == before["scene_revision"]
    assert after["sequence"] == before["sequence"]
    assert [p["id"] for p in after["proposals"]] == [p["id"] for p in before["proposals"]]
    assert [a["id"] for a in after["approvals"]] == [a["id"] for a in before["approvals"]]
    assert events(client)["latest_sequence"] == before_events["latest_sequence"]


def test_comparison_policy_override_does_not_persist(client):
    reset(client)
    response = compare(client, comparison_payload(
        policy={"temporal_gate": True, "confidence_half_life_seconds": 30.0}))
    assert response.status_code == 200, response.text
    assert response.json()["source"]["policy"]["temporal_gate"] is True
    assert response.json()["source"]["policy_version"] == 0
    assert intelligence(client)["policy"]["temporal_gate"] is False
    assert compare(client, {"scenarios": []}).status_code == 422
    assert client.get("/api/v1/simulations/other/intelligence").status_code == 404


def test_compare_marks_epoch_changed_when_reset_runs_during_solve(client, monkeypatch):
    import optimization.analysis as analysis

    reset(client)
    before_page = events(client)
    real_analyze = analysis.analyze_counterfactuals

    def reset_mid_solve(request):
        reset(client)
        return real_analyze(request)

    monkeypatch.setattr(analysis, "analyze_counterfactuals", reset_mid_solve)
    response = compare(client, comparison_payload())
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["context"] == "stale"
    assert "epoch_changed" in report["stale_reasons"]
    assert len(report["analysis"]["scenarios"]) == 1

    after = state(client)
    assert after["epoch"] == report["source"]["epoch"] + 1
    assert len(after["proposals"]) == 1
    assert after["proposals"][0]["revision"] == after["scene_revision"]
    fresh_types = {event["type"] for event in events(client)["events"]
                   if event["sequence"] > before_page["latest_sequence"]}
    assert fresh_types <= {"simulation.reset", "simulation.replan_requested",
                           "simulation.recommendation_computed",
                           "simulation.proposal_created"}


def test_compare_marks_revision_changed_when_policy_updates_during_solve(client,
                                                                        monkeypatch):
    import optimization.analysis as analysis

    before = reset(client)
    prop = next(p for p in before["proposals"]
                if p["id"] == before["current_proposal_id"])
    real_analyze = analysis.analyze_counterfactuals

    def policy_mid_solve(request):
        assert set_policy(client, {"actor": "operator-1",
                                   "temporal_gate": True}).status_code == 200
        return real_analyze(request)

    monkeypatch.setattr(analysis, "analyze_counterfactuals", policy_mid_solve)
    response = compare(client, comparison_payload())
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["context"] == "stale"
    assert "scene_revision_changed" in report["stale_reasons"]
    assert "epoch_changed" not in report["stale_reasons"]
    assert len(report["analysis"]["scenarios"]) == 1

    after = state(client)
    assert after["epoch"] == before["epoch"]
    assert after["scene_revision"] == report["source"]["scene_revision"] + 1
    assert len(after["proposals"]) == 2
    assert {p["status"] for p in after["proposals"]} == {"superseded", "pending_approval"}
    assert after["current_proposal_id"] != prop["id"]


def test_time_advanced_compare_is_stale_with_zero_mutation(client, clock):
    reset(client)
    assert set_policy(client, {"actor": "operator-1",
                               "stability_preference": True}).status_code == 200
    before = state(client)
    before_page = events(client)
    clock.advance(200)

    response = compare(client, comparison_payload())
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["context"] == "stale"
    assert report["stale_reasons"] == ["source_validity_horizon_reached"]
    assert report["source"]["source_valid_until"] is not None
    assert report["evaluated_at"] >= report["source"]["source_valid_until"]
    assert report["source"]["epoch"] == before["epoch"]
    assert report["source"]["scene_revision"] == before["scene_revision"]
    assert report["source"]["sequence"] == before["sequence"]
    assert len(report["analysis"]["scenarios"]) == 1

    after_page = events(client)
    assert after_page["latest_sequence"] == before_page["latest_sequence"]
    assert after_page["events"] == before_page["events"]


def test_evaluations_report_true_content_digests(client):
    reset(client)
    first = client.get(f"{BASE}/intelligence/evaluations")
    assert first.status_code == 200, first.text
    index = first.json()
    assert index["mode"] == "simulation"
    assert index["synthetic_only"] is True
    assert "synthetic" in index["interpretation"].lower()
    assert {artifact["name"] for artifact in index["artifacts"]} == set(REPORT_FILES)
    for artifact in index["artifacts"]:
        assert artifact["available"] is True
        assert artifact["unavailable_reason"] is None
        assert isinstance(artifact["report"], dict)
        expected = hashlib.sha256(
            (REPORT_ROOT / REPORT_FILES[artifact["name"]]).read_bytes()).hexdigest()
        assert artifact["sha256"] == expected

    second = client.get(f"{BASE}/intelligence/evaluations")
    assert second.status_code == 200, second.text
    assert [(a["name"], a["sha256"]) for a in second.json()["artifacts"]] == [
        (a["name"], a["sha256"]) for a in index["artifacts"]]
    assert client.get("/api/v1/simulations/other/intelligence/evaluations").status_code == 404


def test_policy_websocket_event_order_and_reconnect(client):
    reset(client)
    with client.websocket_connect(f"{BASE}/ws") as socket:
        first = socket.receive_json()
        assert first["type"] == "simulation.snapshot"
        response = set_policy(client, {"actor": "dashboard-operator", "temporal_gate": True})
        assert response.status_code == 200, response.text
        final = response.json()["state"]
        count = final["sequence"] - first["sequence"]
        assert 1 <= count <= 20
        received = [socket.receive_json() for _ in range(count)]
    assert [item["sequence"] for item in received] == list(
        range(first["sequence"] + 1, final["sequence"] + 1))
    assert len({item["event_id"] for item in received}) == count
    assert "simulation.intelligence_policy_updated" in {item["type"] for item in received}
    assert "simulation.dispatch_created" not in {item["type"] for item in received}
    with client.websocket_connect(f"{BASE}/ws") as socket:
        recovered = socket.receive_json()
    assert recovered["type"] == "simulation.snapshot"
    assert recovered["sequence"] == final["sequence"]
    assert recovered["payload"]["scene_revision"] == final["scene_revision"]
    assert intelligence(client)["policy"]["temporal_gate"] is True
