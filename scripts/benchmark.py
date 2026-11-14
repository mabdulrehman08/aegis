"""Measure both solvers on identical deterministic synthetic requests."""

import argparse
from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
from time import perf_counter

from backend.app.models import Assignment, CVObservation, CVTrack
from optimization.models import OptimizationRequest
from optimization.solver import optimize
from scripts.scenarios import NOW, make_scenario


SEEDS = (7, 19, 41, 83, 127)


def observations_for(scenario):
    observations = {}
    if scenario.id == "grid3":
        return observations
    for index, casualty in enumerate(scenario.casualties):
        observed_at = NOW - timedelta(seconds=90 if index == 2 else 1)
        observations[casualty.id] = CVObservation(
            id=f"observation-{index}", source="cv", observed_at=observed_at,
            position=casualty.position, kind="person_detected",
            confidence=0.2 if index == 3 else 0.95,
            track=CVTrack(track_id=f"track-{index}", state="lost" if index == 4 else "visible",
                          first_seen_at=observed_at, last_seen_at=observed_at),
        )
    return observations


def reachable(scene_map, start, goal):
    """Independent four-neighbor grid reachability for these grid-only fixtures."""
    grid = scene_map.occupancy_grid
    def cell(point):
        return (math.floor((point.x_m - grid.origin.x_m) / grid.resolution_m),
                math.floor((point.y_m - grid.origin.y_m) / grid.resolution_m))
    start_cell, goal_cell = cell(start), cell(goal)
    frontier, visited = deque([start_cell]), {start_cell}
    while frontier:
        current = frontier.popleft()
        if current == goal_cell:
            return True
        column, row = current
        for neighbor in ((column - 1, row), (column + 1, row), (column, row - 1), (column, row + 1)):
            next_column, next_row = neighbor
            if (0 <= next_column < grid.width and 0 <= next_row < grid.height
                    and grid.cells[next_row * grid.width + next_column] == 0 and neighbor not in visited):
                visited.add(neighbor)
                frontier.append(neighbor)
    return False


def validate(request, result):
    casualties = {casualty.id: casualty for casualty in request.scenario.casualties}
    responders = {responder.id: responder for responder in request.scenario.responders}
    used_casualties, used_responders, violations = set(), set(), []
    scene_map = request.scenario.scene_map
    if result.map_revision != scene_map.revision:
        violations.append("stale result map revision")
    for assignment in result.assignments:
        casualty = casualties.get(assignment.casualty_id)
        responder = responders.get(assignment.responder_id)
        if casualty.id in {active.casualty_id for active in request.active_assignments} or responder.id in {
                active.responder_id for active in request.active_assignments}:
            violations.append("active entity reused for new assignment")
        if casualty is None or responder is None:
            violations.append("unknown entity")
            continue
        if casualty.id in used_casualties or responder.id in used_responders:
            violations.append("duplicate casualty/responder")
        used_casualties.add(casualty.id)
        used_responders.add(responder.id)
        if responder.status != "available":
            violations.append("unavailable responder")
        if not set(request.required_capabilities.get(casualty.id, [])).issubset(responder.capabilities):
            violations.append("missing capability")
        if casualty.medical_assessment is not None and casualty.medical_assessment.source != "human":
            violations.append("nonhuman assessment")
        observation = request.observations.get(casualty.id)
        if observation is not None:
            if not 0 <= (request.now - observation.observed_at).total_seconds() <= request.freshness_seconds:
                violations.append("stale/future observation")
            if observation.confidence < request.min_confidence:
                violations.append("low confidence observation")
            if observation.track is not None and observation.track.state != "visible":
                violations.append("nonvisible track")
            if observation.track is not None and not 0 <= (
                    request.now - observation.track.last_seen_at).total_seconds() <= request.freshness_seconds:
                violations.append("stale/future track")
            if observation.position != casualty.position:
                violations.append("observation position mismatch")
        route = assignment.route
        if route is None:
            violations.append("missing route")
            continue
        if route.map_revision != scene_map.revision or result.map_revision != scene_map.revision:
            violations.append("stale map revision")
        if route.waypoints[0] != responder.position or route.waypoints[-1] != casualty.position:
            violations.append("route endpoints")
        grid = scene_map.occupancy_grid
        for point in route.waypoints:
            column = math.floor((point.x_m - grid.origin.x_m) / grid.resolution_m)
            row = math.floor((point.y_m - grid.origin.y_m) / grid.resolution_m)
            if (point.frame_id != scene_map.frame_id or not (0 <= column < grid.width and 0 <= row < grid.height)
                    or grid.cells[row * grid.width + column] != 0):
                violations.append("route enters blocked/outside cell")
        measured_distance = 0.0
        for first, second in zip(route.waypoints, route.waypoints[1:]):
            length = math.hypot(second.x_m - first.x_m, second.y_m - first.y_m)
            measured_distance += length
            if (first.x_m != second.x_m and first.y_m != second.y_m) or length > grid.resolution_m + 1e-8:
                violations.append("route skips grid adjacency")
        if not math.isclose(route.distance_m, measured_distance, abs_tol=1e-8):
            violations.append("incorrect route distance")
        if not reachable(scene_map, responder.position, casualty.position):
            violations.append("unreachable assignment")
    preserved = result.preserved_assignments
    if preserved != sorted(request.active_assignments, key=lambda assignment: (assignment.casualty_id, assignment.responder_id)):
        violations.append("active assignment history overwritten")
    unassigned = result.unassigned_casualty_ids
    if (set(unassigned) != set(casualties) - used_casualties - {active.casualty_id for active in preserved}
            or len(unassigned) != len(set(unassigned))):
        violations.append("incorrect unassigned partition")
    if result.scene_revision != request.scene_revision:
        violations.append("stale scene revision")
    assert not violations, f"{request.scenario.id}/{request.solver}: {violations}"
    return violations


