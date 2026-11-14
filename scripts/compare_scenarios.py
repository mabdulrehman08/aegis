"""Generate a deterministic counterfactual and stability scenario comparison.

Every row is a real solver rerun on a fixed synthetic request; timing fields are
excluded so repeated runs on the same dependencies produce identical reports.
"""

import argparse
import json
from pathlib import Path

from backend.app.models import Assignment, Obstacle
from optimization.analysis import (
    CounterfactualRequest, CounterfactualScenario, ObstacleAddedChange, PreferenceChange,
    ResponderAvailabilityChange, ResponderCapabilityChange, analyze_counterfactuals,
)
from optimization.models import OptimizationRequest
from scripts.scenarios import NOW, make_scenario, position


def block(identifier, column, row):
    return Obstacle(id=identifier, vertices=[position(column + 0.2, row + 0.2),
                                             position(column + 0.8, row + 0.2),
                                             position(column + 0.8, row + 0.8),
                                             position(column + 0.2, row + 0.8)])


def case(identifier, title, base, scenarios):
    analysis = analyze_counterfactuals(CounterfactualRequest(
        base=base, scenarios=scenarios, expires_at=NOW.replace(minute=5)))
    assert analysis.solver_calls <= analysis.max_solver_calls
    dump = analysis.model_dump(mode="json")
    for result in [dump["baseline"], *(item["recommendation"] for item in dump["scenarios"])]:
        result.pop("runtime_ms")
    return {"id": identifier, "title": title, "analysis": dump}


def run():
    scenario, requirements = make_scenario(None)
    stable = OptimizationRequest(
        scenario=scenario, required_capabilities=requirements, now=NOW, solver="ortools",
        previous_assignments=[Assignment(casualty_id="casualty-000", responder_id="responder-000"),
                              Assignment(casualty_id="casualty-001", responder_id="responder-001")])
    plain = stable.model_copy(update={"previous_assignments": []}, deep=True)
    active = plain.model_copy(update={"active_assignments": [
        Assignment(casualty_id="casualty-000", responder_id="responder-000")]}, deep=True)
    cases = [
        case("hypothetical-world-changes", "Responder, capability and obstacle counterfactuals", plain, [
            CounterfactualScenario(id="responder-000-offline", label="Responder 000 goes offline",
                                   changes=[ResponderAvailabilityChange(
                                       kind="responder_availability", responder_id="responder-000",
                                       status="offline")]),
            CounterfactualScenario(id="lose-first-aid", label="Responder 001 loses first_aid",
                                   changes=[ResponderCapabilityChange(
                                       kind="responder_capabilities", responder_id="responder-001",
                                       capabilities=[])]),
            CounterfactualScenario(id="casualty-000-blocked", label="Debris blocks casualty 000",
                                   changes=[ObstacleAddedChange(kind="obstacle_added",
                                                                obstacle=block("debris", 3, 1))]),
        ]),
        case("stability-policies", "Previous unapproved proposal vs objective policy", stable, [
            CounterfactualScenario(id="stability-first", label="Prefer fewest changes before travel",
                                   changes=[PreferenceChange(kind="preference",
                                                             objective_policy="stability_first")]),
            CounterfactualScenario(id="budget-zero", label="Stability with zero extra travel",
                                   changes=[PreferenceChange(
                                       kind="preference", objective_policy="bounded_travel_stability",
                                       stability_travel_budget_m=0)]),
            CounterfactualScenario(id="budget-ten", label="Stability with 10 m extra travel budget",
                                   changes=[PreferenceChange(
                                       kind="preference", objective_policy="bounded_travel_stability",
                                       stability_travel_budget_m=10)]),
            CounterfactualScenario(id="forget-previous", label="Discard previous preferences",
                                   changes=[PreferenceChange(kind="preference", previous_assignments=[])]),
        ]),
        case("active-work-preserved", "Hard-reserved approved work under hypothetical changes", active, [
            CounterfactualScenario(id="active-responder-offline", label="Active responder offline",
                                   changes=[ResponderAvailabilityChange(
                                       kind="responder_availability", responder_id="responder-000",
                                       status="offline")]),
            CounterfactualScenario(id="debris-on-active", label="Debris on the active casualty",
                                   changes=[ObstacleAddedChange(kind="obstacle_added",
                                                                obstacle=block("debris", 3, 1))]),
        ]),
    ]
    for item in cases:
        for outcome in item["analysis"]["scenarios"]:
            assert outcome["authorizes_dispatch"] is False
            assert outcome["preserved_assignments_unchanged"]
    return {"schema_version": "1.0", "synthetic_only": True, "fixed_now": NOW.isoformat(),
            "advisory_only": True, "authorizes_dispatch": False, "cases": cases}


def describe(difference):
    if difference["field"] == "map.obstacle":
        side = lambda value: "absent" if value is None else "present"
        return f"map.obstacle[{difference['entity_id']}]: {side(difference['before'])} → {side(difference['after'])}"
    return (f"{difference['field']}[{difference['entity_id'] or '-'}]: "
            f"{difference['before']} → {difference['after']}")


def pair_text(assignments):
    return ", ".join(f"{item['casualty_id']}→{item['responder_id']}" for item in assignments) or "none"


def markdown(report):
    lines = ["# Counterfactual and stability scenario comparison", "",
             "Synthetic fixed-time inputs; every row is a real solver rerun, not generated text. "
             "Nothing here approves or dispatches work; all output requires human review.", ""]
    for item in report["cases"]:
        analysis = item["analysis"]
        baseline = analysis["baseline"]
        lines += [f"## {item['title']}", "",
                  f"Scene revision {analysis['scene_revision']}, map revision {analysis['map_revision']}, "
                  f"expires {analysis['expires_at']} (expired: {analysis['expired']}); "
                  f"{analysis['solver_calls']} of at most {analysis['max_solver_calls']} solver calls.", "",
                  f"Baseline ({baseline['engine']}, policy {baseline['objective_policy']}): "
                  f"{pair_text(baseline['assignments'])}; preserved: "
                  f"{pair_text(baseline['preserved_assignments'])}.", "",
                  "| Scenario | Assumption differences | New assignments | Engine | Verdict | Tier deltas |",
                  "|---|---|---|---|---|---|"]
        for outcome in analysis["scenarios"]:
            differences = "; ".join(map(describe, outcome["assumption_differences"])) or "none"
            deltas = ", ".join(f"{d['name']} {d['delta']:+g}" if d["comparable"] else f"{d['name']} n/a"
                               for d in outcome["objective_deltas"])
            lines.append(f"| {outcome['scenario_id']} | {differences} | "
                         f"{pair_text(outcome['recommendation']['assignments'])} | {outcome['engine']} | "
                         f"{outcome['objective_verdict']} | {deltas} |")
        lines.append("")
    lines += ["Verdicts are lexicographic and given only when policy, budget, tier set and previous "
              "preferences match the baseline; otherwise `not_comparable` and per-tier measurements "
              "are reported. Travel is in millimeters. Active work is identical in every scenario.", ""]
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
    arguments.output.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(arguments.output), "markdown": str(arguments.output.with_suffix(".md")),
                      "cases": len(report["cases"])}))


if __name__ == "__main__":
    main()
