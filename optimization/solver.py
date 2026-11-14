"""Pure assignment recommendations with one shared feasibility graph.

CP-SAT stages: maximum new assignment count, maximum allocation priority sum,
minimum rounded millimeter travel, then lexicographic casualty/responder IDs.
The fallback maximizes count with deterministic priority-ordered augmenting
paths; it does not claim minimum total travel. Nearest is a greedy baseline.
"""

from collections import deque
import math
from time import perf_counter

from backend.app.models import AStarRoute, Assignment, Casualty, RecommendationMetadata
from optimization.models import OptimizationRequest, OptimizationResult
from optimization.routing import _GridRouter

OBJECTIVE = "count_then_protected_human_urgency_then_travel_then_ids_v1"
CP_STAGE_SECONDS = 1.0
CP_TOTAL_SECONDS = 5.0
POLICY = (
    "Maximize feasible new assignments before allocation priority, then minimize "
    "rounded millimeter travel and break ties by sorted casualty/responder IDs. "
    "Human-confirmed immediate=2, delayed=1, minor=0 allocation points. "
    "Unassessed, future-assessed, expectant and deceased receive protected 2-point "
    "allocation parity with immediate, NOT an inferred medical severity. "
    "There is no automatic clinical expectant/deceased deprioritization. "
    "These recommendations require human review and do not authorize dispatch."
)


class _SolverNotOptimal(RuntimeError):
    """A bounded solve did not prove a stage optimum; use deterministic fallback."""


def _priority(casualty: Casualty, request: OptimizationRequest) -> int:
    assessment = casualty.medical_assessment
    if assessment is None or assessment.assessed_at > request.now:
        return 2
    return {"immediate": 2, "delayed": 1, "minor": 0,
            "expectant": 2, "deceased": 2}[assessment.severity]


def _observation_reasons(casualty: Casualty, request: OptimizationRequest) -> list[str]:
    observation = request.observations.get(casualty.id)
    if observation is None:
        return []
    reasons = []
    age = (request.now - observation.observed_at).total_seconds()
    if age < 0:
        reasons.append("observation_future_dated")
    elif age > request.freshness_seconds:
        reasons.append("observation_stale")
    if observation.confidence < request.min_confidence:
        reasons.append("observation_low_confidence")
    if (observation.position.frame_id != casualty.position.frame_id
            or math.hypot(observation.position.x_m - casualty.position.x_m,
                          observation.position.y_m - casualty.position.y_m)
            > request.position_tolerance_m):
        reasons.append("observation_position_mismatch")
    if observation.track is not None:
        track = observation.track
        if track.state != "visible":
            reasons.append(f"track_{track.state}")
        seen_age = (request.now - track.last_seen_at).total_seconds()
        if seen_age < 0:
            reasons.append("track_future_dated")
        elif seen_age > request.freshness_seconds:
            reasons.append("track_stale")
    return reasons


def _validate_context(request: OptimizationRequest) -> None:
    casualties = {casualty.id for casualty in request.scenario.casualties}
    responders = {responder.id for responder in request.scenario.responders}
    for mapping in (request.required_capabilities, request.observations):
        if set(mapping) - casualties:
            raise ValueError("Casualty-keyed inputs must reference scenario casualty IDs")
    for tags in request.required_capabilities.values():
        if any(not tag or not tag.strip() for tag in tags):
            raise ValueError("Required capability tags must be nonempty")
    active_casualties: set[str] = set()
    active_responders: set[str] = set()
    for assignment in request.active_assignments:
        if assignment.casualty_id not in casualties or assignment.responder_id not in responders:
            raise ValueError("Active assignments must reference existing scenario entities")
        if (assignment.casualty_id in active_casualties
                or assignment.responder_id in active_responders):
            raise ValueError("Active assignments must be one-to-one and unique")
        active_casualties.add(assignment.casualty_id)
        active_responders.add(assignment.responder_id)


def _greedy(routes: dict[tuple[str, str], AStarRoute], priorities: dict[str, int],
            nearest: bool) -> list[tuple[str, str]]:
    if nearest:
        selected = []
        casualties: set[str] = set()
        responders: set[str] = set()
        for pair in sorted(routes, key=lambda pair: (routes[pair].distance_m, *pair)):
            if pair[0] not in casualties and pair[1] not in responders:
                selected.append(pair)
                casualties.add(pair[0])
                responders.add(pair[1])
        return sorted(selected)
    candidates: dict[str, list[str]] = {}
    for casualty_id, responder_id in sorted(routes, key=lambda pair: (routes[pair].distance_m, *pair)):
        candidates.setdefault(casualty_id, []).append(responder_id)
    assigned: dict[str, str] = {}
    owner: dict[str, str] = {}
    for casualty_id in sorted(candidates, key=lambda identifier: (-priorities[identifier], identifier)):
        queue = deque([casualty_id])
        seen = {casualty_id}
        parents: dict[str, str] = {}
        free_responder = None
        while queue and free_responder is None:
            current = queue.popleft()
            for responder_id in candidates[current]:
                if responder_id in parents:
                    continue
                parents[responder_id] = current
                if responder_id not in owner:
                    free_responder = responder_id
                    break
                previous = owner[responder_id]
                if previous not in seen:
                    seen.add(previous)
                    queue.append(previous)
        while free_responder is not None:
            current = parents[free_responder]
            previous_responder = assigned.get(current)
            assigned[current] = free_responder
            owner[free_responder] = current
            free_responder = previous_responder
    return sorted(assigned.items())


