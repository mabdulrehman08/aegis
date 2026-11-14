"""Deterministic, read-only counterfactual analysis built on the real solver.

Every scenario applies explicit, validated changes to a deep copy of the source
request and reruns ``optimization.solver.optimize``. Results are measured solver
outputs, never generated text. The source request is never mutated, the number of
scenarios and therefore solver calls is bounded, and nothing returned here is an
approval, dispatch authorization or medical inference.
"""

import hashlib
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator,
)

from backend.app.models import Assignment, Identifier, Obstacle
from optimization.models import ObjectivePolicy, ObjectiveTier, OptimizationRequest, OptimizationResult
from optimization.solver import optimize

MAX_SCENARIOS = 4
MAX_CHANGES_PER_SCENARIO = 8
MAX_SOLVER_CALLS = MAX_SCENARIOS + 1


class AnalysisInputError(ValueError):
    """A counterfactual scenario is invalid for the supplied base request."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResponderAvailabilityChange(_Frozen):
    kind: Literal["responder_availability"]
    responder_id: Identifier
    status: Literal["available", "busy", "offline"]


class ResponderCapabilityChange(_Frozen):
    """Replace the responder's whole capability tag set."""

    kind: Literal["responder_capabilities"]
    responder_id: Identifier
    capabilities: list[Identifier]


class ObstacleAddedChange(_Frozen):
    kind: Literal["obstacle_added"]
    obstacle: Obstacle


class ObstacleRemovedChange(_Frozen):
    kind: Literal["obstacle_removed"]
    obstacle_id: Identifier


class PreferenceChange(_Frozen):
    """Replace soft preferences. Omitted fields keep the base values.

    Supplying ``objective_policy`` replaces the policy and its budget together.
    """

    kind: Literal["preference"]
    previous_assignments: list[Assignment] | None = None
    objective_policy: ObjectivePolicy | None = None
    stability_travel_budget_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _something_changes(self) -> "PreferenceChange":
        if (self.previous_assignments is None and self.objective_policy is None
                and self.stability_travel_budget_m is None):
            raise ValueError("Preference change must set at least one preference field")
        return self


CounterfactualChange = Annotated[
    ResponderAvailabilityChange | ResponderCapabilityChange | ObstacleAddedChange
    | ObstacleRemovedChange | PreferenceChange,
    Field(discriminator="kind"),
]


class CounterfactualScenario(_Frozen):
    id: Identifier
    label: Annotated[str, Field(min_length=1, max_length=200)]
    changes: Annotated[list[CounterfactualChange], Field(min_length=1, max_length=MAX_CHANGES_PER_SCENARIO)]
    map_revision: Identifier | None = None


