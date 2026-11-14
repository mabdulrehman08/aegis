"""Deterministic fixed-time evaluation of the temporal uncertainty model.

Every case is a synthetic CV observation (or its absence) evaluated at fixed
offsets from a fixed epoch. Each step records the expected exclusion reasons,
the model's reasons, and the legacy solver's reasons for the same thresholds,
so the report shows constraint behaviour and that eligibility is never wider
than the legacy solver. It measures rule conformance, not perception or
medical accuracy; there is no training data and no learned component.
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from backend.app.intelligence.uncertainty import UncertaintyPolicy, evaluate_uncertainty
from backend.app.models import Casualty, CVObservation, CVTrack, Position, Scenario
from optimization.models import OptimizationRequest
from optimization.solver import _observation_reasons

EPOCH = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
HOME = Position(frame_id="world", x_m=4.5, y_m=2.5)
SCHEMA = "aegis.uncertainty_evaluation.v1"
DISCLAIMER = (
    "Synthetic rule-conformance evaluation of deterministic eligibility constraints. "
    "ranking_score is a ranking heuristic, not a calibrated probability, and nothing here "
    "measures perception, triage or medical accuracy."
)


@dataclass(frozen=True)
class Step:
    offset_seconds: float
    expected_reasons: tuple[str, ...]
    observation: CVObservation | None = None


@dataclass(frozen=True)
class Case:
    name: str
    description: str
    steps: tuple[Step, ...]
    policy: UncertaintyPolicy = field(default_factory=UncertaintyPolicy)
    location_basis: str | None = "calibrated_world"
    authoritative_position: Position | None = field(default_factory=lambda: HOME)


def at(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def observation(observed: float = 0, seen: float | None = None, confidence: float = 0.9,
                state: str | None = "visible", position: Position = HOME,
                identifier: str = "obs-1") -> CVObservation:
    track = None
    if state is not None:
        last_seen = at(observed if seen is None else seen)
        track = CVTrack(track_id="track-1", state=state,
                        first_seen_at=min(last_seen, at(-120)), last_seen_at=last_seen)
    return CVObservation(id=identifier, source="cv", observed_at=at(observed), position=position,
                         kind="person_detected", confidence=confidence, track=track)


def steps(base: CVObservation | None, *pairs: tuple[float, tuple[str, ...]]) -> tuple[Step, ...]:
    return tuple(Step(offset, expected, base) for offset, expected in pairs)


STALE = ("observation_stale", "track_stale")

CASES: tuple[Case, ...] = (
    Case("fresh_visible", "Visible calibrated detection ages through the 30 s freshness window.",
         steps(observation(), (0, ()), (15, ()), (29.999999, ()), (30, STALE), (31, STALE))),
    Case("confidence_at_threshold", "Confidence exactly at min_confidence stays eligible.",
         steps(observation(confidence=0.5), (0, ()))),
    Case("confidence_below_threshold", "Confidence just below min_confidence is excluded.",
         steps(observation(confidence=0.49), (0, ("observation_low_confidence",)))),
    Case("future_dated", "Report and detection stamped 10 s ahead of the evaluation clock.",
         steps(observation(observed=10), (0, ("observation_future_dated", "track_future_dated")),
               (9.999999, ("observation_future_dated", "track_future_dated")), (10, ()))),
    Case("stale_detection_fresh_report", "Recent report re-sending a detection last seen 25 s earlier.",
         steps(observation(observed=0, seen=-25), (0, ()), (4.999999, ()), (5, ("track_stale",)),
               (30, STALE))),
    Case("occluded", "Occluded track keeps its last reported position but is never newly assigned.",
         steps(observation(state="occluded"), (0, ("track_occluded",)),
               (30, ("observation_stale", "track_occluded", "track_stale")))),
    Case("lost", "Lost track has unknown location and is never newly assigned.",
         steps(observation(state="lost"), (0, ("track_lost",)))),
    Case("recovery", "Lost, then occluded, then visible again in later reports.",
         (Step(0, ("track_lost",), observation(state="lost", identifier="obs-1")),
          Step(5, ("track_occluded",), observation(observed=5, state="occluded", identifier="obs-2")),
          Step(10, (), observation(observed=10, identifier="obs-3")))),
    Case("no_track", "Detection with no track: detection age equals report age.",
         steps(observation(state=None), (0, ()), (30, ("observation_stale",))),
         location_basis=None),
    Case("zone_estimate_default", "Zone reference point accepted by default and labelled estimated.",
         steps(observation(), (0, ())), location_basis="zone_estimate"),
    Case("zone_estimate_strict", "Opt-in policy refusing zone estimates for new assignments.",
         steps(observation(), (0, ("zone_estimate_not_allowed",))),
         policy=UncertaintyPolicy(allow_zone_estimates=False), location_basis="zone_estimate"),
    Case("position_mismatch_distance", "Reported position 0.3 m from the human-known position.",
         steps(observation(position=Position(frame_id="world", x_m=4.8, y_m=2.5)),
               (0, ("observation_position_mismatch",)))),
    Case("position_within_tolerance", "Reported position 0.2 m from the human-known position.",
         steps(observation(position=Position(frame_id="world", x_m=4.7, y_m=2.5)), (0, ()))),
    Case("position_mismatch_frame", "Reported position in a different coordinate frame.",
         steps(observation(position=Position(frame_id="camera", x_m=4.5, y_m=2.5)),
               (0, ("observation_position_mismatch",)))),
    Case("missing_human_known", "No CV observation; authoritative human-known position exists.",
         steps(None, (0, ()), (600, ())), location_basis=None),
    Case("missing_unknown", "No CV observation and no authoritative position.",
         steps(None, (0, ("location_unknown",))), location_basis=None, authoritative_position=None),
    Case("decay_gate", "Opt-in decay gate: 0.9 halves every 30 s and falls below 0.5 at ~25.44 s.",
         steps(observation(), (0, ()), (25, ()), (26, ("decayed_confidence_below_threshold",)),
               (31, ("observation_stale", "track_stale", "decayed_confidence_below_threshold"))),
         policy=UncertaintyPolicy(decay_gates_eligibility=True)),
    Case("decay_gate_at_threshold", "Opt-in decay gate excludes confidence equal to the threshold.",
         steps(observation(confidence=0.5), (0, ("decayed_confidence_below_threshold",))),
         policy=UncertaintyPolicy(decay_gates_eligibility=True)),
)


def legacy_reasons(case: Case, step: Step) -> list[str] | None:
    """Reasons from the existing solver rule, or None when it has no comparable input."""
    if case.authoritative_position is None:
        return None
    scenario = Scenario(
        schema_version="1.0", id="uncertainty-eval", name="uncertainty-eval", synthetic=True,
        frame_id=case.authoritative_position.frame_id, created_at=EPOCH,
        casualties=[Casualty(id="casualty-1", position=case.authoritative_position,
                             medical_assessment=None)],
        responders=[],
    )
    request = OptimizationRequest(
        scenario=scenario, now=at(step.offset_seconds),
        observations={} if step.observation is None else {"casualty-1": step.observation},
        freshness_seconds=case.policy.freshness_seconds, min_confidence=case.policy.min_confidence,
        position_tolerance_m=case.policy.position_tolerance_m,
    )
    return _observation_reasons(scenario.casualties[0], request)


def seconds(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def iso(instant: datetime | None) -> str | None:
    return None if instant is None else instant.isoformat()


def build_report() -> dict:
    """Evaluate every case step and return the machine-readable report."""
    cases = []
    by_reason: dict[str, int] = {}
    totals = {"steps": 0, "passed": 0, "eligible": 0, "excluded": 0,
              "legacy_compared": 0, "legacy_identical": 0, "stricter_than_legacy": 0,
              "wider_than_legacy": 0}
    for case in CASES:
        rows = []
        for step in case.steps:
            now = at(step.offset_seconds)
            report = evaluate_uncertainty(
                step.observation, now, case.policy, location_basis=case.location_basis,
                authoritative_position=case.authoritative_position)
            legacy = legacy_reasons(case, step)
            passed = report.reasons == step.expected_reasons
            totals["steps"] += 1
            totals["passed"] += passed
            totals["eligible" if report.eligible_for_new_assignment else "excluded"] += 1
            for reason in report.reasons:
                by_reason[reason] = by_reason.get(reason, 0) + 1
            comparison = None
            if legacy is not None:
                totals["legacy_compared"] += 1
                if not report.eligible_for_new_assignment and not legacy:
                    comparison = "stricter"
                elif report.eligible_for_new_assignment and legacy:
                    comparison = "wider"
                else:
                    comparison = "identical" if list(report.reasons) == legacy else "same_eligibility"
                totals["legacy_identical"] += comparison == "identical"
                totals["stricter_than_legacy"] += comparison == "stricter"
                totals["wider_than_legacy"] += comparison == "wider"
            rows.append({
                "offset_seconds": step.offset_seconds,
                "evaluated_at": iso(now),
                "observation_id": report.observation_id,
                "expected_reasons": list(step.expected_reasons),
                "reasons": list(report.reasons),
                "passed": passed,
                "eligible_for_new_assignment": report.eligible_for_new_assignment,
                "location_state": report.location_state,
                "location_evidence": report.location_evidence,
                "raw_confidence": report.raw_confidence,
                "ranking_score": None if report.ranking_score is None else round(report.ranking_score, 6),
                "report_age_seconds": report.report_age_seconds,
                "detection_age_seconds": report.detection_age_seconds,
                "next_transition_at": iso(report.next_transition_at),
                "legacy_reasons": legacy,
                "legacy_comparison": comparison,
            })
        cases.append({
            "name": case.name,
            "description": case.description,
            "policy": case.policy.model_dump(mode="json"),
            "location_basis": case.location_basis,
            "authoritative_position": (None if case.authoritative_position is None
                                       else case.authoritative_position.model_dump(mode="json")),
            "steps": rows,
        })
    return {
        "schema": SCHEMA,
        "epoch": iso(EPOCH),
        "disclaimer": DISCLAIMER,
        "default_policy": UncertaintyPolicy().model_dump(mode="json"),
        "summary": {"cases": len(cases), **totals,
                    "exclusions_by_reason": dict(sorted(by_reason.items()))},
        "cases": cases,
    }


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Temporal uncertainty evaluation",
        "",
        f"Schema `{report['schema']}`; fixed epoch {report['epoch']}; generated by "
        "`python -m scripts.evaluate_uncertainty`.",
        "",
        report["disclaimer"],
        "",
        "## Summary",
        "",
        f"- Cases: {summary['cases']}; time steps: {summary['steps']}; "
        f"expectations met: {summary['passed']}/{summary['steps']}",
        f"- Eligible steps: {summary['eligible']}; excluded steps: {summary['excluded']}",
        f"- Legacy solver comparison: {summary['legacy_compared']} steps compared, "
        f"{summary['legacy_identical']} identical reasons, "
        f"{summary['stricter_than_legacy']} stricter, {summary['wider_than_legacy']} wider",
        "",
        "| Exclusion reason | Steps |",
        "|---|---:|",
        *(f"| `{reason}` | {count} |" for reason, count in summary["exclusions_by_reason"].items()),
        "",
        "## Steps",
        "",
        "| Case | t (s) | Eligible | Reasons | Expected met | Location | Evidence | Score "
        "| Next transition | Legacy |",
        "|---|---:|---|---|---|---|---|---:|---|---|",
    ]
    for case in report["cases"]:
        for row in case["steps"]:
            score = "" if row["ranking_score"] is None else f"{row['ranking_score']:.3f}"
            lines.append(
                f"| {case['name']} | {seconds(row['offset_seconds'])} | "
                f"{'yes' if row['eligible_for_new_assignment'] else 'no'} | "
                f"{', '.join(row['reasons']) or '-'} | {'yes' if row['passed'] else 'NO'} | "
                f"{row['location_state']} | {row['location_evidence']} | {score} | "
                f"{row['next_transition_at'] or '-'} | {row['legacy_comparison'] or 'n/a'} |")
    lines += ["", "## Cases", ""]
    lines += [f"- **{case['name']}**: {case['description']}" for case in report["cases"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument("--check", action="store_true",
                        help="Fail if committed reports differ instead of writing them.")
    args = parser.parse_args(argv)
    report = build_report()
    rendered = {"uncertainty.json": json.dumps(report, indent=2) + "\n",
                "uncertainty.md": render_markdown(report)}
    summary = report["summary"]
    failed = summary["passed"] != summary["steps"] or summary["wider_than_legacy"]
    if args.check:
        stale = [name for name, text in rendered.items()
                 if not (args.output_dir / name).is_file()
                 or (args.output_dir / name).read_text() != text]
        if stale:
            print(f"Out of date: {', '.join(stale)}", file=sys.stderr)
            return 1
    else:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, text in rendered.items():
            (args.output_dir / name).write_text(text)
    print(f"{summary['passed']}/{summary['steps']} steps met expectations across "
          f"{summary['cases']} cases; {summary['wider_than_legacy']} wider than legacy")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
