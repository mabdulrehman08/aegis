import pytest
from pydantic import ValidationError

from backend.app.models import Assignment, MedicalAssessment, Scenario
from optimization.models import OptimizationRequest
from optimization.solver import OBJECTIVE, optimize
from optimization.tests.conftest import NOW, casualty, responder, scene_map

ENGINES = ["nearest", "fallback", "ortools"]


def build(casualties, responders, **kwargs):
    scene = Scenario(schema_version="1.0", id="scene", name="Synthetic", synthetic=True,
                     frame_id="world", created_at=NOW, casualties=casualties,
                     responders=responders, scene_map=kwargs.pop("scene_map", scene_map()))
    return OptimizationRequest(scenario=scene, now=NOW, **{"solver": "ortools", **kwargs})


def previous(*pairs):
    return [Assignment(casualty_id=c, responder_id=r) for c, r in pairs]


def pairs(result):
    return [(item.casualty_id, item.responder_id) for item in result.assignments]


def stability_case(**kwargs):
    return build([casualty("c1", 4.5)], [responder("r1", 3.5), responder("r2", 0.5)],
                 previous_assignments=previous(("c1", "r2")), **kwargs)


def assessed(severity):
    return MedicalAssessment(severity=severity, source="human", assessed_by="human-operator",
                             assessed_at=NOW)


@pytest.mark.parametrize("engine", ENGINES)
def test_default_without_previous_is_legacy_identical(engine):
    request = build([casualty("c1"), casualty("c2", 5.5)], [responder("r1"), responder("r2", 1.5)],
                    solver=engine)
    legacy = optimize(request)
    assert legacy.metadata.objective == OBJECTIVE
    assert [tier.name for tier in legacy.objective_tiers] == [
        "new_assigned_count", "allocation_priority_points", "new_travel_cost_mm"]
    for policy, extra in (("stability_first", {}),
                          ("bounded_travel_stability", {"stability_travel_budget_m": 5})):
        other = optimize(request.model_copy(update={"objective_policy": policy, **extra}))
        assert pairs(other) == pairs(legacy)
        assert other.metadata.objective == OBJECTIVE


def test_distance_first_default_trades_stability_for_distance():
    result = optimize(stability_case())
    assert pairs(result) == [("c1", "r1")]
    assert result.metadata.objective != OBJECTIVE
    names = [tier.name for tier in result.objective_tiers]
    assert names == ["new_assigned_count", "allocation_priority_points", "new_travel_cost_mm",
                     "previous_assignment_changes"]
    assert result.objective_tiers[-1].value == 1


def test_stability_first_keeps_previous_over_shorter_travel():
    result = optimize(stability_case(objective_policy="stability_first"))
    assert pairs(result) == [("c1", "r2")]
    assert [tier.name for tier in result.objective_tiers][2:] == [
        "previous_assignment_changes", "new_travel_cost_mm"]
    assert [tier.value for tier in result.objective_tiers] == [1, 2, 0, 4000]
    assert all(tier.proven_optimal for tier in result.objective_tiers)
    assert result.previous_assignment_outcomes[0].outcome == "retained"


def test_distance_first_stability_breaks_exact_travel_ties():
    request = build([casualty("c1", 4.5)], [responder("r1", 3.5), responder("r2", 5.5)],
                    previous_assignments=previous(("c1", "r2")))
    assert pairs(optimize(request)) == [("c1", "r2")]
    assert pairs(optimize(request.model_copy(update={"previous_assignments": []}))) == [("c1", "r1")]


@pytest.mark.parametrize("budget, expected", [(2.9, "r1"), (3.0, "r2"), (10, "r2")])
def test_bounded_budget_limits_extra_travel(budget, expected):
    result = optimize(stability_case(objective_policy="bounded_travel_stability",
                                     stability_travel_budget_m=budget))
    assert pairs(result) == [("c1", expected)]
    assert "bounded" in result.metadata.objective


@pytest.mark.parametrize("engine", ["fallback"])
def test_fallback_orders_by_policy_without_optimality_claim(engine):
    result = optimize(stability_case(solver=engine, objective_policy="stability_first"))
    assert pairs(result) == [("c1", "r2")]
    assert not any(tier.proven_optimal for tier in result.objective_tiers)
    assert any("does not prove minimum changes" in text for text in result.rationale)
    bounded = optimize(stability_case(solver=engine, objective_policy="bounded_travel_stability",
                                      stability_travel_budget_m=0))
    assert any("budget is not enforced" in text for text in bounded.rationale)


