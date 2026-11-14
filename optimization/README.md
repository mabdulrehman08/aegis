# Checkpoint A optimization

Pure Python interfaces: `optimization.models.OptimizationRequest` /
`OptimizationResult`, `optimization.solver.optimize(request)`, and
`optimization.routing.plan_route(scene_map, start, goal)`.
Backend models are imported, never duplicated. No transport, dispatch, approval,
global cache, or mutable external state is introduced. Pin `request.now` for
reproducibility; `runtime_ms` is diagnostic and inherently nondeterministic.

## Safety and allocation policy

All engines build the same candidate graph: available responders only, required
capability set inclusion, no active entities reused, and a verified grid route.
Requirements and observations are keyed by **casualty ID**, not observation ID.
Unknown casualty keys and invalid/conflicting active assignments raise ValueError.
Valid active assignments are copied unchanged into `preserved_assignments`,
never into the new-proposal `assignments` list, including busy/offline responders
and old/null routes: preservation does not certify their continued executability.
They must be independently revalidated before dispatch. If map/grid is missing,
no new assignments are emitted; preserved assignments remain unchanged. Active
casualties are excluded from the unassigned list because they are already served.
Status and `assigned_count`/`new_assigned_count` describe only pending/new work;
`preserved_assigned_count` separately reports existing work. An empty pending
workload is feasible, even when existing work cannot currently be rerouted.
Preserved null/unverifiable routes carry `active_route_unverified`; routes from
a different supplied map revision carry `active_route_stale_map`.
Capability tags are case-sensitive. An absent casualty requirement explicitly
means no required tags, not a missing qualification inferred by the solver.

An absent observation does not invalidate a known scene casualty. A supplied
observation blocks **new** assignments if stale, future-dated, below the confidence
threshold, position/frame mismatched, or tracked as occluded/lost. Frames must
match exactly; Euclidean position difference may be at most the request's finite,
nonnegative `position_tolerance_m` (default **0.25 meters**). Routing always uses
the authoritative scenario position, never the observation position. Both capture
time and actual `track.last_seen_at` must be fresh. Exact threshold equality is
accepted. Uncertainty never modifies medical assessments or cancels active work.

CP-SAT uses separate solves fixing each preceding optimum, not unsafe big-M
weights: maximize count, maximize allocation points, minimize rounded millimeter
route cost, then prefer sorted casualty IDs and sorted responder IDs. Human
assessments supply immediate=2, delayed=1, minor=0 allocation points. Unassessed,
future-assessed, expectant, and deceased cases receive **protected parity (2)**,
not a fabricated severity. Thus expectant/deceased labels never automatically
deprioritize a person, nor does missing assessment imply low urgency. This is an
explicit provisional human-review allocation policy, not a clinical triage rule.
`objective_values` reports new-work priority/cost separately from preserved work.

Each CP-SAT stage has a **1-second** time budget, capped by a **5-second** total
solve budget. If any stage does not prove optimality, its partial solution is
discarded and the deterministic fallback is used, explicitly without claiming
CP-SAT optimality. Model/geometry preparation is not part of that solve budget.
When OR-Tools cannot be imported, `ortools` requests also use deterministic
priority-ordered augmenting paths: maximum feasible cardinality and allocation
points, but not guaranteed minimum total travel. `nearest` is a global
shortest-feasible-edge greedy benchmark with exactly the same safety constraints;
it does not promise cardinality/priority optimality. `engine` identifies the
actual algorithm (`none` when occupancy is unavailable).

## Geometry

Grid origin is lower-left, cells row-major, coordinates/resolution are meters.
A* uses four neighbors and Manhattan distance. Occupied/unknown/out-of-bounds
cells are blocked. Polygon **closed-cell intersection** blocks cells, including
edge/vertex contact, crossing edges, tiny polygons, and enclosing polygons.
Malformed/degenerate/self-intersecting polygons and nonfinite arithmetic raise
ValueError. Endpoint frames must match. Bounds are half-open at upper/right;
internal grid-boundary endpoints touching a blocked neighboring cell are blocked.
World waypoints start/end exactly at inputs and use cell centers between them;
same-cell routes use a direct segment. Distance is actual polyline length.
Routes carry the map revision, never pixel coordinates. This is conservative
point-agent planning, not a responder-clearance certificate. Cell-center paths
and whole-cell polygon blocking are pessimistic: reported distance can exceed a
continuous-space shortest route, and narrow but physically passable corridors may
be rejected rather than assumed safe.

Run `uv run pytest optimization/tests`. OR-Tools tests skip when the optional
solver is absent; fallback and nearest need no extra dependency. For benchmarks,
use identical request data and fixed `now`, changing only `solver`, and compare
assigned count, reported allocation points and world-meter travel rather than
assuming nearest achieves a feasible maximum matching.
