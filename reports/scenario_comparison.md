# Counterfactual and stability scenario comparison

Synthetic fixed-time inputs; every row is a real solver rerun, not generated text. Nothing here approves or dispatches work; all output requires human review.

## Responder, capability and obstacle counterfactuals

Scene revision 1, map revision map-1, expires 2026-11-13T12:05:00Z (expired: False); 4 of at most 5 solver calls.

Baseline (ortools, policy distance_first): casualty-000→responder-001, casualty-001→responder-000; preserved: none.

| Scenario | Assumption differences | New assignments | Engine | Verdict | Tier deltas |
|---|---|---|---|---|---|
| responder-000-offline | responder.status[responder-000]: available → offline | casualty-000→responder-001 | ortools | degraded | new_assigned_count -1, allocation_priority_points -1, new_travel_cost_mm -3000 |
| lose-first-aid | responder.capabilities[responder-001]: ['first_aid'] → [] | casualty-000→responder-000 | ortools | degraded | new_assigned_count -1, allocation_priority_points -1, new_travel_cost_mm -3000 |
| casualty-000-blocked | map.obstacle[debris]: absent → present; map.revision[-]: map-1 → map-1+cf-casualty-000-blocked | casualty-001→responder-000, casualty-002→responder-001 | ortools | degraded | new_assigned_count +0, allocation_priority_points -2, new_travel_cost_mm +30000 |

## Previous unapproved proposal vs objective policy

Scene revision 1, map revision map-1, expires 2026-11-13T12:05:00Z (expired: False); 5 of at most 5 solver calls.

Baseline (ortools, policy distance_first): casualty-000→responder-001, casualty-001→responder-000; preserved: none.

| Scenario | Assumption differences | New assignments | Engine | Verdict | Tier deltas |
|---|---|---|---|---|---|
| stability-first | objective_policy[-]: distance_first → stability_first | casualty-000→responder-000, casualty-001→responder-001 | ortools | not_comparable | new_assigned_count +0, allocation_priority_points +0, new_travel_cost_mm +4000, previous_assignment_changes -2 |
| budget-zero | objective_policy[-]: distance_first → bounded_travel_stability; stability_travel_budget_m[-]: None → 0.0 | casualty-000→responder-001, casualty-001→responder-000 | ortools | not_comparable | new_assigned_count +0, allocation_priority_points +0, new_travel_cost_mm +0, previous_assignment_changes +0 |
| budget-ten | objective_policy[-]: distance_first → bounded_travel_stability; stability_travel_budget_m[-]: None → 10.0 | casualty-000→responder-000, casualty-001→responder-001 | ortools | not_comparable | new_assigned_count +0, allocation_priority_points +0, new_travel_cost_mm +4000, previous_assignment_changes -2 |
| forget-previous | previous_assignments[-]: [['casualty-000', 'responder-000'], ['casualty-001', 'responder-001']] → [] | casualty-000→responder-001, casualty-001→responder-000 | ortools | not_comparable | new_assigned_count +0, allocation_priority_points +0, new_travel_cost_mm +0, previous_assignment_changes n/a |

## Hard-reserved approved work under hypothetical changes

Scene revision 1, map revision map-1, expires 2026-11-13T12:05:00Z (expired: False); 3 of at most 5 solver calls.

Baseline (ortools, policy distance_first): casualty-001→responder-001; preserved: casualty-000→responder-000.

| Scenario | Assumption differences | New assignments | Engine | Verdict | Tier deltas |
|---|---|---|---|---|---|
| active-responder-offline | responder.status[responder-000]: available → offline | casualty-001→responder-001 | ortools | unchanged | new_assigned_count +0, allocation_priority_points +0, new_travel_cost_mm +0 |
| debris-on-active | map.obstacle[debris]: absent → present; map.revision[-]: map-1 → map-1+cf-debris-on-active | casualty-001→responder-001 | ortools | unchanged | new_assigned_count +0, allocation_priority_points +0, new_travel_cost_mm +0 |

Verdicts are lexicographic and given only when policy, budget, tier set and previous preferences match the baseline; otherwise `not_comparable` and per-tier measurements are reported. Travel is in millimeters. Active work is identical in every scenario.
