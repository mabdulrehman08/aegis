import builtins
import itertools
from datetime import timedelta

import pytest
from pydantic import ValidationError

from backend.app.models import Assignment, CVObservation, CVTrack, MedicalAssessment
from optimization.models import OptimizationRequest
from optimization.routing import plan_route
from optimization.solver import _greedy, optimize
from optimization.tests.conftest import NOW, casualty, position, responder, scene_map

ENGINES = ["nearest", "fallback", "ortools"]


def request(scenario, solver="fallback", **kwargs):
    return OptimizationRequest(scenario=scenario, solver=solver, **{"now": NOW, **kwargs})


def assessment(severity):
    return MedicalAssessment(severity=severity, source="human", assessed_by="human-operator",
                             assessed_at=NOW - timedelta(seconds=1))


def observation(casualty_entity, age=0, confidence=0.9, state=None, last_seen_age=0):
    return CVObservation(id="observation-id-not-casualty-id", source="cv", kind="person_detected",
                         observed_at=NOW - timedelta(seconds=age), position=casualty_entity.position,
                         confidence=confidence, track=None if state is None else CVTrack(
                             track_id="track-1", state=state, first_seen_at=NOW - timedelta(minutes=5),
                             last_seen_at=NOW - timedelta(seconds=last_seen_age)))


def pairs(result):
    return [(assignment.casualty_id, assignment.responder_id) for assignment in result.assignments]


@pytest.mark.parametrize("engine", ENGINES)
def test_shared_feasibility_and_exact_routes(scenario, engine):
    scenario.responders = [responder("busy", status="busy", capabilities=["aed"]),
                           responder("offline", status="offline", capabilities=["aed"]),
                           responder("no-tags"), responder("qualified", capabilities=["aed", "first_aid"])]
    result = optimize(request(scenario, engine, required_capabilities={"c1": ["aed", "first_aid"],
                                                                    "c2": ["stretcher"]}))
    assert pairs(result) == [("c1", "qualified")]
    assert result.unassigned_casualty_ids == ["c2"]
    assert "required_capabilities_missing" in result.reasons["c2"]
    assert result.metadata.status == "partial"
    assert result.scene_revision == 1
    assert result.map_revision == "map-7"
    route = result.assignments[0].route
    assert route.waypoints[0] == scenario.responders[-1].position
    assert route.waypoints[-1] == scenario.casualties[0].position
    assert route == plan_route(scenario.scene_map, scenario.responders[-1].position,
                               scenario.casualties[0].position)


