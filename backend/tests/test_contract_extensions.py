import json

import pytest
from pydantic import ValidationError

from backend.app.models import (
    AStarRoute,
    Assignment,
    CVObservation,
    DeviceAssignmentEvent,
    DispatchProposal,
    OccupancyGrid,
    RecommendationMetadata,
    Scenario,
    SceneMap,
)
from backend.app.services.scenarios import DEMO_SCENARIO_PATH


def position(x_m=0, y_m=0, frame_id="demo-floor-1"):
    return {"frame_id": frame_id, "x_m": x_m, "y_m": y_m}


@pytest.fixture
def tracked_observation():
    return {
        "id": "observation-002",
        "source": "cv",
        "observed_at": "2026-11-13T12:00:02Z",
        "position": position(4, 8),
        "kind": "person_detected",
        "confidence": 0.9,
        "track": {
            "track_id": "camera-session-1:track-7",
            "state": "visible",
            "first_seen_at": "2026-11-13T12:00:00Z",
            "last_seen_at": "2026-11-13T12:00:01Z",
        },
    }


@pytest.fixture
def scene_map():
    return {
        "frame_id": "demo-floor-1",
        "revision": "map-001",
        "obstacles": [{
            "id": "obstacle-001",
            "vertices": [position(2, 2), position(3, 2), position(3, 3)],
        }],
        "occupancy_grid": {
            "origin": position(),
            "resolution_m": 0.5,
            "width": 2,
            "height": 2,
            "cells": [0, 100, -1, 0],
        },
    }


@pytest.fixture
def route():
    return {
        "algorithm": "astar",
        "map_revision": "map-001",
        "waypoints": [position(), position(0.5, 0.5)],
        "distance_m": 0.5 ** 0.5,
    }


@pytest.fixture
def recommendation():
    return {
        "solver_name": "mock-solver",
        "solver_version": "0.1.0",
        "generated_at": "2026-11-13T12:00:00Z",
        "objective": "minimize_total_distance",
        "status": "partial",
        "unassigned_casualty_ids": ["casualty-003"],
        "objective_value": 10.5,
    }


@pytest.fixture
def device_event():
    return {
        "id": "device-event-001",
        "scenario_id": "demo",
        "device_id": "mock-device-001",
        "dispatch_id": "mock-dispatch-001",
        "command_id": "mock-command-001",
        "casualty_id": "casualty-001",
        "responder_id": "responder-001",
        "kind": "delivered",
        "occurred_at": "2026-11-13T12:00:02Z",
        "mode": "simulation",
    }


def test_original_scenario_is_compatible_and_serialization_unchanged():
    original = json.loads(DEMO_SCENARIO_PATH.read_text(encoding="utf-8"))
    scenario = Scenario.model_validate(original)
    assert scenario.scene_map is None
    assert all(responder.capabilities == [] for responder in scenario.responders)
    assert scenario.model_dump(mode="json", exclude_unset=True) == original
    scenario.responders[0].capabilities.append("aed")
    assert scenario.responders[1].capabilities == []


@pytest.mark.parametrize("state", ["visible", "occluded", "lost"])
def test_track_states_preserve_identity_and_last_detection(tracked_observation, state):
    tracked_observation["track"]["state"] = state
    observation = CVObservation.model_validate(tracked_observation)
    assert observation.track.track_id == "camera-session-1:track-7"
    assert observation.track.last_seen_at < observation.observed_at
    assert observation.confidence == 0.9


def test_legacy_cv_observation_needs_no_track(tracked_observation):
    del tracked_observation["track"]
    assert CVObservation.model_validate(tracked_observation).track is None


@pytest.mark.parametrize("change", [
    {"track_id": ""},
    {"state": "deceased"},
    {"first_seen_at": "2026-11-13T12:00:02Z"},
    {"last_seen_at": "2026-11-13T12:00:03Z"},
    {"last_seen_at": "2026-11-13T12:00:01"},
    {"severity": "immediate"},
])
def test_invalid_track_payloads_are_rejected(tracked_observation, change):
    tracked_observation["track"].update(change)
    with pytest.raises(ValidationError):
        CVObservation.model_validate(tracked_observation)


def test_scene_map_and_capabilities_extend_original_scenario(scene_map):
    scenario = json.loads(DEMO_SCENARIO_PATH.read_text(encoding="utf-8"))
    scenario["scene_map"] = scene_map
    scenario["responders"][0]["capabilities"] = ["first_aid", "aed"]
    parsed = Scenario.model_validate(scenario)
    assert parsed.scene_map.occupancy_grid.cells == [0, 100, -1, 0]
    assert parsed.responders[0].capabilities == ["first_aid", "aed"]
    scenario["responders"][0]["capabilities"] = [""]
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario)