class CounterfactualRequest(_Frozen):
    """``expires_at`` is the source recommendation/proposal expiry, copied through."""

    base: OptimizationRequest
    scenarios: Annotated[list[CounterfactualScenario], Field(min_length=1, max_length=MAX_SCENARIOS)]
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _unique_scenario_ids(self) -> "CounterfactualRequest":
        ids = [scenario.id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("Counterfactual scenario IDs must be unique")
        return self


class AssumptionDifference(_Frozen):
    """One exact input difference between the base and the counterfactual request."""

    field: str
    entity_id: str | None
    before: JsonValue
    after: JsonValue


class AssignmentChange(_Frozen):
    """Change in NEW proposed work only; preserved active work is never listed here."""

    casualty_id: str
    kind: Literal["added", "removed", "responder_changed"]
    base_responder_id: str | None
    scenario_responder_id: str | None
    base_distance_m: float | None
    scenario_distance_m: float | None


class ReasonChange(_Frozen):
    casualty_id: str
    base_reasons: list[str]
    scenario_reasons: list[str]


class ObjectiveDelta(_Frozen):
    """``delta`` is scenario minus base, populated only for comparable tiers."""

    name: str
    direction: Literal["maximize", "minimize"]
    base_value: float | None
    scenario_value: float | None
    delta: float | None
    comparable: bool
    outcome: Literal["better", "worse", "same", "not_comparable"]
    note: str | None = None


class CounterfactualOutcome(_Frozen):
    scenario_id: str
    label: str
    hypothetical: Literal[True] = True
    assumption_differences: list[AssumptionDifference]
    no_op: bool
    recommendation: OptimizationResult
    changed_assignments: list[AssignmentChange]
    unchanged_assignment_count: int
    preserved_assignments: list[Assignment]
    preserved_assignments_unchanged: bool
    newly_assigned_casualty_ids: list[str]
    newly_unassigned_casualty_ids: list[str]
    reason_changes: list[ReasonChange]
    objective_deltas: list[ObjectiveDelta]
    objective_verdict: Literal["improved", "degraded", "unchanged", "not_comparable"]
    verdict_note: str
    requested_solver: str
    engine: str
    base_engine: str
    fallback_used: bool
    engine_matches_base: bool
    scene_revision: int
    map_revision: str | None
    expires_at: AwareDatetime | None
    expired: bool | None
    authorizes_dispatch: Literal[False] = False


class CounterfactualAnalysis(_Frozen):
    scenario_id: str
    scene_revision: int
    map_revision: str | None
    generated_at: AwareDatetime
    expires_at: AwareDatetime | None
    expired: bool | None
    source_request_sha256: str
    baseline: OptimizationResult
    scenarios: list[CounterfactualOutcome]
    solver_calls: int
    max_solver_calls: int
    advisory_only: Literal[True] = True
    authorizes_dispatch: Literal[False] = False
    limitations: list[str]


LIMITATIONS = (
    "Counterfactuals are hypothetical reruns of the real solver on explicitly changed copies of the "
    "source request; they do not predict the world, medical outcomes or responder behavior.",
    "Approved or acknowledged active work is hard reserved and identical in every scenario; "
    "route staleness against a hypothetical map is reported, never silently rewritten.",
    "Objective deltas compare measured tier values; a verdict is given only when both runs use the "
    "same objective policy, tier set and previous-assignment preferences.",
    "Results carry no approval, dispatch authorization or clinical assessment and require human review.",
    "Fallback or nearest engines make no optimality claim; engine and fallback use are reported per run.",
)


def _fingerprint(request: OptimizationRequest) -> str:
    return hashlib.sha256(request.model_dump_json().encode()).hexdigest()


def _pairs(assignments: list[Assignment]) -> list[list[str]]:
    return sorted([assignment.casualty_id, assignment.responder_id] for assignment in assignments)


def _apply(base: OptimizationRequest, scenario: CounterfactualScenario) -> OptimizationRequest:
    variant = base.model_copy(deep=True)
    scene = variant.scenario
    responders = {responder.id: responder for responder in scene.responders}
    seen: set[tuple[str, str]] = set()
    map_changed = False
    for change in scenario.changes:
        if isinstance(change, ObstacleAddedChange):
            target = change.obstacle.id
        else:
            target = getattr(change, "responder_id", None) or getattr(change, "obstacle_id", "")
        key = (change.kind, target)
        if key in seen:
            raise AnalysisInputError(f"Scenario {scenario.id} has conflicting duplicate changes")
        seen.add(key)
        if isinstance(change, (ResponderAvailabilityChange, ResponderCapabilityChange)):
            responder = responders.get(change.responder_id)
            if responder is None:
                raise AnalysisInputError(f"Unknown responder {change.responder_id}")
            if isinstance(change, ResponderAvailabilityChange):
                responder.status = change.status
            else:
                responder.capabilities = list(change.capabilities)
        elif isinstance(change, (ObstacleAddedChange, ObstacleRemovedChange)):
            scene_map = scene.scene_map
            if scene_map is None or scene_map.occupancy_grid is None:
                raise AnalysisInputError("Map obstacle changes require a base map with an occupancy grid")
            map_changed = True
            if isinstance(change, ObstacleAddedChange):
                if any(obstacle.id == change.obstacle.id for obstacle in scene_map.obstacles):
                    raise AnalysisInputError(f"Obstacle {change.obstacle.id} already exists")
                if any(point.frame_id != scene_map.frame_id for point in change.obstacle.vertices):
                    raise AnalysisInputError("Obstacle vertices must use the map frame")
                scene_map.obstacles.append(change.obstacle.model_copy(deep=True))
            else:
                remaining = [obstacle for obstacle in scene_map.obstacles
                             if obstacle.id != change.obstacle_id]
                if len(remaining) == len(scene_map.obstacles):
                    raise AnalysisInputError(f"Unknown obstacle {change.obstacle_id}")
                scene_map.obstacles = remaining
        else:
            if change.previous_assignments is not None:
                variant.previous_assignments = [item.model_copy(deep=True)
                                                for item in change.previous_assignments]
            if change.objective_policy is not None:
                variant.objective_policy = change.objective_policy
                variant.stability_travel_budget_m = change.stability_travel_budget_m
            elif change.stability_travel_budget_m is not None:
                variant.stability_travel_budget_m = change.stability_travel_budget_m
    if scene.scene_map is not None:
        if map_changed:
            revision = scenario.map_revision or f"{scene.scene_map.revision}+cf-{scenario.id}"
            if revision == base.scenario.scene_map.revision:
                raise AnalysisInputError("Counterfactual map revision must differ from the base revision")
            scene.scene_map.revision = revision
        elif scenario.map_revision is not None:
            raise AnalysisInputError("map_revision may only be set when the scenario changes the map")
    elif scenario.map_revision is not None:
        raise AnalysisInputError("map_revision may only be set when the scenario changes the map")
    try:
        return OptimizationRequest.model_validate(variant.model_dump())
    except ValidationError as error:
        raise AnalysisInputError(f"Scenario {scenario.id} is invalid: {error.errors()[0]['msg']}") from error


def _differences(base: OptimizationRequest, variant: OptimizationRequest) -> list[AssumptionDifference]:
    found: list[AssumptionDifference] = []

    def note(field: str, entity_id: str | None, before: JsonValue, after: JsonValue) -> None:
        if before != after:
            found.append(AssumptionDifference(field=field, entity_id=entity_id, before=before, after=after))

    variants = {responder.id: responder for responder in variant.scenario.responders}
    for responder in sorted(base.scenario.responders, key=lambda item: item.id):
        other = variants[responder.id]
        note("responder.status", responder.id, responder.status, other.status)
        note("responder.capabilities", responder.id, sorted(responder.capabilities),
             sorted(other.capabilities))
    base_map, variant_map = base.scenario.scene_map, variant.scenario.scene_map
    if base_map is not None and variant_map is not None:
        before = {obstacle.id: obstacle for obstacle in base_map.obstacles}
        after = {obstacle.id: obstacle for obstacle in variant_map.obstacles}
        for identifier in sorted(set(before) | set(after)):
            note("map.obstacle", identifier,
                 before[identifier].model_dump(mode="json") if identifier in before else None,
                 after[identifier].model_dump(mode="json") if identifier in after else None)
        note("map.revision", None, base_map.revision, variant_map.revision)
    note("previous_assignments", None, _pairs(base.previous_assignments),
         _pairs(variant.previous_assignments))
    note("objective_policy", None, base.objective_policy, variant.objective_policy)
    note("stability_travel_budget_m", None, base.stability_travel_budget_m,
         variant.stability_travel_budget_m)
    return found


def _assignment_changes(base: OptimizationResult,
                        other: OptimizationResult) -> tuple[list[AssignmentChange], int]:
    before = {item.casualty_id: item for item in base.assignments}
    after = {item.casualty_id: item for item in other.assignments}
    changes, unchanged = [], 0
    for casualty_id in sorted(set(before) | set(after)):
        first, second = before.get(casualty_id), after.get(casualty_id)
        if first is not None and second is not None and first.responder_id == second.responder_id:
            unchanged += 1
            continue
        kind = ("added" if first is None else "removed" if second is None else "responder_changed")
        changes.append(AssignmentChange(
            casualty_id=casualty_id, kind=kind,
            base_responder_id=first.responder_id if first else None,
            scenario_responder_id=second.responder_id if second else None,
            base_distance_m=first.route.distance_m if first and first.route else None,
            scenario_distance_m=second.route.distance_m if second and second.route else None))
    return changes, unchanged


def _deltas(base: OptimizationRequest, base_result: OptimizationResult, variant: OptimizationRequest,
            result: OptimizationResult) -> tuple[list[ObjectiveDelta], str, str]:
    same_previous = _pairs(base.previous_assignments) == _pairs(variant.previous_assignments)
    same_policy = (base_result.objective_policy == result.objective_policy
                   and base.stability_travel_budget_m == variant.stability_travel_budget_m)
    before = {tier.name: tier for tier in base_result.objective_tiers}
    after = {tier.name: tier for tier in result.objective_tiers}
    order = [tier.name for tier in base_result.objective_tiers]
    order += [name for name in (tier.name for tier in result.objective_tiers) if name not in before]
    deltas = []
    for name in order:
        first, second = before.get(name), after.get(name)
        tier = first or second
        note = None
        comparable = first is not None and second is not None
        if not comparable:
            note = "Tier exists in only one run because its policy inputs differ."
        elif name == "previous_assignment_changes" and not same_previous:
            comparable, note = False, "Measured against different previous-assignment preferences."
        elif base_result.engine != result.engine:
            note = "Engines differ; values are measured, optimality claims are not equal."
        if comparable:
            delta = second.value - first.value
            better = delta > 0 if tier.direction == "maximize" else delta < 0
            outcome = "same" if delta == 0 else "better" if better else "worse"
        else:
            delta, outcome = None, "not_comparable"
        deltas.append(ObjectiveDelta(
            name=name, direction=tier.direction, base_value=first.value if first else None,
            scenario_value=second.value if second else None, delta=delta, comparable=comparable,
            outcome=outcome, note=note))
    if not same_policy or not same_previous or [tier.name for tier in result.objective_tiers] != order:
        return deltas, "not_comparable", (
            "Objective policy, budget, previous-assignment preferences or tier set differ, so a "
            "lexicographic verdict is not meaningful; per-tier measurements are listed separately.")
    for delta in deltas:
        if delta.outcome != "same":
            verdict = "improved" if delta.outcome == "better" else "degraded"
            return deltas, verdict, f"First differing tier in policy order: {delta.name}."
    return deltas, "unchanged", "All objective tiers are equal."


def analyze_counterfactuals(request: CounterfactualRequest) -> CounterfactualAnalysis:
    """Rerun the real solver for the baseline and each explicit scenario.

    Performs at most ``MAX_SOLVER_CALLS`` solves. Raises ``AnalysisInputError`` for
    scenarios that do not validate against the base request. The source request is
    read-only: all changes apply to deep copies, verified unchanged afterwards.
    """
    source = request.base
    fingerprint = _fingerprint(source)
    baseline_request = source.model_copy(deep=True)
    variants = [(scenario, _apply(source, scenario)) for scenario in request.scenarios]
    baseline = optimize(baseline_request)
    expired = None if request.expires_at is None else source.now >= request.expires_at
    outcomes = []
    for scenario, variant in variants:
        try:
            result = optimize(variant)
        except ValueError as error:
            raise AnalysisInputError(f"Scenario {scenario.id} is invalid: {error}") from error
        changed, unchanged = _assignment_changes(baseline, result)
        deltas, verdict, verdict_note = _deltas(source, baseline, variant, result)
        differences = _differences(source, variant)
        before_reasons, after_reasons = baseline.reasons, result.reasons
        outcomes.append(CounterfactualOutcome(
            scenario_id=scenario.id, label=scenario.label, assumption_differences=differences,
            no_op=not differences, recommendation=result, changed_assignments=changed,
            unchanged_assignment_count=unchanged, preserved_assignments=result.preserved_assignments,
            preserved_assignments_unchanged=result.preserved_assignments == baseline.preserved_assignments,
            newly_assigned_casualty_ids=[item.casualty_id for item in changed
                                         if item.kind == "added"],
            newly_unassigned_casualty_ids=[item.casualty_id for item in changed
                                           if item.kind == "removed"],
            reason_changes=[ReasonChange(casualty_id=identifier,
                                         base_reasons=before_reasons.get(identifier, []),
                                         scenario_reasons=after_reasons.get(identifier, []))
                            for identifier in sorted(set(before_reasons) | set(after_reasons))
                            if before_reasons.get(identifier) != after_reasons.get(identifier)],
            objective_deltas=deltas, objective_verdict=verdict, verdict_note=verdict_note,
            requested_solver=variant.solver, engine=result.engine, base_engine=baseline.engine,
            fallback_used=result.engine == "fallback" and variant.solver != "fallback",
            engine_matches_base=result.engine == baseline.engine,
            scene_revision=result.scene_revision, map_revision=result.map_revision,
            expires_at=request.expires_at, expired=expired))
    if _fingerprint(source) != fingerprint:
        raise RuntimeError("Counterfactual analysis mutated the source request")
    return CounterfactualAnalysis(
        scenario_id=source.scenario.id, scene_revision=baseline.scene_revision,
        map_revision=baseline.map_revision, generated_at=source.now, expires_at=request.expires_at,
        expired=expired, source_request_sha256=fingerprint, baseline=baseline, scenarios=outcomes,
        solver_calls=1 + len(variants), max_solver_calls=MAX_SOLVER_CALLS, limitations=list(LIMITATIONS))