def test_nearest_ignores_previous_and_says_so():
    result = optimize(stability_case(solver="nearest", objective_policy="stability_first"))
    assert pairs(result) == [("c1", "r1")]
    assert any("nearest baseline ignores previous" in text for text in result.rationale)
    assert result.previous_assignment_outcomes[0].outcome == "changed_responder"


@pytest.mark.parametrize("engine", ["fallback", "ortools"])
def test_stability_never_reduces_count(engine):
    request = build([casualty("c1", 4.5), casualty("c2", 5.5)],
                    [responder("r1", 3.5), responder("r2", 0.5, capabilities=["aed"])],
                    solver=engine, objective_policy="stability_first",
                    required_capabilities={"c2": ["aed"]},
                    previous_assignments=previous(("c1", "r2")))
    result = optimize(request)
    assert pairs(result) == [("c1", "r1"), ("c2", "r2")]
    outcomes = result.previous_assignment_outcomes
    assert [outcome.outcome for outcome in outcomes] == ["changed_responder"]


@pytest.mark.parametrize("engine", ["fallback", "ortools"])
def test_stability_never_displaces_higher_priority(engine):
    request = build([casualty("minor", 4.5, assessment=assessed("minor")),
                     casualty("urgent", 5.5, assessment=assessed("immediate"))],
                    [responder("r1")], solver=engine, objective_policy="stability_first",
                    previous_assignments=previous(("minor", "r1")))
    result = optimize(request)
    assert pairs(result) == [("urgent", "r1")]
    assert result.previous_assignment_outcomes[0].outcome == "not_reassigned"
    assert result.objective_values["allocation_priority_points"] == 2


@pytest.mark.parametrize("engine", ENGINES)
def test_active_work_is_hard_reserved_not_soft_stability(engine):
    active = Assignment(casualty_id="c1", responder_id="r1")
    request = build([casualty("c1"), casualty("c2", 5.5), casualty("c3", 6.5)],
                    [responder("r1"), responder("r2", 1.5), responder("r3", 2.5)], solver=engine,
                    active_assignments=[active], objective_policy="stability_first",
                    previous_assignments=previous(("c1", "r2"), ("c2", "r1"), ("c3", "r3")))
    before = request.model_dump(mode="json")
    result = optimize(request)
    assert result.preserved_assignments == [active]
    assert all(item.casualty_id != "c1" and item.responder_id != "r1" for item in result.assignments)
    outcomes = {(item.casualty_id, item.responder_id): item for item in result.previous_assignment_outcomes}
    assert outcomes["c1", "r2"].outcome == "blocked_active_casualty"
    assert outcomes["c2", "r1"].outcome == "blocked_active_responder"
    assert outcomes["c2", "r1"].reasons == ["responder_reserved_active"]
    assert request.model_dump(mode="json") == before


def test_previous_outcomes_report_pair_specific_blockers():
    request = build([casualty("c1"), casualty("c2", 5.5), casualty("c3", 6.5)],
                    [responder("r1", status="offline"), responder("r2", 1.5),
                     responder("r3", 2.5)],
                    required_capabilities={"c2": ["aed"]},
                    previous_assignments=previous(("c1", "r1"), ("c2", "r2"), ("c3", "r3")))
    outcomes = {item.casualty_id: item for item in optimize(request).previous_assignment_outcomes}
    assert outcomes["c1"].outcome == "blocked_infeasible"
    assert outcomes["c1"].reasons == ["responder_unavailable"]
    assert outcomes["c2"].reasons == ["required_capabilities_missing"]
    assert outcomes["c3"].outcome == "retained"


def test_previous_unreachable_pair_is_infeasible():
    blocked = scene_map(cells=[0, 0, -1, 0, 0, 0, 0, 0] * 6)
    request = build([casualty("c1")], [responder("r1", 1.5)], scene_map=blocked,
                    previous_assignments=previous(("c1", "r1")))
    outcome = optimize(request).previous_assignment_outcomes[0]
    assert outcome.outcome == "blocked_infeasible"
    assert outcome.reasons == ["unreachable"]


