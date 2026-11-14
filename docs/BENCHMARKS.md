# Reproducible synthetic optimizer benchmark

```sh
uv sync --locked
uv run python -m scripts.benchmark --output reports/benchmark.json
```

The CLI writes measured JSON and a sibling Markdown report. Inputs include a
known three-casualty/two-responder 2D occupancy grid and multiple fixed-seed
scenes with variable positions, blocked cells, capability requirements, busy and
offline responders, and explicit human assessments (including unassessed
casualties). It compares `nearest` and `ortools` through the same
`OptimizationRequest`/`optimize` contract and identical feasibility inputs.

Reports distinguish initial planning and disruption/replanning. They measure
assignments, unassigned casualties, routed distance, constraint violations, and
runtime. Validation checks new route/map consistency, unique assignments,
availability, capabilities, assessment provenance, observation freshness, track
visibility/confidence, and independent grid reachability. Existing assignments
are checked for exact historical preservation and excluded from new assignments
and unassigned casualties. Old preserved route revisions generate separate
review warnings; they are not silently rerouted or certified as current.
Any violation fails the CLI instead of generating a success report.

The solver's protected-parity policy permits unassessed casualties without
inventing a medical label. Inputs are checked for mutation. `nearest` is a
shortest-feasible-pair greedy baseline, not a maximum-cardinality or
priority-optimal baseline. Both use exactly the same feasibility graph; do not
compare raw distance as an improvement if assigned counts or allocation points
differ. OR-Tools lexicographically optimizes count, allocation points, travel,
then identifiers. The report records the engine actually used, not just the
requested name. First-call solver/import overhead is included, explicitly, in
the one-shot runtime measurements; no warmup or statistical inference is used.

Fixed seeds make inputs reproducible, not wall-clock timing. Solver versions,
host, and machine load affect timings. These tiny synthetic scenes are not
evidence of clinical safety, production performance, or universal solver
superiority. No medical inference or RL training is performed. Only generated
reports contain run-specific measured metrics; no estimated results are claimed
in this document.

## Recorded validation run

An implementation validation run on November 13, 2026 (Python 3.13.11,
OR-Tools 9.15.6755) completed all 24 measured solver calls with **zero new-work
constraint violations**. Six scenes were measured for each engine and phase.

| Engine | Phase | New assignments | Preserved assignments | Unassigned | New routed meters |
|---|---|---:|---:|---:|---:|
| nearest | initial | 27 | 0 | 26 | 272 |
| ortools | initial | 32 | 0 | 21 | 391 |
| nearest | replan | 20 | 6 | 27 | 206 |
| ortools | replan | 20 | 6 | 27 | 233 |

The known `grid3` initial solution assigned two casualties with both engines:
nearest routed 9 m and OR-Tools routed 5 m. Across replans, twelve historical
route-revision review warnings were reported separately, not counted as newly
feasible routes. Invalid active-entity identifiers were rejected by both engines
for every scene. The first initial OR-Tools call includes cold import overhead; it is
not a steady-state latency estimate. Aggregate distances cannot be interpreted
as a travel improvement because assigned counts and allocation objectives differ.

This is one measured run, not a performance guarantee. `reports/benchmark.json`
and `reports/benchmark.md` hold that measured report. Re-run the command to
obtain current JSON and Markdown with per-scene measurements and input hashes.
