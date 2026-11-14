# Additive simulation contract proposal — review separately from production

`shared/CONTRACT.md` remains unchanged and authoritative for production-reserved
schemas and endpoints. This implementation proposes a **separate local simulation
namespace**, not an implicit weakening or implementation of production dispatch.
Review this additive boundary with all affected teammates before adopting it.

## Isolation and compatibility

| Foundation/production-reserved interface | Local simulation proposal |
| --- | --- |
| `/api/v1/scenarios/demo` remains immutable | `/api/v1/simulations/{scenario_id}/...` is process-local mutable state |
| `/api/v1/dispatches` remains unavailable | Simulation dispatch changes local assignment records only |
| `/api/v1/ws` remains unavailable | Simulation-scoped WebSocket carries explicitly labelled mock state |
| DeviceAssignmentEvent permits future `real` mode | SimulatedDeviceEvent accepts only `simulation` |
| Production proposal/approval schemas remain unchanged | Separate SimulationProposal/SimulationApproval records add local lifecycle diagnostics |
| Authenticated human identity is required in a future real system | `actor`/`operator_id` are claimed local demo labels, explicitly `authenticated: false` |
| Durable atomic dispatch/outbox/audit required before real commands | In-memory locked transitions demonstrate semantics, not durability or physical delivery |

The original read-only fixture and its payload are preserved. Shared Position,
Scenario, CVObservation, MedicalAssessment, Assignment, map, and route schemas
are reused, not renamed. Additional simulation records live in
`backend/app/simulation/models.py`; actual routes/examples are in `INTEGRATION.md`.

## Local events and state

Simulation envelopes retain the shared core fields (`schema_version`, `event_id`,
`type`, `scenario_id`, `occurred_at`, `sequence`, `payload`) and add the required
output label `mode: simulation`. Their payloads can include the richer local
SimulationState/SimulationProposal records; they are not wire-compatible with a
strict production-only consumer. Never forward them to a production topic.

`epoch` distinguishes resets; `scene_revision` identifies optimization assumptions.
Events and snapshots expose sequence checkpoints for deduplication and recovery.
Consumers must adopt the local schemas explicitly, keep the simulation banner
visible, and recover from a fresh authoritative snapshot after gaps or resets.
Server-side events are notifications, not client commands or authorizations.

Local proposal lifecycle additionally describes supersession, dispatch, and
closure reasons without changing the reserved DispatchProposal enum. Approval
and execution records always retain simulation provenance. No device completion
or acknowledgement can create a human approval or alter medical assessments.

## Before any real integration

Teammates may adapt synthetic inputs and UI subscriptions to this namespace now.
Actual device transport, authenticated operator/device identity, persistence,
durable idempotency, tamper-resistant auditing, and production safety review are
separate work. A future production change requires a dedicated shared-contract
review and explicit authorization; this PR cannot authorize real dispatch.