@pytest.mark.parametrize("engine", ["fallback", "ortools"])
def test_deterministic_and_input_order_independent(engine):
    base = dict(solver=engine, objective_policy="stability_first")
    casualties = [casualty("c1", 4.5), casualty("c2", 5.5)]
    responders = [responder("r1", 3.5), responder("r2", 3.5)]
    first = build(casualties, responders, previous_assignments=previous(("c1", "r2"), ("c2", "r1")),
                  **base)
    second = build(casualties, responders, previous_assignments=previous(("c2", "r1"), ("c1", "r2")),
                   **base)
    one, two = optimize(first), optimize(second)
    assert pairs(one) == pairs(two) == [("c1", "r2"), ("c2", "r1")]
    assert pairs(optimize(first)) == pairs(one)
    assert one.previous_assignment_outcomes == two.previous_assignment_outcomes


def test_no_mutation_of_stability_inputs():
    request = stability_case(objective_policy="stability_first")
    before = request.model_dump(mode="json")
    optimize(request)
    assert request.model_dump(mode="json") == before


@pytest.mark.parametrize("kwargs", [
    {"previous_assignments": previous(("missing", "r1"))},
    {"previous_assignments": previous(("c1", "missing"))},
    {"previous_assignments": previous(("c1", "r1"), ("c1", "r2"))},
    {"previous_assignments": previous(("c1", "r1"), ("c2", "r1"))},
])
def test_invalid_previous_assignments_raise(kwargs):
    request = build([casualty("c1"), casualty("c2", 5.5)], [responder("r1"), responder("r2")], **kwargs)
    with pytest.raises(ValueError):
        optimize(request)


@pytest.mark.parametrize("kwargs", [
    {"objective_policy": "bounded_travel_stability"},
    {"objective_policy": "stability_first", "stability_travel_budget_m": 1},
    {"objective_policy": "bounded_travel_stability", "stability_travel_budget_m": -1},
    {"objective_policy": "unknown"},
])
def test_invalid_policy_and_budget_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        build([casualty("c1")], [responder("r1")], **kwargs)


@pytest.mark.parametrize("engine", ENGINES)
def test_exclusions_block_new_work_only_and_explain(engine):
    request = build([casualty("c1"), casualty("c2", 5.5), casualty("c3", 6.5)],
                    [responder("r1"), responder("r2", 1.5)], solver=engine,
                    active_assignments=[Assignment(casualty_id="c1", responder_id="r1")],
                    assignment_exclusions={"c1": ["temporal_gate:stale"],
                                           "c2": ["temporal_gate:b", "temporal_gate:a", "temporal_gate:a"]})
    before = request.model_dump(mode="json")
    result = optimize(request)
    assert pairs(result) == [("c3", "r2")]
    assert result.preserved_assignments == [Assignment(casualty_id="c1", responder_id="r1")]
    assert "active_assignment_preserved" in result.reasons["c1"]
    assert not any(reason.startswith("temporal_gate") for reason in result.reasons["c1"])
    assert result.reasons["c2"] == ["temporal_gate:a", "temporal_gate:b"]
    assert result.unassigned_casualty_ids == ["c2"]
    assert result.applied_assignment_exclusions == {"c2": ["temporal_gate:a", "temporal_gate:b"]}
    text = " ".join(result.rationale)
    assert "caller policy, not CV inference" in text
    assert "Exclusions for active casualties c1 were ignored" in text
    assert request.model_dump(mode="json") == before


def test_exclusions_without_map_and_default_is_empty():
    request = build([casualty("c1")], [responder("r1")], scene_map=None,
                    assignment_exclusions={"c1": ["temporal_gate:x"]})
    result = optimize(request)
    assert result.reasons["c1"] == ["map_unavailable", "temporal_gate:x"]
    assert optimize(build([casualty("c1")], [responder("r1")])).applied_assignment_exclusions == {}


@pytest.mark.parametrize("exclusions", [
    {"missing": ["gate"]}, {"c1": []}, {"c1": [" "]}, {"c1": [""]},
    {"c1": ["assigned_feasible_route"]}, {"c1": ["observation_stale"]},
    {"c1": ["track_lost"]}, {"c1": ["active_route_unverified"]}, {"c1": ["unreachable"]},
])
def test_invalid_exclusions_raise(exclusions):
    with pytest.raises(ValueError):
        optimize(build([casualty("c1")], [responder("r1")], assignment_exclusions=exclusions))
