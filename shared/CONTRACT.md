# AEGIS integration contract — v1.0

Status: foundation proposal for team review. Owners: Prince (backend), AskAr
(CV/mapping), Muhammad (hardware), Hillary (frontend).

Only `GET /health` and `GET /api/v1/scenarios/demo` are implemented. Everything
marked **reserved** is a design agreement, not an available or secured feature.
There is no WebSocket server or dispatch execution in this foundation.

## Conventions

- HTTP and WebSocket payloads use JSON, snake_case, and UTF-8.
- API prefix: `/api/v1`; schema version: `"1.0"`. Breaking changes require a new
  major version and review by all affected owners. Do not silently rename fields.
- IDs are nonempty opaque strings, stable within a scenario. Casualty and responder
  IDs are unique across both collections. Never use a person's real name as an ID.
- Timestamps are timezone-aware ISO 8601 strings; producers should emit UTC `Z`.
- Every field listed below is required unless explicitly optional. Nullable fields
  must be present as `null` when unknown. Unlisted fields are rejected, including
  extra fields in nested objects. Unknown data must not be guessed.
- Coordinates are finite numbers in **meters**, not pixels or GPS degrees.
  `frame_id` identifies a calibrated local Cartesian frame; +x is right/east and
  +y is up/north on the shared map. The demo origin is the lower-left corner.
  All positions in a scenario must match its frame. Calibration/transforms are
  future CV/mapping work; do not combine unrelated frames.
- No real patient data, imagery, credentials, or device addresses in shared mocks.

## Shared entity schemas

Notation: `string` below means a nonempty string; `number` must be finite.
Arrays use `[Type]`. Enum values are case-sensitive.

### Position

| Field | Type | Meaning |
| --- | --- | --- |
| `frame_id` | string | Shared map frame |
| `x_m` | number | Local x coordinate in meters |
| `y_m` | number | Local y coordinate in meters |

### MedicalAssessment

| Field | Type | Meaning |
| --- | --- | --- |
| `severity` | `immediate` / `delayed` / `minor` / `expectant` / `deceased` | Human-entered label, not an automated diagnosis |
| `source` | `human` | No CV/model source is allowed |
| `assessed_by` | string | Identified human assessor |
| `assessed_at` | timestamp | Assessment time |

These are transport labels only, not clinical decision rules. The future backend
must bind `assessed_by` to an authorized authenticated human, never trust the field
alone. There is no assessment-write endpoint in this foundation.

### Casualty

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Casualty identifier |
| `position` | Position | Shared-frame location |
| `medical_assessment` | MedicalAssessment or null | `null` means **unassessed**, not low severity |

### Responder

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Responder identifier |
| `display_name` | string | Display label; synthetic in fixtures |
| `position` | Position | Shared-frame location |
| `status` | `available` / `busy` / `offline` | Availability, not dispatch authorization |

### CVObservation

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Observation identifier for deduplication |
| `source` | `cv` | Visual observation source |
| `observed_at` | timestamp | Capture/observation time |
| `position` | Position | Mapped person location |
| `kind` | `person_detected` | Detection only |
| `confidence` | number in [0, 1] | Detector confidence, never clinical certainty |

CVObservation has **no severity, triage, injury, medical assessment, or priority
field**. It does not create a medical assessment. Backend association of a detection
with a casualty is future work; observation IDs are not automatically casualty IDs.

### Scenario

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | `1.0` | Contract version |
| `id` | string | Scenario identifier |
| `name` | string | Scenario display label |
| `synthetic` | `true` | Foundation serves synthetic scenes only |
| `frame_id` | string | Frame shared by all entities |
| `created_at` | timestamp | Fixture creation time |
| `casualties` | [Casualty] | Scene casualties |
| `responders` | [Responder] | Scene responders |

Executable validation for these schemas lives in `backend/app/models.py`.
The shared example is `shared/mock/scenario.demo.json`: three casualties, two
available responders, and no medical assessments. Frontend/hardware/CV can read
this file without importing backend code.

## REST — implemented now

| Method | Path | Success body | Status |
| --- | --- | --- | --- |
| GET | `/health` | `{"status":"ok","service":"aegis-backend","version":"0.1.0"}` | 200 |
| GET | `/api/v1/scenarios/demo` | Scenario with `id: "demo"` | 200 |

Health is liveness only. Both routes are read-only and have no authentication;
run on loopback only. `/docs` and `/openapi.json` describe the available API.
Unknown paths return 404 with `{"detail":"Not Found"}`. Unsupported methods on
known routes return 405 with `{"detail":"Method Not Allowed"}`. Fixture loading
or validation failures are server errors, not successful partial scenarios.

## Coordination schemas — reserved