def measure(request, phase):
    before = request.model_dump(mode="json")
    started = perf_counter()
    result = optimize(request)
    wall_ms = (perf_counter() - started) * 1000
    assert request.model_dump(mode="json") == before, "optimizer mutated benchmark input"
    violations = validate(request, result)
    return result, {
        "scenario": request.scenario.id, "phase": phase, "solver": request.solver,
        "input_sha256_excluding_solver": hashlib.sha256(
            json.dumps(request.model_dump(mode="json", exclude={"solver"}), sort_keys=True).encode()
        ).hexdigest(),
        "engine": result.engine, "assignments": len(result.assignments),
        "existing_assignments": len(result.preserved_assignments),
        "new_assignments": len(result.assignments),
        "preserved_route_review_warnings": [
            {"casualty_id": assignment.casualty_id, "responder_id": assignment.responder_id,
             "reason": "historical_route_requires_review"}
            for assignment in result.preserved_assignments
            if assignment.route is None or assignment.route.map_revision != request.scenario.scene_map.revision
        ],
        "unassigned": len(result.unassigned_casualty_ids),
        "unassigned_casualty_ids": result.unassigned_casualty_ids,
        "distance_m": sum(assignment.route.distance_m for assignment in result.assignments),
        "new_distance_m": sum(assignment.route.distance_m for assignment in result.assignments),
        "constraint_violations": len(violations), "runtime_ms": result.runtime_ms,
        "wall_runtime_ms": wall_ms, "scene_revision": result.scene_revision,
        "map_revision": result.map_revision, "objective_values": result.objective_values,
        "rationale": result.rationale,
        "reasons": result.reasons,
    }


