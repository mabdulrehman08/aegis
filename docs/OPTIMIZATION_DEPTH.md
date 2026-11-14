# Optimization depth: stability, exclusions and counterfactuals

Additive to the Checkpoint A solver. With no new fields the solver, objective string
(`count_then_protected_human_urgency_then_travel_then_ids_v1`), assignments and
`objective_values` are unchanged. Nothing here approves, dispatches or clinically
assesses anyone; all output requires human review. No RL/PPO is involved.

## Hard constraints versus soft preferences

Hard, in every engine and policy: available responder, capability inclusion, verified
grid route, one-to-one assignment, observation uncertainty gates, and **active
(approved/acknowledged) work, which is always reserved and preserved unchanged**.
Soft: `previous_assignments`, earlier **unapproved** recommendations only. A previous
pair touching an active casualty or responder is never optimized; it is reported as
`blocked_active_casualty` / `blocked_active_responder`.

Stability can never displace a higher-priority casualty and never reduces feasible new
assignment count, because it sits below both tiers in every policy.

## `objective_policy`

| Policy | CP-SAT tier order |
|---|---|
| `distance_first` (default) | count, protected priority, travel, **changes (tie-break only)**, IDs |
| `stability_first` | count, protected priority, **fewest changes**, travel, IDs |
| `bounded_travel_stability` | count, priority, minimum travel `T*`; then travel ≤ `T*` + `stability_travel_budget_m`; fewest changes; travel; IDs |

`stability_travel_budget_m` is required for, and only valid with, the bounded policy.
Changes = previous proposals not retained verbatim (including forced ones blocked by
feasibility). Without `previous_assignments` all policies equal the legacy order.
`metadata.objective` switches to a v2 identifier only when previous proposals exist.

Tradeoff: `stability_first` can cost travel with no cap; the bounded policy caps the
added travel at an explicit, auditable amount; the default never trades travel for
stability.

## Engine honesty

Only CP-SAT proves stage optimality. `objective_tiers[].proven_optimal` is true only for
engine `ortools` (including when its bounded solve did not fall back). The fallback
orders candidates by the policy but does **not** prove minimum changes, and ignores the
travel budget; `nearest` ignores previous assignments. Rationale text says so, and
`engine` is the actual algorithm run (`none` without an occupancy map).

## `assignment_exclusions`

`dict[casualty_id, list[reason]]`: caller-policy reasons that block **new** work in the
shared graph for all engines and analysis. They are not CV observations, never change
medical assessments or positions, and never affect active work (listed in rationale as
ignored). Unknown casualties, empty reason lists, blank reasons, and solver-owned names
(or `observation_`, `track_`, `active_route_` prefixes) raise `ValueError`. Reasons are
deduplicated and sorted into `reasons[casualty]` after any observation reasons, and
echoed in `applied_assignment_exclusions` (pending casualties only).

## Result additions

`objective_policy`, `objective_tiers` (`name`, `direction`, `value`, `proven_optimal`, in
policy order), `previous_assignment_outcomes` (`retained`, `changed_responder`,
`not_reassigned`, `blocked_infeasible` with pair-specific reasons, `blocked_active_*`),
`applied_assignment_exclusions`.

## `optimization.analysis`

```python
analyze_counterfactuals(CounterfactualRequest) -> CounterfactualAnalysis
```

`CounterfactualRequest(base: OptimizationRequest, scenarios: 1..4, expires_at?)`. Each
`CounterfactualScenario(id, label, changes: 1..8, map_revision?)` holds explicit changes:
`responder_availability`, `responder_capabilities` (replace tag set), `obstacle_added`,
`obstacle_removed`, `preference` (previous assignments, policy, budget). Invalid
references raise `AnalysisInputError` (a `ValueError`); Pydantic bounds raise
`ValidationError`. At most five solver calls (baseline plus four scenarios).

Every scenario deep-copies the base, applies the changes, revalidates it and reruns the
real `optimize`; the source request is verified unmodified by fingerprint. Counterfactual
map changes get revision `<base>+cf-<scenario id>` unless supplied.

Outputs (frozen models) include: `scene_revision`, `map_revision`, `generated_at`,
`expires_at`/`expired` (source expiry versus `base.now`), `source_request_sha256`,
`baseline`, and per scenario `assumption_differences` (exact before/after of changed
inputs only), `recommendation`, `changed_assignments` (new work only: added / removed /
responder_changed with route distances), `preserved_assignments` separately with
`preserved_assignments_unchanged`, `reason_changes`, `objective_deltas`,
`objective_verdict`, `requested_solver`/`engine`/`base_engine`/`fallback_used`,
`no_op`, and constant `authorizes_dispatch=False`.

**Comparability.** A delta is scenario minus base. Stability deltas are `not_comparable`
when previous preferences differ or a tier exists in only one run. A lexicographic
verdict (`improved`/`degraded`/`unchanged`) is only given when policy, budget, previous
preferences and tier set match; otherwise `not_comparable`. Differing engines are noted,
since measured values then carry different optimality claims.

## Limitations

Counterfactuals are reruns on explicit hypothetical inputs, not predictions. Travel is
grid A* meters (reported as rounded millimeters in tiers). CP-SAT stages keep their 1 s /
5 s budgets, so worst-case analysis time is bounded by five solves. `runtime_ms` is
diagnostic and nondeterministic. Active routes against a hypothetical map are flagged
(`active_route_stale_map`), never rewritten. Regenerate the sample report with
`uv run python -m scripts.compare_scenarios --output reports/scenario_comparison.json`.
