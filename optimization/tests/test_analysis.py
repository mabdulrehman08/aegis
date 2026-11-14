import builtins
from datetime import timedelta

import pytest
from pydantic import ValidationError

from backend.app.models import Assignment, MedicalAssessment, Obstacle, Position, Scenario
from optimization.analysis import (
    MAX_SCENARIOS, MAX_SOLVER_CALLS, AnalysisInputError, CounterfactualAnalysis,
    CounterfactualRequest, CounterfactualScenario, ObstacleAddedChange, ObstacleRemovedChange,
    PreferenceChange, ResponderAvailabilityChange, ResponderCapabilityChange, analyze_counterfactuals,
)
from optimization.models import OptimizationRequest
from optimization.tests.conftest import NOW, casualty, responder, scene_map

ENGINES = ["nearest", "fallback", "ortools"]


def assessed(severity):
    return MedicalAssessment(severity=severity, source="human", assessed_by="human-operator",
                             assessed_at=NOW)


def base_request(solver="fallback", casualties=None, responders=None, **kwargs):
    scene = Scenario(schema_version="1.0", id="scene", name="Synthetic", synthetic=True,
                     frame_id="world", created_at=NOW,
                     casualties=casualties or [casualty("c1", 1.5, assessment=assessed("immediate")),
                                    casualty("c2", 6.5, assessment=assessed("minor"))],
                     responders=responders or [responder("r1"), responder("r2", 7.5)],
                     scene_map=kwargs.pop("scene_map", scene_map()))
    return OptimizationRequest(scenario=scene, now=NOW, solver=solver, **kwargs)


def scenario(identifier, *changes, **kwargs):
    return CounterfactualScenario(id=identifier, label=f"scenario {identifier}",
                                  changes=list(changes), **kwargs)


def offline(responder_id):
    return ResponderAvailabilityChange(kind="responder_availability", responder_id=responder_id,
                                       status="offline")


def run(base, *scenarios, **kwargs):
    return analyze_counterfactuals(CounterfactualRequest(base=base, scenarios=list(scenarios), **kwargs))


def wall(identifier="wall"):
    return Obstacle(id=identifier, vertices=[Position(frame_id="world", x_m=2.2, y_m=0.0),
                                             Position(frame_id="world", x_m=2.8, y_m=0.0),
                                             Position(frame_id="world", x_m=2.8, y_m=6.0),
                                             Position(frame_id="world", x_m=2.2, y_m=6.0)])


def pairs(result):
    return [(item.casualty_id, item.responder_id) for item in result.assignments]


@pytest.mark.parametrize("engine", ["fallback", "ortools"])
def test_availability_change_reruns_real_solver_with_exact_deltas(engine):
    base = base_request(engine)
    analysis = run(base, scenario("lose-r1", offline("r1")))
    outcome = analysis.scenarios[0]
    assert pairs(analysis.baseline) == [("c1", "r1"), ("c2", "r2")]
    assert pairs(outcome.recommendation) == [("c1", "r2")]
    assert [(change.casualty_id, change.kind, change.scenario_distance_m)
            for change in outcome.changed_assignments] == [
        ("c1", "responder_changed", 6.0), ("c2", "removed", None)]
    assert outcome.newly_unassigned_casualty_ids == ["c2"]
    assert outcome.newly_assigned_casualty_ids == []
    deltas = {delta.name: delta for delta in outcome.objective_deltas}
    assert deltas["new_assigned_count"].delta == -1
    assert deltas["new_assigned_count"].outcome == "worse"
    assert deltas["allocation_priority_points"].delta == 0
    assert deltas["allocation_priority_points"].outcome == "same"
    assert deltas["new_travel_cost_mm"].delta == 4000
    assert outcome.objective_verdict == "degraded"
    assert [(item.field, item.entity_id, item.before, item.after)
            for item in outcome.assumption_differences] == [
        ("responder.status", "r1", "available", "offline")]
    reasons = {change.casualty_id: change for change in outcome.reason_changes}
    assert reasons["c2"].scenario_reasons == ["feasible_but_capacity_or_objective_limited"]
    assert outcome.engine == engine and outcome.base_engine == engine
    assert outcome.engine_matches_base and not outcome.fallback_used
    assert analysis.solver_calls == 2