@pytest.mark.parametrize("change", [
    {"cells": [0]},
    {"cells": [0, 50, 0, 0]},
    {"cells": [0, False, 0, 0]},
    {"cells": [0, 0.0, 0, 0]},
    {"width": 0},
    {"height": -1},
    {"width": 2.5},
    {"width": True},
    {"resolution_m": 0},
    {"resolution_m": float("inf")},
])
def test_invalid_grid_payloads_are_rejected(scene_map, change):
    grid = {**scene_map["occupancy_grid"], **change}
    with pytest.raises(ValidationError):
        OccupancyGrid.model_validate(grid)


@pytest.mark.parametrize("invalid_part", ["vertices", "origin", "duplicates", "polygon"])
def test_invalid_scene_geometry_is_rejected(scene_map, invalid_part):
    if invalid_part == "vertices":
        scene_map["obstacles"][0]["vertices"][0]["frame_id"] = "other-frame"
    elif invalid_part == "origin":
        scene_map["occupancy_grid"]["origin"]["frame_id"] = "other-frame"
    elif invalid_part == "duplicates":
        scene_map["obstacles"].append(scene_map["obstacles"][0])
    else:
        scene_map["obstacles"][0]["vertices"] = [position()]
    with pytest.raises(ValidationError):
        SceneMap.model_validate(scene_map)


def test_scene_map_must_match_scenario_frame(scene_map):
    scenario = json.loads(DEMO_SCENARIO_PATH.read_text(encoding="utf-8"))
    scene_map.update(frame_id="other-frame", obstacles=[], occupancy_grid=None)
    scenario["scene_map"] = scene_map
    with pytest.raises(ValidationError, match="scenario coordinate frame"):
        Scenario.model_validate(scenario)


def test_scene_map_can_explicitly_omit_unavailable_occupancy(scene_map):
    scene_map["occupancy_grid"] = None
    assert SceneMap.model_validate(scene_map).occupancy_grid is None


def test_assignment_accepts_legacy_or_meter_route(route):
    assignment = {"casualty_id": "casualty-001", "responder_id": "responder-001"}
    assert Assignment.model_validate(assignment).route is None
    parsed = Assignment.model_validate({**assignment, "route": route})
    assert parsed.route.waypoints[1].x_m == 0.5


@pytest.mark.parametrize("change", [
    {"waypoints": []},
    {"waypoints": [position(), position(frame_id="camera-pixels")]},
    {"waypoints": [{"row": 0, "column": 0}, {"row": 1, "column": 1}]},
    {"distance_m": -1},
    {"distance_m": float("nan")},
    {"map_revision": ""},
    {"algorithm": "unverified"},
])
def test_invalid_route_payloads_are_rejected(route, change):
    with pytest.raises(ValidationError):
        AStarRoute.model_validate({**route, **change})


def test_proposal_accepts_optional_solver_metadata(recommendation):
    proposal = {
        "id": "proposal-001",
        "scenario_id": "demo",
        "revision": 1,
        "status": "pending_approval",
        "assignments": [],
        "created_at": "2026-11-13T12:00:00Z",
        "expires_at": "2026-11-13T12:01:00Z",
    }
    assert DispatchProposal.model_validate(proposal).recommendation is None
    parsed = DispatchProposal.model_validate({**proposal, "recommendation": recommendation})
    assert parsed.status == "pending_approval"
    assert parsed.recommendation.unassigned_casualty_ids == ["casualty-003"]


@pytest.mark.parametrize("change", [
    {"solver_version": ""},
    {"status": "approved"},
    {"generated_at": "2026-11-13T12:00:00"},
    {"objective_value": float("nan")},
    {"severity": "immediate"},
    {"approved_by": "operator-001"},
])
def test_invalid_solver_metadata_is_rejected(recommendation, change):
    with pytest.raises(ValidationError):
        RecommendationMetadata.model_validate({**recommendation, **change})


@pytest.mark.parametrize("kind", ["delivered", "acknowledged", "completed"])
@pytest.mark.parametrize("mode", ["simulation", "real"])
def test_device_event_milestones_are_explicit(device_event, kind, mode):
    parsed = DeviceAssignmentEvent.model_validate({**device_event, "kind": kind, "mode": mode})
    assert parsed.kind == kind
    assert parsed.mode == mode
    assert parsed.command_id == "mock-command-001"


@pytest.mark.parametrize("field", [
    "id", "scenario_id", "device_id", "dispatch_id", "command_id", "casualty_id",
    "responder_id", "kind", "occurred_at", "mode",
])
def test_device_event_requires_correlation_and_explicit_mode(device_event, field):
    del device_event[field]
    with pytest.raises(ValidationError):
        DeviceAssignmentEvent.model_validate(device_event)


@pytest.mark.parametrize("change", [
    {"kind": "approved"},
    {"mode": "automatic"},
    {"command_id": ""},
    {"occurred_at": "2026-11-13T12:00:02"},
    {"approved_by": "operator-001"},
    {"medical_assessment": {"severity": "minor"}},
])
def test_device_events_cannot_carry_approval_or_clinical_data(device_event, change):
    with pytest.raises(ValidationError):
        DeviceAssignmentEvent.model_validate({**device_event, **change})
