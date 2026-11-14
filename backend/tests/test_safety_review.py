from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

import pytest

from backend.app.models import CVObservation, Position
from backend.app.simulation.models import (
    AssociationRequest, DecisionRequest, DispatchRequest, ReplanRequest,
    SimulateDeviceRequest,
)
from backend.app.simulation.store import SimulationError, SimulationStore
from optimization.solver import optimize


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def observation(clock, identifier="observation-1", state="visible", x_m=4, confidence=0.9):
    return CVObservation(
        id=identifier, source="cv", observed_at=clock(),
        position=Position(frame_id="demo-floor-1", x_m=x_m, y_m=8),
        kind="person_detected", confidence=confidence,
        track={"track_id": "camera-session:1", "state": state,
               "first_seen_at": "2026-11-13T11:59:59Z", "last_seen_at": clock()},
    )


def current_proposal(store):
    state = store.snapshot("demo")
    return next(proposal for proposal in state.proposals if proposal.id == state.current_proposal_id)


def approve(store, proposal):
    return store.decide("demo", proposal.id, DecisionRequest(
        proposal_revision=proposal.revision, decision="approve", actor="commander",
    ), f"approve-{proposal.id}").result


def dispatch_request(proposal, approval):
    return DispatchRequest(
        proposal_id=proposal.id, proposal_revision=proposal.revision,
        approval_id=approval["id"], actor="commander",
    )


def dispatched_store():
    clock = Clock()
    store = SimulationStore(clock=clock)
    store.reset("demo")
    proposal = current_proposal(store)
    approval = approve(store, proposal)
    result = store.dispatch("demo", dispatch_request(proposal, approval), "dispatch-1")
    return store, clock, result.state.assignments[0]


def test_time_alone_cannot_leave_a_stale_observation_dispatchable():
    clock = Clock()
    store = SimulationStore(clock=clock, proposal_ttl_seconds=120, freshness_seconds=30)
    store.reset("demo")
    store.ingest_observation("demo", observation(clock))
    store.associate_track("demo", AssociationRequest(
        track_id="camera-session:1", casualty_id="casualty-001", actor="commander",
    ))
    proposal = current_proposal(store)
    assert any(item.casualty_id == "casualty-001" for item in proposal.assignments)
    approval = approve(store, proposal)
    clock.advance(31)
    with pytest.raises(SimulationError):
        store.dispatch("demo", dispatch_request(proposal, approval), "stale-dispatch")
    assert store.snapshot("demo").dispatches == []


def test_small_authoritative_moves_invalidate_revision():
    clock = Clock()
    store = SimulationStore(clock=clock, auto_recommend=False)
    store.reset("demo")
    store.ingest_observation("demo", observation(clock))
    store.associate_track("demo", AssociationRequest(
        track_id="camera-session:1", casualty_id="casualty-001", actor="commander",
    ))
    before = store.snapshot("demo").scene_revision
    clock.advance(1)
    store.ingest_observation("demo", observation(clock, "observation-2", x_m=4.01))
    assert store.snapshot("demo").scene_revision > before


def test_lost_update_cannot_replace_last_actual_detection():
    clock = Clock()
    store = SimulationStore(clock=clock, auto_recommend=False)
    store.reset("demo")
    store.ingest_observation("demo", observation(clock))
    clock.advance(1)
    with pytest.raises(SimulationError):
        store.ingest_observation("demo", observation(
            clock, "observation-2", state="lost", x_m=20, confidence=0.1,
        ))
    assert store.snapshot("demo").tracks[0].latest_observation.confidence == 0.9


def test_historical_map_revision_cannot_be_rebound_to_new_geometry():
    clock = Clock()
    store = SimulationStore(clock=clock, auto_recommend=False)
    store.reset("demo")
    original = store.snapshot("demo").scenario.scene_map.model_copy(deep=True)
    newer = original.model_copy(deep=True)
    newer.revision = "map-B"
    store.update_map("demo", newer)
    original.occupancy_grid.cells[0] = 100
    with pytest.raises(SimulationError):
        store.update_map("demo", original)
    assert store.snapshot("demo").scenario.scene_map.revision == "map-B"


def test_convenience_device_retry_keeps_generated_timestamp_stable():
    store, clock, assignment = dispatched_store()
    request = SimulateDeviceRequest(kind="delivered")
    first = store.simulate_device_event("demo", assignment.id, request, "delivery-1")
    clock.advance(2)
    second = store.simulate_device_event("demo", assignment.id, request, "delivery-1")
    assert second.duplicate
    assert second.result == first.result


def test_milestone_timestamp_contradiction_does_not_complete_or_release():
    store, clock, assignment = dispatched_store()
    dispatched_at = clock()
    clock.advance(10)
    store.simulate_device_event("demo", assignment.id, SimulateDeviceRequest(
        kind="delivered", event_id="delivery", occurred_at=dispatched_at + timedelta(seconds=9),
    ))
    with pytest.raises(SimulationError):
        store.simulate_device_event("demo", assignment.id, SimulateDeviceRequest(
            kind="completed", event_id="completion", occurred_at=dispatched_at + timedelta(seconds=1),
        ))
    state = store.snapshot("demo")
    assert assignment.casualty_id not in state.resolved_casualty_ids
    stored = next(item for item in state.assignments if item.id == assignment.id)
    assert stored.status == "delivered"
    assert stored.completed_at is None


def test_concurrent_replan_key_is_reserved_before_solver_unlock():
    clock = Clock()
    entered = threading.Event()
    release = threading.Event()

    def blocking_solver(request):
        entered.set()
        assert release.wait(timeout=5)
        return optimize(request)

    store = SimulationStore(clock=clock, solver=blocking_solver, auto_recommend=False)
    store.reset("demo")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(store.replan, "demo", ReplanRequest(actor="one", reason="first"), "same-key")
        assert entered.wait(timeout=2)
        try:
            second = pool.submit(store.replan, "demo", ReplanRequest(actor="two", reason="conflict"), "same-key")
            with pytest.raises(SimulationError) as error:
                second.result(timeout=2)
            assert error.value.status_code == 409
        finally:
            release.set()
        first.result(timeout=5)