def test_nearest_engine_reports_its_own_greedy_result():
    analysis = run(base_request("nearest"), scenario("lose-r1", offline("r1")))
    outcome = analysis.scenarios[0]
    assert pairs(outcome.recommendation) == [("c2", "r2")]
    assert [(change.casualty_id, change.kind) for change in outcome.changed_assignments] == [
        ("c1", "removed")]
    deltas = {delta.name: delta for delta in outcome.objective_deltas}
    assert deltas["allocation_priority_points"].delta == -2
    assert deltas["new_travel_cost_mm"].delta == -1000
    assert not any(tier.proven_optimal for tier in outcome.recommendation.objective_tiers)


@pytest.mark.parametrize("engine", ["fallback", "ortools"])
def test_capability_change_makes_new_assignment_possible(engine):
    base = base_request(engine, required_capabilities={"c2": ["aed"]})
    change = ResponderCapabilityChange(kind="responder_capabilities", responder_id="r2",
                                       capabilities=["aed"])
    analysis = run(base, scenario("qualify", change))
    assert pairs(analysis.baseline) == [("c1", "r1")]
    outcome = analysis.scenarios[0]
    assert outcome.newly_assigned_casualty_ids == ["c2"]
    assert pairs(outcome.recommendation) == [("c1", "r1"), ("c2", "r2")]
    assert outcome.objective_verdict == "improved"
    assert outcome.reason_changes[0].base_reasons == ["required_capabilities_missing"]
    assert outcome.reason_changes[0].scenario_reasons == ["assigned_feasible_route"]
    assert outcome.assumption_differences[0].before == [] and outcome.assumption_differences[0].after == ["aed"]


@pytest.mark.parametrize("engine", ENGINES)
def test_obstacle_makes_casualties_unreachable_with_new_map_revision(engine):
    base = base_request(engine, casualties=[casualty("c1", 4.5)], responders=[responder("r1")])
    analysis = run(base, scenario("wall", ObstacleAddedChange(kind="obstacle_added", obstacle=wall())))
    outcome = analysis.scenarios[0]
    assert analysis.map_revision == "map-7"
    assert outcome.map_revision == "map-7+cf-wall"
    assert outcome.recommendation.assignments == []
    assert outcome.recommendation.reasons["c1"] == ["unreachable"]
    assert outcome.recommendation.metadata.status == "infeasible"
    fields = {(item.field, item.entity_id) for item in outcome.assumption_differences}
    assert fields == {("map.obstacle", "wall"), ("map.revision", None)}
    assert analysis.scenarios[0].changed_assignments[0].kind == "removed"


def test_obstacle_removal_restores_reachability_and_custom_revision():
    base = base_request(casualties=[casualty("c1", 4.5)], responders=[responder("r1")],
                        scene_map=scene_map().model_copy(update={"obstacles": [wall("existing")]}))
    analysis = run(base, scenario("open", ObstacleRemovedChange(kind="obstacle_removed",
                                                                 obstacle_id="existing"),
                                  map_revision="map-open"))
    assert analysis.baseline.assignments == []
    assert pairs(analysis.scenarios[0].recommendation) == [("c1", "r1")]
    assert analysis.scenarios[0].map_revision == "map-open"


def test_active_work_exactly_preserved_and_stale_route_reported_not_rewritten():
    base = base_request(active_assignments=[Assignment(casualty_id="c1", responder_id="r1")])
    analysis = run(base, scenario("wall", ObstacleAddedChange(kind="obstacle_added",
                                                                 obstacle=wall())),
                   scenario("lose-r1", offline("r1")))
    for outcome in analysis.scenarios:
        assert outcome.preserved_assignments == base.active_assignments
        assert outcome.preserved_assignments_unchanged
        assert all(item.casualty_id != "c1" for item in outcome.changed_assignments)
        assert outcome.recommendation.preserved_assignments == base.active_assignments
    assert "active_route_unverified" in analysis.scenarios[0].recommendation.reasons["c1"]


