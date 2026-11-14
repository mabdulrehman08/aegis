# Intelligence expansion: evidence and demo boundaries

This enhancement keeps the merged two-camera MVP and human approval workflow.
The default live simulation still uses OR-Tools and A*, not PPO. No component
diagnoses an actor, assigns clinical labels from camera confidence, or authorizes
real dispatch. All experiments use synthetic people and geometry.

## Deterministic evidence

Run from the repository root:

```sh
uv sync --locked
uv run pytest -q
uv run python -m scripts.evaluate_uncertainty --check
uv run python -m scripts.compare_scenarios --output /tmp/aegis-comparison.json
cmp reports/scenario_comparison.json /tmp/aegis-comparison.json
uv run python -m scripts.benchmark --output /tmp/aegis-benchmark.json
uv run python -m scripts.demo
uv run python -m scripts.live_demo --json
```

Measured uncertainty evaluation: **35 expected outcomes across 18 cases**, with
zero eligibility expansions relative to the legacy solver. Cases cover visible,
occluded, lost, stale, future-dated, low-confidence, zone-estimated, and absent
camera evidence. This is deterministic policy validation, not learned confidence
calibration. The optional decay gate is inclusive at its threshold and its
transition instant is rounded up to a representable microsecond.

The committed counterfactual report contains three fixed scenario groups. One
two-responder example quantifies the stability tradeoff:

| Policy | New assignments | Route distance | Previous pairs retained |
| --- | ---: | ---: | ---: |
| Distance first | 2 | 5 m | 0 |
| Stability first | 2 | 9 m | 2 |
| Bounded stability, 0 m extra | 2 | 5 m | 0 |
| Bounded stability, 10 m extra | 2 | 9 m | 2 |

These are actual CP-SAT outputs, not universal quality claims. Assignment count
and protected human priority dominate both travel and stability. Capability,
availability, observation eligibility, verified reachability, and active
reservations remain hard constraints. A missing capability requirement means
there is no explicitly specified requirement; it is not a claim of competence.

The allocation benchmark executes 24 measured calls on identical inputs for
nearest and OR-Tools, with zero new-work constraint violations. The known
`grid3` initial scene has two assignments for each engine: nearest travels 9 m,
OR-Tools 5 m. Across all six initial scenes, nearest assigns 27 casualties and
OR-Tools 32. Their distances (272 m versus 391 m) cannot be read as a
like-for-like distance improvement: OR-Tools serves more casualties. Historical
route warnings are reported separately from new-work validity.

Timing varies by machine and includes the cold OR-Tools import in the allocation
benchmark. Deterministic comparison reports intentionally omit timing fields;
benchmark measurements must not be substituted for clinical outcomes.

## Dashboard and operator semantics

See `INTELLIGENCE_API.md` for the additive simulation endpoints, example
payloads, provenance fields, policy updates, and WebSocket integration.

- `ranking_score` is a recency heuristic, not a calibrated probability or a
  medical severity. The detector's raw confidence stays unchanged.
- A location labelled `observed` is not proof of reliability. Read
  `location_evidence`, exclusion reasons, and eligibility together. In particular,
  `human_known` is not a fabricated camera detection.
- A zone is a registered reference point, not a precise detection coordinate.
  Calibrations are externally asserted, not computed or verified by the backend.
- Policies can tighten new-work eligibility; they cannot relax the legacy
  hard gates or silently cancel approved work. Policy and impactful freshness
  changes invalidate unconsumed approvals and require a new exact revision.
- Counterfactuals solve copied scenarios without installing proposals or
  generating dispatch authority. A stale result must be recomputed.
- CP-SAT's travel bound is in rounded millimeters. Check
  `stability_budget_enforced`: fallback and nearest do not enforce that bound.
  `proven_optimal` never describes a heuristic engine.
- A retained casualty/responder pair may have a changed route; inspect
  `route_changes` rather than interpreting pair continuity as route continuity.

## Experimental isolation

PPO is developed and reviewed on a separate experimental branch under
`experiments/ppo/`, with its own dependencies and reports. It is not imported by
the backend, is not a dispatch policy, and is not required to run either demo.
Training and evaluation must use disjoint fixed seeds and identical feasibility
inputs across methods. Invalid policy attempts and safety-filtered committed
violations must be reported separately; a filter is not evidence of learned
safety. Underperformance is an acceptable experimental result.

The local prototype still lacks authentication, persistence, real emergency
dispatch, cross-camera re-identification, and certified responder clearance.
Camera streaming, the physical Arduino bridge, and dashboard rendering remain
the respective teammates' integration responsibilities. Keep the synthetic
rehearsal separate from a connected physical demonstration; use the existing
`--prepare-only` handoff documented in `LIVE_DEMO.md`.