def run():
    rows, input_hashes = [], {}
    for seed in (None, *SEEDS):
        scenario, requirements = make_scenario(seed)
        base = OptimizationRequest(scenario=scenario, required_capabilities=requirements,
                                   observations=observations_for(scenario), scene_revision=1, now=NOW)
        input_hashes[scenario.id] = hashlib.sha256(base.model_dump_json().encode()).hexdigest()
        primary, primary_row = measure(base.model_copy(update={"solver": "ortools"}, deep=True), "initial")
        baseline, baseline_row = measure(base.model_copy(update={"solver": "nearest"}, deep=True), "initial")
        assert primary.engine == "ortools", "primary benchmark must exercise OR-Tools, not a silent fallback"
        assert len(primary.assignments) >= len(baseline.assignments)
        if len(primary.assignments) == len(baseline.assignments):
            assert primary.objective_values["allocation_priority_points"] >= baseline.objective_values["allocation_priority_points"]
            if primary.objective_values["allocation_priority_points"] == baseline.objective_values["allocation_priority_points"]:
                assert primary_row["new_distance_m"] <= baseline_row["new_distance_m"] + 1e-8
        rows.extend([baseline_row, primary_row])
        if seed is None:
            assert len(primary.assignments) == len(baseline.assignments) == 2
            assert primary_row["distance_m"] == 5.0, "known-grid optimum changed"
            assert baseline_row["distance_m"] == 9.0, "known-grid greedy result changed"
        assert primary.assignments, "fixture must exercise existing assignments"
        active = primary.assignments[0]
        disrupted = scenario.model_copy(deep=True)
        disrupted.scene_map.revision = "map-2"
        occupied = {(int(entity.position.x_m), int(entity.position.y_m))
                    for entity in [*disrupted.casualties, *disrupted.responders]}
        route_cells = {(int(point.x_m), int(point.y_m)) for point in active.route.waypoints}
        blocked_cell = next((column, row) for row in range(24) for column in range(24)
                            if (column, row) not in occupied | route_cells
                            and disrupted.scene_map.occupancy_grid.cells[row * 24 + column] == 0)
        disrupted.scene_map.occupancy_grid.cells[blocked_cell[1] * 24 + blocked_cell[0]] = 100
        reserve = next(responder for responder in disrupted.responders if responder.id == active.responder_id)
        reserve.status = "busy"
        other = next((responder for responder in disrupted.responders
                      if responder.id != active.responder_id and responder.status == "available"), None)
        if other is not None:
            other.status = "offline"
        replanning = base.model_copy(update={"scenario": disrupted, "active_assignments": [active], "scene_revision": 2}, deep=True)
        for solver in ("nearest", "ortools"):
            result, row = measure(replanning.model_copy(update={"solver": solver}, deep=True), "replan")
            assert any(assignment.casualty_id == active.casualty_id and assignment.responder_id == active.responder_id
                       for assignment in result.preserved_assignments), "valid existing assignment was not preserved"
            assert row["preserved_route_review_warnings"], "old active routes must be flagged, not rewritten"
            rows.append(row)
        for solver in ("nearest", "ortools"):
            invalidated = base.model_copy(update={"solver": solver, "active_assignments": [
                Assignment(casualty_id="unknown-casualty", responder_id=active.responder_id)]}, deep=True)
            try:
                optimize(invalidated)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid existing assignment identifiers were accepted")
    return {"schema_version": "1.0", "synthetic_only": True, "seeds": list(SEEDS),
            "fixed_now": NOW.isoformat(), "python": platform.python_version(),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(), "machine": platform.machine(),
            "ortools_version": version("ortools"),
            "timing_policy": "One measured call per input/solver; first OR-Tools call includes cold import. No warmup or averaging.",
            "input_sha256": input_hashes, "runs": rows}


def markdown(report):
    lines = ["# Measured synthetic benchmark", "", f"Fixed seeds: {report['seeds']}; Python {report['python']}; OR-Tools {report['ortools_version']}.",
             f"Platform: {report['platform']}; measured at {report['generated_at_utc']}.",
             "", report["timing_policy"], "",
             "| Scene | Phase | Solver / engine | New / existing | Unassigned | New distance (m) | Violations | Solver ms | Wall ms |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in report["runs"]:
        lines.append(f"| {row['scenario']} | {row['phase']} | {row['solver']} / {row['engine']} | "
                     f"{row['new_assignments']} / {row['existing_assignments']} | "
                     f"{row['unassigned']} | {row['new_distance_m']:.3f} | {row['constraint_violations']} | "
                     f"{row['runtime_ms']:.3f} | {row['wall_runtime_ms']:.3f} |")
    lines.extend(["", "Timing is measured, machine-dependent, and not deterministic. Both solvers receive identical feasibility inputs.",
                  "Replans preserve one historical approved assignment with a busy responder, revise the map, and take another responder offline.",
                  "Historical routes remain unchanged and are flagged for review when their map revision is old. They are not new dispatchable work.",
                  f"Historical-route review warnings: {sum(len(row['preserved_route_review_warnings']) for row in report['runs'])}.",
                  "Human assessments are fixed synthetic input; no clinical inference, real dispatch, or RL training occurs.", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON path; Markdown uses the same stem")
    arguments = parser.parse_args()
    if arguments.output.suffix.lower() != ".json":
        parser.error("--output must use a .json suffix; the sibling .md is generated separately")
    report = run()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path = arguments.output.with_suffix(".md")
    markdown_path.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(arguments.output), "markdown": str(markdown_path),
                      "runs": len(report["runs"]),
                      "constraint_violations": sum(row["constraint_violations"] for row in report["runs"])}))


if __name__ == "__main__":
    main()