@pytest.mark.parametrize("engine", ENGINES)
def test_unreachable_casualty_is_excluded_for_every_engine(scenario, engine):
    scenario.scene_map = scene_map(width=8, height=6, cells=[0, 0, -1, 0, 0, 0, 0, 0] * 6)
    result = optimize(request(scenario, engine))
    assert result.assignments == []
    assert result.metadata.status == "infeasible"
    assert all("unreachable" in result.reasons[identifier] for identifier in ["c1", "c2"])


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("grid_missing", [False, True])
def test_no_map_or_grid_produces_no_assignments(scenario, engine, grid_missing):
    scenario.scene_map = (scenario.scene_map.model_copy(update={"occupancy_grid": None})
                          if grid_missing else None)
    result = optimize(request(scenario, engine,
                             active_assignments=[Assignment(casualty_id="c1", responder_id="r1")]))
    assert result.assignments == []
    assert result.unassigned_casualty_ids == ["c2"]
    assert result.preserved_assignments == [Assignment(casualty_id="c1", responder_id="r1")]
    assert result.objective_values["preserved_assigned_count"] == 1
    assert result.objective_values["new_assigned_count"] == 0
    assert result.engine == "none"
    assert ("occupancy_grid_unavailable" if grid_missing else "map_unavailable") in result.reasons["c2"]
    assert "active_assignment_preserved" in result.reasons["c1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_existing_active_assignments_reserved_and_unchanged_despite_uncertainty(scenario, engine):
    scenario.responders[0].status = "busy"
    old_route = plan_route(scenario.scene_map, position(), scenario.casualties[0].position)
    old_route.map_revision = "old-map"
    active = Assignment(casualty_id="c1", responder_id="r1", route=old_route)
    supplied = request(scenario, engine, active_assignments=[active],
                       observations={"c1": observation(scenario.casualties[0], confidence=0.1)},
                       required_capabilities={"c1": ["undeclared"]})
    before = supplied.model_dump()
    result = optimize(supplied)
    assert pairs(result) == [("c2", "r2")]
    assert result.preserved_assignments == [active]
    assert result.preserved_assignments[0] is not active
    assert result.unassigned_casualty_ids == []
    assert result.objective_values["assigned_count"] == 1
    assert result.objective_values["preserved_assigned_count"] == 1
    assert "active_assignment_preserved" in result.reasons["c1"]
    assert "observation_low_confidence" in result.reasons["c1"]
    assert "active_route_stale_map" in result.reasons["c1"]
    assert supplied.model_dump() == before


@pytest.mark.parametrize("active", [
    [("missing", "r1")], [("c1", "missing")],
    [("c1", "r1"), ("c2", "r1")], [("c1", "r1"), ("c1", "r2")],
    [("c1", "r1"), ("c1", "r1")],
])
def test_invalid_active_assignments_raise_instead_of_silently_reassigning(scenario, active):
    with pytest.raises(ValueError, match="Active assignments"):
        optimize(request(scenario, active_assignments=[Assignment(casualty_id=casualty_id,
                                                                  responder_id=responder_id)
                                                       for casualty_id, responder_id in active]))


@pytest.mark.parametrize("kwargs,reason", [
    ({"age": 31}, "observation_stale"),
    ({"age": -1}, "observation_future_dated"),
    ({"confidence": 0.49}, "observation_low_confidence"),
    ({"state": "lost"}, "track_lost"),
    ({"state": "occluded"}, "track_occluded"),
    ({"state": "visible", "last_seen_age": 31}, "track_stale"),
])
@pytest.mark.parametrize("engine", ENGINES)
def test_casualty_keyed_uncertainty_blocks_new_work_for_every_engine(scenario, kwargs, reason, engine):
    result = optimize(request(scenario, engine,
                             observations={"c1": observation(scenario.casualties[0], **kwargs)}))
    assert "c1" in result.unassigned_casualty_ids
    assert reason in result.reasons["c1"]
    assert pairs(result)[0][0] == "c2"
    assert scenario.casualties[0].medical_assessment is None


def test_observation_position_and_frame_mismatch_are_not_silently_routed(scenario):
    seen = observation(scenario.casualties[0])
    seen.position = position(4.5, 0.5, frame_id="foreign")
    result = optimize(request(scenario, observations={"c1": seen}))
    assert "observation_position_mismatch" in result.reasons["c1"]


@pytest.mark.parametrize("offset,accepted", [(0, True), (0.249, True), (0.25, True),
                                             (0.2500001, False), (0.3, False)])
def test_observation_position_tolerance_boundary_uses_authoritative_route(scenario, offset, accepted):
    seen = observation(scenario.casualties[0])
    seen.position = position(4.5 + offset, 0.5)
    result = optimize(request(scenario, observations={"c1": seen}))
    assigned = {assignment.casualty_id: assignment for assignment in result.assignments}
    assert ("c1" in assigned) is accepted
    if accepted:
        assert assigned["c1"].route.waypoints[-1] == scenario.casualties[0].position
    else:
        assert "observation_position_mismatch" in result.reasons["c1"]


def test_position_tolerance_is_euclidean_and_configurable(scenario):
    seen = observation(scenario.casualties[0])
    seen.position = position(4.7, 0.7)
    assert "c1" in optimize(request(scenario, observations={"c1": seen})).unassigned_casualty_ids
    assert "c1" not in optimize(request(scenario, observations={"c1": seen},
                                        position_tolerance_m=0.3)).unassigned_casualty_ids


def test_freshness_confidence_boundary_and_missing_observation_accepted(scenario):
    seen = observation(scenario.casualties[0], age=30, confidence=0.5,
                       state="visible", last_seen_age=30)
    result = optimize(request(scenario, observations={"c1": seen}))
    assert len(result.assignments) == 2
    assert result.metadata.status == "feasible"


@pytest.mark.parametrize("kwargs", [
    {"required_capabilities": {"missing": []}},
    {"required_capabilities": {"c1": [""]}},
    {"required_capabilities": {"c1": ["   "]}},
])
def test_invalid_capability_keys_and_tags_rejected(scenario, kwargs):
    with pytest.raises(ValueError):
        optimize(request(scenario, **kwargs))


def test_unknown_observation_casualty_id_rejected(scenario):
    with pytest.raises(ValueError):
        optimize(request(scenario, observations={"observation-id": observation(scenario.casualties[0])}))


@pytest.mark.parametrize("kwargs", [
    {"freshness_seconds": -1}, {"freshness_seconds": float("nan")},
    {"min_confidence": 1.1}, {"scene_revision": 0}, {"now": NOW.replace(tzinfo=None)},
    {"position_tolerance_m": -1}, {"position_tolerance_m": float("inf")},
])
def test_request_validation(scenario, kwargs):
    with pytest.raises(ValidationError):
        request(scenario, **kwargs)


@pytest.mark.parametrize("severity", [None, "immediate", "expectant", "deceased"])
def test_protected_cases_are_not_deprioritized_by_minor_label(scenario, severity):
    scenario.casualties = [casualty("z-protected", 6.5, assessment=None if severity is None else assessment(severity)),
                           casualty("a-minor", 1.5, assessment=assessment("minor"))]
    scenario.responders = [responder("r1")]
    result = optimize(request(scenario))
    assert pairs(result) == [("z-protected", "r1")]
    assert result.objective_values["allocation_priority_points"] == 2
    assert "no automatic clinical expectant/deceased deprioritization" in result.rationale[0]


def test_human_confirmed_delayed_before_minor_without_modifying_assessments(scenario):
    scenario.casualties = [casualty("minor", 1.5, assessment=assessment("minor")),
                           casualty("delayed", 6.5, assessment=assessment("delayed"))]
    scenario.responders = [responder("r1")]
    before = scenario.model_dump()
    assert pairs(optimize(request(scenario))) == [("delayed", "r1")]
    assert scenario.model_dump() == before


def test_fallback_augmenting_path_avoids_greedy_cardinality_loss(scenario):
    scenario.casualties = [casualty("a-flexible", 0.5), casualty("b-special", 2.5)]
    scenario.responders = [responder("r-special", 0.5, capabilities=["aed"]),
                           responder("r-basic", 7.5)]
    kwargs = {"required_capabilities": {"b-special": ["aed"]}}
    assert len(optimize(request(scenario, "nearest", **kwargs)).assignments) == 1
    result = optimize(request(scenario, "fallback", **kwargs))
    assert pairs(result) == [("a-flexible", "r-basic"), ("b-special", "r-special")]


def test_missing_ortools_selects_deterministic_fallback(monkeypatch, scenario):
    original_import = builtins.__import__

    def without_ortools(name, *args, **kwargs):
        if name.startswith("ortools"):
            raise ImportError("test optional dependency unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_ortools)
    result = optimize(request(scenario, "ortools"))
    assert result.engine == "fallback"
    assert pairs(result) == pairs(optimize(request(scenario, "fallback")))
    assert any("OR-Tools unavailable" in text for text in result.rationale)


@pytest.mark.parametrize("solve_status", ["UNKNOWN", "FEASIBLE"])
def test_bounded_nonoptimal_ortools_stage_uses_fallback(monkeypatch, scenario, solve_status):
    pytest.importorskip("ortools")
    from ortools.sat.python import cp_model

    def nonoptimal(solver, model):
        assert 0 < solver.parameters.max_time_in_seconds <= 1
        return getattr(cp_model, solve_status)

    monkeypatch.setattr(cp_model.CpSolver, "Solve", nonoptimal)
    result = optimize(request(scenario, "ortools"))
    assert result.engine == "fallback"
    assert pairs(result) == pairs(optimize(request(scenario, "fallback")))
    assert any("No CP-SAT optimality is claimed" in text for text in result.rationale)


@pytest.mark.parametrize("missing_map", [False, True])
def test_only_active_work_is_not_proposed_or_unassigned_and_null_route_warns(scenario, missing_map):
    scenario.casualties = scenario.casualties[:1]
    if missing_map:
        scenario.scene_map = None
    active = Assignment(casualty_id="c1", responder_id="r1")
    result = optimize(request(scenario, active_assignments=[active]))
    assert result.assignments == []
    assert result.preserved_assignments == [active]
    assert result.unassigned_casualty_ids == []
    assert result.metadata.status == "feasible"
    assert result.metadata.objective_value == 0
    assert result.objective_values["assigned_count"] == 0
    assert "active_route_unverified" in result.reasons["c1"]


def test_capability_tags_are_case_sensitive_and_absence_means_no_requirement(scenario):
    scenario.responders = [responder("r1", capabilities=["AED"])]
    result = optimize(request(scenario, required_capabilities={"c1": ["aed"]}))
    assert pairs(result) == [("c2", "r1")]
    assert "required_capabilities_missing" in result.reasons["c1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_deterministic_order_independent_outputs_except_runtime(scenario, engine):
    first = optimize(request(scenario, engine)).model_dump(exclude={"runtime_ms"})
    scenario.casualties.reverse()
    scenario.responders.reverse()
    second = optimize(request(scenario, engine)).model_dump(exclude={"runtime_ms"})
    assert first == second


def test_empty_scene_and_no_responders(scenario):
    scenario.responders = []
    result = optimize(request(scenario))
    assert result.assignments == []
    assert result.reasons == {"c1": ["no_responders"], "c2": ["no_responders"]}
    scenario.casualties = []
    result = optimize(request(scenario))
    assert result.metadata.status == "feasible"


def test_ortools_count_before_priority_and_cost(scenario):
    pytest.importorskip("ortools")
    scenario.casualties = [casualty("a-flexible", 0.5, assessment=assessment("immediate")),
                           casualty("b-special", 2.5, assessment=assessment("minor"))]
    scenario.responders = [responder("r-special", 0.5, capabilities=["aed"]),
                           responder("r-basic", 7.5)]
    result = optimize(request(scenario, "ortools", required_capabilities={"b-special": ["aed"]}))
    assert result.engine == "ortools"
    assert pairs(result) == [("a-flexible", "r-basic"), ("b-special", "r-special")]


def test_ortools_travel_and_exact_deterministic_ties(scenario):
    pytest.importorskip("ortools")
    scenario.casualties = [casualty("c1", 1.5), casualty("c2", 6.5)]
    scenario.responders = [responder("r1", 0.5), responder("r2", 7.5)]
    result = optimize(request(scenario, "ortools"))
    assert pairs(result) == [("c1", "r1"), ("c2", "r2")]
    assert result.objective_values["new_travel_distance_m"] == 2
    scenario.responders[1].position = scenario.responders[0].position
    assert pairs(optimize(request(scenario, "ortools"))) == [("c1", "r1"), ("c2", "r2")]


def test_fallback_maximum_cardinality_and_priority_exhaustive_small_graphs():
    route = plan_route(scene_map(), position(), position())
    all_pairs = list(itertools.product(["c1", "c2", "c3"], ["r1", "r2"]))
    priorities = {"c1": 0, "c2": 1, "c3": 2}
    for mask in range(1 << len(all_pairs)):
        routes = {pair: route for index, pair in enumerate(all_pairs) if mask & (1 << index)}
        result = _greedy(routes, priorities, nearest=False)
        result_score = (len(result), sum(priorities[pair[0]] for pair in result))
        scores = []
        for count in range(3):
            for selected in itertools.combinations(routes, count):
                if len({pair[0] for pair in selected}) == count and len({pair[1] for pair in selected}) == count:
                    scores.append((count, sum(priorities[pair[0]] for pair in selected)))
        assert result_score == max(scores)