def _ortools(routes: dict[tuple[str, str], AStarRoute],
             priorities: dict[str, int]) -> list[tuple[str, str]]:
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    variables = {pair: model.NewBoolVar(f"edge_{index}")
                 for index, pair in enumerate(sorted(routes))}
    casualty_ids = sorted({pair[0] for pair in variables})
    responder_ids = sorted({pair[1] for pair in variables})
    for casualty_id in casualty_ids:
        model.Add(sum(variable for pair, variable in variables.items() if pair[0] == casualty_id) <= 1)
    for responder_id in responder_ids:
        model.Add(sum(variable for pair, variable in variables.items() if pair[1] == responder_id) <= 1)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    deadline = perf_counter() + CP_TOTAL_SECONDS

    def stage(expression, maximize: bool) -> None:
        remaining = deadline - perf_counter()
        if remaining <= 0:
            raise _SolverNotOptimal("CP-SAT total solve budget exhausted")
        solver.parameters.max_time_in_seconds = min(CP_STAGE_SECONDS, remaining)
        if maximize:
            model.Maximize(expression)
        else:
            model.Minimize(expression)
        status = solver.Solve(model)
        if status != cp_model.OPTIMAL:
            raise _SolverNotOptimal("CP-SAT did not prove a stage optimal within its bounded budget")
        model.Add(expression == solver.Value(expression))

    stage(sum(variables.values()), True)
    stage(sum(priorities[pair[0]] * variable for pair, variable in variables.items()), True)
    if any(not math.isfinite(route.distance_m * 1000) for route in routes.values()):
        raise ValueError("Travel costs exceed finite millimeter arithmetic bounds")
    costs = {pair: round(route.distance_m * 1000) for pair, route in routes.items()}
    if sum(costs.values()) >= 2 ** 60:
        raise ValueError("Travel costs exceed safe CP-SAT integer bounds")
    stage(sum(costs[pair] * variable for pair, variable in variables.items()), False)
    ranks = {responder_id: index for index, responder_id in enumerate(responder_ids)}
    for casualty_id in casualty_ids:
        expression = sum((len(responder_ids) - ranks[pair[1]]) * variable
                         for pair, variable in variables.items() if pair[0] == casualty_id)
        stage(expression, True)
    return [pair for pair, variable in variables.items() if solver.Value(variable)]