These schemas and endpoints are **not implemented**. Before enabling writes,
implement authentication, role authorization, durable transactional state,
idempotency, and an append-only audit trail.

| Schema | Required fields |
| --- | --- |
| Assignment | `casualty_id: string`, `responder_id: string` |
| ProposalRequest | `scenario_id: string` |
| DispatchProposal | `id: string`, `scenario_id: string`, `revision: integer >= 1`, `status: pending_approval / approved / rejected / expired`, `assignments: [Assignment]`, `created_at: timestamp`, `expires_at: timestamp` |
| ApprovalRequest | `proposal_revision: integer >= 1`, `decision: approve / reject` |
| ApprovalRecord | `id: string`, `proposal_id: string`, `proposal_revision: integer >= 1`, `decision: approve / reject`, `approved_by: string`, `decided_at: timestamp` |
| DispatchRequest | `proposal_id: string`, `proposal_revision: integer >= 1`, `approval_id: string` |
| DispatchRecord | `id: string`, `proposal_id: string`, `proposal_revision: integer >= 1`, `approval_id: string`, `status: dispatched`, `dispatched_at: timestamp` |

`approved_by` is derived from authenticated operator identity, never accepted from
the request body; it identifies the decision-maker for either decision. Proposal
assignments must reference existing entities in that scenario, use available
responders, and contain no duplicate casualty or responder IDs. The backend
revalidates current state, revision, and expiry at approval and at dispatch.

### Reserved REST endpoints

| Method | Path | Request | Success |
| --- | --- | --- | --- |
| POST | `/api/v1/observations` | CVObservation | 201, accepted CVObservation |
| PATCH | `/api/v1/responders/{responder_id}` | `{"position": Position, "status": available / busy / offline}` | 200, Responder |
| POST | `/api/v1/dispatch-proposals` | ProposalRequest | 201, pending DispatchProposal |
| POST | `/api/v1/dispatch-proposals/{proposal_id}/approval` | ApprovalRequest | 201, ApprovalRecord |
| POST | `/api/v1/dispatches` | DispatchRequest | 201, DispatchRecord |

Future ingest calls are scoped to an authenticated scenario/device; foreign
frames or IDs are rejected. For every POST, require an `Idempotency-Key` header:
the same key and body replay the stored response; a changed body returns 409.
Reserved error format remains `{"detail": "message"}` (FastAPI validation may
use an array for `detail`): 401 unauthenticated, 403 unauthorized, 404 unknown ID,
409 stale/conflicting/expired state, 422 invalid payload.

### Human approval gate

1. Recommendation creates a **pending_approval** proposal only; no device effect.
2. Authorized human reviews and explicitly approves or rejects the exact revision.
3. A dispatch request requires that stored approval and the unchanged proposal.
   Reject missing, rejected, expired, superseded, or already-consumed approvals.
4. Check and consume approval atomically with durable dispatch creation. Retries
   must not send duplicate commands. Use a transactional outbox/deduplicated device
   command path when hardware execution is added.
5. Only a successfully authorized dispatch may reach hardware. Events, telemetry,
   UI selection, CV detections, and recommendations never constitute approval.

Editing assignments requires a new revision and fresh approval. Until these
gates exist and are tested, **do not add a working dispatch path**.

## WebSocket — reserved, server-to-client only

Path: `/api/v1/ws`. Authenticate and authorize scenario subscriptions before
enabling it. WebSocket client messages cannot approve or dispatch; use the
authenticated REST flow. No route is exposed in the foundation.

Every event uses this envelope (all fields required):

```json
{
  "schema_version": "1.0",
  "event_id": "event-001",
  "type": "scenario.snapshot",
  "scenario_id": "demo",
  "occurred_at": "2026-11-13T12:00:00Z",
  "sequence": 1,
  "payload": {}
}
```

`event_id` is a unique string; `sequence` is a positive monotonically increasing
integer per scenario. The payload above is illustrative; actual payloads must
match the event table:

| Event type | Payload schema | Meaning |
| --- | --- | --- |
| `scenario.snapshot` | Scenario | Initial/resynchronization state |
| `observation.created` | CVObservation | Detection accepted, not clinical assessment |
| `responder.updated` | Responder | Position/availability change |
| `dispatch.proposed` | DispatchProposal | Advisory pending proposal only |
| `dispatch.decided` | ApprovalRecord | Human approval or rejection recorded |
| `dispatch.created` | DispatchRecord | Approved dispatch durably created, not device delivery confirmation |

Delivery is at-least-once; clients deduplicate `event_id`. On reconnect or a
sequence gap, obtain a fresh snapshot before applying further updates. A future
implementation must define durable replay/snapshot ordering before streaming
live updates. Never interpret an event as permission to dispatch.
