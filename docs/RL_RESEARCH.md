# Reinforcement learning remains outside the live MVP

The working demo engine remains A* plus constrained OR-Tools allocation.
Prince's technical-depth directive authorizes a **separate synthetic PPO
experiment only after uncertainty and advanced optimization pass their tests**.
It does not authorize a learned policy in the live dispatch pathway or any
historical dataset processing. Research must not compromise demo reliability.
A policy must never learn or bypass medical/approval gates.

## Potential data and what is actually available

Only metadata pages were inspected during implementation, not full datasets:

- [NYC EMS Incident Dispatch Data](https://data.cityofnewyork.us/Public-Safety/EMS-Incident-Dispatch-Data/76xm-jjuj):
  metadata includes incident/assignment/activation/on-scene timestamps, dispatch
  and incident response durations, call types, severity codes, and geographic
  areas. Potential use: arrival and response-time distribution calibration.
  Call severity codes are not indoor casualty assessments and must not be used
  to infer a tracked person's medical condition.
- [Seattle Real Time Fire 911 Calls](https://cos-data.seattle.gov/Public-Safety/Seattle-Real-Time-Fire-911-Calls/kzjm-xkqj):
  metadata includes incident number, type, datetime, address, latitude, and
  longitude. Potential use: incident-demand patterns, not indoor responder paths.
- [NEMSIS research access](https://nemsis.org/using-ems-data/request-research-data/):
  a research-data request/access process, not an already provisioned local
  training corpus. Review access terms, privacy restrictions, variable definitions,
  population coverage, and project suitability before any data acquisition.

None supplies the complete joint indoor obstacle map, tracking uncertainty,
responder actions, and counterfactual outcomes needed for a PPO environment.
Historical dispatch records alone are not an interactive training environment.
No claims of clinical benefit, mortality reduction, or ready-made PPO data follow.

## Experimental Gymnasium-compatible environment requirements

- `reset(seed, options)` creates a fully synthetic scenario and reproducible RNG.
  Observation: schema-backed scene/map revision, responder capabilities/status,
  task requirements, observed track age/confidence, human-confirmed assessments,
  active tasks, and explicit unknown masks. No privileged medical truth is exposed.
- Action: propose a candidate casualty/responder matching or request reobservation.
  The action space masks infeasible pairs using the same routing/capability and
  active-assignment checks as the current solver. The policy cannot approve tasks.
- `step(action)` advances a simulated clock, responder movement, occlusion,
  obstacle updates, and synthetic human decisions. Human approval is an external
  transition, never a policy-controlled shortcut.
- Reward: explicit, separately reported components for feasible service, travel,
  waiting, unnecessary reassignment, and invalid actions. Human-confirmed urgency
  may be evaluated under an approved policy, not learned from detector confidence.
  Coefficients and tradeoffs require review; no mortality proxy is asserted.
- Termination: all tasks completed, no pending work, or bounded simulation horizon.
  Time limits truncate episodes rather than fabricate successful completion.
- Output follows Gymnasium's `(observation, reward, terminated, truncated, info)`
  shape, with reproducibility metadata, constraint violations, and provenance.

## Future interchangeable policy boundary

The existing `OptimizationRequest -> OptimizationResult` boundary separates
scenario inputs from solver computation and transport. A future experimental
policy can implement that pure boundary offline, but all proposed assignments
must pass the exact same independent feasibility validator, revision checks,
human review, and simulation dispatch gate before becoming executable.
Use held-out fixed seeds, consistent baseline feasibility, and confidence
intervals before claiming improvements. Record failures, not just mean reward.

The core backend dependency set has no Gymnasium, PPO trainer, learned weights,
or policy hook into live dispatch. An experiment must use its own dependencies,
held-out fixed seeds, measured reports, and separate review branch. A trained
policy is not evidence of clinical safety or permission to dispatch. If an
experiment underperforms, report that result and retain the constrained solver.