def optimize(request: OptimizationRequest) -> OptimizationResult:
    """Return a recommendation without mutating inputs or any external state.

    Existing active work is copied unchanged into preserved_assignments and its
    entities reserved, even if now busy, uncertain, or on an older map. It is not
    a new proposal or newly validated dispatch. Missing maps block only new work.
    Invalid/conflicting active identifiers raise ValueError rather than dropping
    or silently reassigning existing work. Runtime is diagnostic only.
    """
    started = perf_counter()
    _validate_context(request)
    scene = request.scenario
    scene_map = scene.scene_map
    reasons = {casualty.id: [] for casualty in sorted(scene.casualties, key=lambda item: item.id)}
    rationale = [POLICY, "Only available, unreserved, capability-compatible responders with verified "
                 "grid routes enter the shared candidate graph. Missing observations do not infer "
                 "uncertainty; supplied uncertain observations block new work, never existing work."]
    engine = request.solver
    assignments = []
    preserved = [assignment.model_copy(deep=True) for assignment in request.active_assignments]
    preserved.sort(key=lambda assignment: (assignment.casualty_id, assignment.responder_id))
    active_casualties = {assignment.casualty_id for assignment in preserved}
    active_responders = {assignment.responder_id for assignment in preserved}
    casualty_positions = {casualty.id: casualty.position for casualty in scene.casualties}
    for casualty in scene.casualties:
        if casualty.id in active_casualties:
            reasons[casualty.id] = ["active_assignment_preserved", *_observation_reasons(casualty, request)]
    for assignment in preserved:
        if assignment.route is None or scene_map is None or scene_map.occupancy_grid is None:
            reasons[assignment.casualty_id].append("active_route_unverified")
        if (assignment.route is not None and scene_map is not None
                and assignment.route.map_revision != scene_map.revision):
            reasons[assignment.casualty_id].append("active_route_stale_map")
        if (assignment.route is not None
                and assignment.route.waypoints[-1] != casualty_positions[assignment.casualty_id]):
            reasons[assignment.casualty_id].append("active_route_destination_changed")
        warnings = [reason for reason in reasons[assignment.casualty_id]
                    if reason.startswith("active_route_")]
        if warnings:
            rationale.append(f"Preserved {assignment.casualty_id}/{assignment.responder_id} requires "
                             f"human route review: {', '.join(warnings)}. "
                             "Historical approval is retained; the route is not silently replaced.")
    routes: dict[tuple[str, str], AStarRoute] = {}
    priorities = {casualty.id: _priority(casualty, request) for casualty in scene.casualties}
    new_pairs: list[tuple[str, str]] = []
    if scene_map is None or scene_map.occupancy_grid is None:
        reason = "map_unavailable" if scene_map is None else "occupancy_grid_unavailable"
        for casualty_id, casualty_reasons in reasons.items():
            if casualty_id not in active_casualties:
                casualty_reasons.append(reason)
        rationale.append("No usable occupancy map: no new routes or assignments are emitted. "
                         "Existing active work remains unchanged in preserved_assignments only.")
        engine = "none"
    else:
        router = _GridRouter(scene_map)
        for casualty in sorted(scene.casualties, key=lambda item: item.id):
            uncertainty = _observation_reasons(casualty, request)
            if casualty.id in active_casualties:
                continue
            if uncertainty:
                reasons[casualty.id] = uncertainty
                continue
            failures: set[str] = set()
            required = set(request.required_capabilities.get(casualty.id, []))
            for responder in sorted(scene.responders, key=lambda item: item.id):
                if responder.id in active_responders:
                    failures.add("responder_reserved_active")
                elif responder.status != "available":
                    failures.add("responder_unavailable")
                elif not required.issubset(responder.capabilities):
                    failures.add("required_capabilities_missing")
                else:
                    route = router.plan(responder.position, casualty.position)
                    if route is None:
                        failures.add("unreachable")
                    else:
                        routes[casualty.id, responder.id] = route
            reasons[casualty.id] = sorted(failures) or ["no_responders"]
        if routes:
            if engine == "ortools":
                try:
                    new_pairs = _ortools(routes, priorities)
                except ImportError:
                    engine = "fallback"
                    rationale.append("OR-Tools unavailable: deterministic augmenting-path fallback used.")
                    new_pairs = _greedy(routes, priorities, nearest=False)
                except _SolverNotOptimal:
                    engine = "fallback"
                    rationale.append("CP-SAT bounded solve did not prove all objective stages optimal; "
                                     "discarded its partial solution and used deterministic fallback. "
                                     "No CP-SAT optimality is claimed.")
                    new_pairs = _greedy(routes, priorities, nearest=False)
            else:
                new_pairs = _greedy(routes, priorities, nearest=engine == "nearest")
        elif engine == "ortools":
            rationale.append("Candidate graph is empty; no CP-SAT solve was necessary.")
        for casualty_id, responder_id in new_pairs:
            assignments.append(Assignment(casualty_id=casualty_id, responder_id=responder_id,
                                          route=routes[casualty_id, responder_id]))
            reasons[casualty_id] = ["assigned_feasible_route"]
        assigned_ids = {assignment.casualty_id for assignment in assignments}
        feasible_ids = {pair[0] for pair in routes}
        for casualty_id in feasible_ids - assigned_ids - active_casualties:
            reasons[casualty_id] = ["feasible_but_capacity_or_objective_limited"]
    if engine == "fallback":
        rationale.append("Fallback maximizes feasible count and allocation priority via augmenting "
                         "paths; travel is a deterministic local preference, not a global optimum.")
    elif engine == "nearest":
        rationale.append("Nearest baseline greedily selects shortest feasible pairs; it does not "
                         "guarantee maximum count, urgency, or minimum total travel.")
    assignments.sort(key=lambda assignment: (assignment.casualty_id, assignment.responder_id))
    assigned_ids = {assignment.casualty_id for assignment in assignments}
    unassigned = sorted(casualty.id for casualty in scene.casualties
                        if casualty.id not in assigned_ids and casualty.id not in active_casualties)
    status = "feasible" if not unassigned else "partial" if assignments else "infeasible"
    new_distance = sum(routes[pair].distance_m for pair in new_pairs)
    objective_values = {
        "assigned_count": float(len(assignments)),
        "new_assigned_count": float(len(new_pairs)),
        "preserved_assigned_count": float(len(preserved)),
        "allocation_priority_points": float(sum(priorities[pair[0]] for pair in new_pairs)),
        "new_travel_distance_m": new_distance,
        "new_travel_cost_mm": float(sum(round(routes[pair].distance_m * 1000) for pair in new_pairs)),
        "feasible_pair_count": float(len(routes)),
    }
    return OptimizationResult(
        assignments=assignments, preserved_assignments=preserved,
        unassigned_casualty_ids=unassigned, reasons=reasons,
        metadata=RecommendationMetadata(solver_name=engine, solver_version="checkpoint-a-v1",
                                        generated_at=request.now, objective=OBJECTIVE, status=status,
                                        unassigned_casualty_ids=unassigned,
                                        objective_value=float(len(assignments))),
        runtime_ms=(perf_counter() - started) * 1000, scene_revision=request.scene_revision,
        map_revision=scene_map.revision if scene_map is not None else None,
        rationale=rationale, objective_values=objective_values, engine=engine,
    )