def test_preference_change_reports_incomparable_policy_values():
    base = base_request("ortools", casualties=[casualty("c1", 4.5)],
                        responders=[responder("r1", 3.5), responder("r2", 0.5)],
                        previous_assignments=[Assignment(casualty_id="c1", responder_id="r2")])
    stable = PreferenceChange(kind="preference", objective_policy="stability_first")
    other_previous = PreferenceChange(kind="preference", previous_assignments=[])
    analysis = run(base, scenario("stable", stable), scenario("forget", other_previous))
    assert pairs(analysis.baseline) == [("c1", "r1")]
    first, second = analysis.scenarios
    assert pairs(first.recommendation) == [("c1", "r2")]
    assert first.objective_verdict == "not_comparable"
    assert [difference.field for difference in first.assumption_differences] == ["objective_policy"]
    deltas = {delta.name: delta for delta in first.objective_deltas}
    assert deltas["new_travel_cost_mm"].delta == 3000
    assert deltas["previous_assignment_changes"].delta == -1
    assert second.objective_verdict == "not_comparable"
    assert second.assumption_differences[0].field == "previous_assignments"
    missing = {delta.name: delta for delta in second.objective_deltas}["previous_assignment_changes"]
    assert not missing.comparable and missing.delta is None and missing.outcome == "not_comparable"
    assert pairs(second.recommendation) == pairs(analysis.baseline)


def test_changed_previous_preferences_make_stability_delta_incomparable():
    base = base_request("ortools", casualties=[casualty("c1", 4.5)],
                        responders=[responder("r1", 3.5), responder("r2", 0.5)],
                        previous_assignments=[Assignment(casualty_id="c1", responder_id="r2")])
    swap = PreferenceChange(kind="preference", previous_assignments=[
        Assignment(casualty_id="c1", responder_id="r1")])
    outcome = run(base, scenario("swap", swap)).scenarios[0]
    stability = {delta.name: delta for delta in outcome.objective_deltas}["previous_assignment_changes"]
    assert not stability.comparable and stability.outcome == "not_comparable"
    assert outcome.objective_verdict == "not_comparable"


def test_noop_scenario_is_flagged_and_unchanged():
    outcome = run(base_request(), scenario("same", ResponderAvailabilityChange(
        kind="responder_availability", responder_id="r1", status="available"))).scenarios[0]
    assert outcome.no_op and outcome.assumption_differences == []
    assert outcome.changed_assignments == []
    assert outcome.objective_verdict == "unchanged"
    assert outcome.unchanged_assignment_count == 2


def test_source_request_not_mutated_and_outputs_immutable():
    base = base_request("ortools", previous_assignments=[Assignment(casualty_id="c1",
                                                                     responder_id="r2")])
    before = base.model_dump(mode="json")
    analysis = run(base, scenario("a", offline("r1"), PreferenceChange(
        kind="preference", objective_policy="stability_first")),
        scenario("b", ObstacleAddedChange(kind="obstacle_added", obstacle=wall())))
    assert base.model_dump(mode="json") == before
    assert base.scenario.responders[0].status == "available"
    assert base.scenario.scene_map.obstacles == []
    with pytest.raises(ValidationError):
        analysis.scenarios[0].authorizes_dispatch = True
    with pytest.raises(ValidationError):
        CounterfactualAnalysis.model_validate({**analysis.model_dump(), "authorizes_dispatch": True})
    assert analysis.authorizes_dispatch is False and analysis.advisory_only is True
    assert all(item.authorizes_dispatch is False for item in analysis.scenarios)


def test_metadata_includes_revisions_and_expiry():
    base = base_request()
    live = run(base, scenario("a", offline("r1")), expires_at=NOW + timedelta(seconds=60))
    assert (live.scene_revision, live.map_revision, live.generated_at) == (1, "map-7", NOW)
    assert live.expires_at == NOW + timedelta(seconds=60) and live.expired is False
    assert live.scenarios[0].expires_at == live.expires_at and live.scenarios[0].expired is False
    assert live.scenarios[0].scene_revision == 1
    expired = run(base, scenario("a", offline("r1")), expires_at=NOW)
    assert expired.expired is True
    assert run(base, scenario("a", offline("r1"))).expired is None
    assert len(live.source_request_sha256) == 64
    assert live.source_request_sha256 == run(base, scenario("b", offline("r2"))).source_request_sha256


def test_scenarios_and_solver_calls_are_bounded():
    base = base_request()
    assert MAX_SOLVER_CALLS == MAX_SCENARIOS + 1 == 5
    many = [scenario(f"s{index}", offline("r1")) for index in range(MAX_SCENARIOS)]
    assert run(base, *many).solver_calls == MAX_SOLVER_CALLS
    with pytest.raises(ValidationError):
        run(base, *many, scenario("extra", offline("r1")))
    with pytest.raises(ValidationError):
        run(base, scenario("same", offline("r1")), scenario("same", offline("r2")))
    with pytest.raises(ValidationError):
        CounterfactualRequest(base=base, scenarios=[])
    with pytest.raises(ValidationError):
        CounterfactualScenario(id="empty", label="x", changes=[])


@pytest.mark.parametrize("changes, kwargs", [
    ([offline("missing")], {}),
    ([ObstacleRemovedChange(kind="obstacle_removed", obstacle_id="nope")], {}),
    ([ObstacleAddedChange(kind="obstacle_added", obstacle=wall("w")),
      ObstacleAddedChange(kind="obstacle_added", obstacle=wall("w"))], {}),
    ([offline("r1"), ResponderAvailabilityChange(kind="responder_availability", responder_id="r1",
                                                  status="busy")], {}),
    ([offline("r1")], {"map_revision": "map-x"}),
    ([ObstacleAddedChange(kind="obstacle_added", obstacle=wall())], {"map_revision": "map-7"}),
    ([PreferenceChange(kind="preference", previous_assignments=[
        Assignment(casualty_id="ghost", responder_id="r1")])], {}),
    ([PreferenceChange(kind="preference", objective_policy="bounded_travel_stability")], {}),
])
def test_invalid_scenarios_are_rejected(changes, kwargs):
    with pytest.raises(AnalysisInputError):
        run(base_request(), scenario("bad", *changes, **kwargs))


def test_obstacle_change_requires_map():
    with pytest.raises(AnalysisInputError):
        run(base_request(scene_map=None),
            scenario("wall", ObstacleAddedChange(kind="obstacle_added", obstacle=wall())))


def test_preference_change_requires_a_field():
    with pytest.raises(ValidationError):
        PreferenceChange(kind="preference")


def test_deterministic_repeat_and_reordered_scenario_inputs():
    base = base_request("ortools")
    first = run(base, scenario("a", offline("r1")), scenario("b", offline("r2")))
    second = run(base, scenario("a", offline("r1")), scenario("b", offline("r2")))
    summary = lambda analysis: [(outcome.scenario_id, pairs(outcome.recommendation),
                                 [delta.model_dump() for delta in outcome.objective_deltas],
                                 [change.model_dump() for change in outcome.changed_assignments])
                                for outcome in analysis.scenarios]
    assert summary(first) == summary(second)
    assert first.source_request_sha256 == second.source_request_sha256


def test_missing_ortools_fallback_is_identified(monkeypatch):
    original_import = builtins.__import__

    def without_ortools(name, *args, **kwargs):
        if name.startswith("ortools"):
            raise ImportError("test optional dependency unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_ortools)
    analysis = run(base_request("ortools"), scenario("a", offline("r1")))
    outcome = analysis.scenarios[0]
    assert analysis.baseline.engine == "fallback"
    assert outcome.requested_solver == "ortools" and outcome.engine == "fallback"
    assert outcome.fallback_used and outcome.engine_matches_base
    assert not any(tier.proven_optimal for tier in outcome.recommendation.objective_tiers)


def test_engine_change_between_runs_is_reported():
    base = base_request("ortools")
    no_map = ObstacleAddedChange(kind="obstacle_added", obstacle=wall())
    outcome = run(base, scenario("wall", no_map)).scenarios[0]
    assert outcome.engine == "ortools" and outcome.engine_matches_base
    degraded = base.model_copy(update={"scenario": base.scenario.model_copy(
        update={"scene_map": None})}, deep=True)
    assert run(degraded, scenario("noop", offline("r1"))).baseline.engine == "none"
