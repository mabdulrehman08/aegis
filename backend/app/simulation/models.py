"""Simulation request/response schemas. Every payload is explicitly SIMULATION.

These models extend, and never replace, the contract entities in
``backend.app.models``. Simulation approvals are local operator decisions with
no authentication claim and can never satisfy a real dispatch gate.
"""

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from backend.app.models import (
    AStarRoute,
    Assignment,
    ContractModel,
    CVObservation,
    Identifier,
    Position,
    RecommendationMetadata,
    Scenario,
)

SimulationMode = Literal["simulation"]
PositiveRevision = Annotated[int, Field(ge=1, strict=True)]
CapabilityTag = Annotated[str, Field(min_length=1, pattern=r"\S")]


# --- Requests -----------------------------------------------------------------


class SimulationRequest(ContractModel):
    """Optional ``mode`` must be ``simulation``; ``real`` is rejected with 422."""

    mode: SimulationMode = "simulation"


class ResetRequest(SimulationRequest):
    """Reset a session. Without ``scenario`` only ``demo`` is loadable.

    A supplied scenario must be synthetic and its ``id`` must match the path.
    ``add_demo_map`` adds the known synthetic 24x20 grid when no map is present.
    """

    scenario: Scenario | None = None
    required_capabilities: dict[Identifier, list[CapabilityTag]] = Field(default_factory=dict)
    add_demo_map: bool = True


class AssociationRequest(SimulationRequest):
    """Bind a CV track to an existing casualty, or create a confirmed casualty."""

    track_id: Identifier
    casualty_id: Identifier
    create_casualty: bool = False
    actor: Identifier


class ResponderUpdateRequest(SimulationRequest):
    position: Position | None = None
    status: Literal["available", "busy", "offline"] | None = None
    capabilities: list[CapabilityTag] | None = None
    reported_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_change(self) -> "ResponderUpdateRequest":
        if self.position is None and self.status is None and self.capabilities is None:
            raise ValueError("Provide at least one of position, status, capabilities")
        return self


class RequiredCapabilitiesRequest(SimulationRequest):
    capabilities: list[CapabilityTag]
    actor: Identifier


class ReplanRequest(SimulationRequest):
    actor: Identifier
    reason: Identifier | None = None
    solver: Literal["ortools", "fallback", "nearest"] | None = None


class DecisionRequest(SimulationRequest):
    """Human decision on the exact proposal revision. ``actor`` is the local
    operator ID; it is recorded, not authenticated."""

    proposal_revision: PositiveRevision
    decision: Literal["approve", "reject"]
    actor: Identifier


class DispatchRequest(SimulationRequest):
    proposal_id: Identifier
    proposal_revision: PositiveRevision
    approval_id: Identifier
    actor: Identifier


class SimulatedDeviceEvent(ContractModel):
    """Contract DeviceAssignmentEvent restricted to ``mode: simulation``."""

    id: Identifier
    scenario_id: Identifier
    device_id: Identifier
    dispatch_id: Identifier
    command_id: Identifier
    casualty_id: Identifier
    responder_id: Identifier
    kind: Literal["delivered", "acknowledged", "completed"]
    occurred_at: AwareDatetime
    mode: SimulationMode


class SimulateDeviceRequest(SimulationRequest):
    """Convenience: the backend builds the correlated SimulatedDeviceEvent."""

    kind: Literal["delivered", "acknowledged", "completed"]
    event_id: Identifier | None = None
    occurred_at: AwareDatetime | None = None


# --- State records ------------------------------------------------------------


class TrackRecord(ContractModel):
    track_id: Identifier
    casualty_id: Identifier | None
    latest_observation: CVObservation
    observation_count: int


class SimulationProposal(ContractModel):
    """Advisory proposal. ``revision`` is the scene revision it was computed on;
    approval and dispatch must quote it exactly. Contains NEW assignments only."""

    mode: SimulationMode = "simulation"
    id: Identifier
    scenario_id: Identifier
    epoch: int
    revision: PositiveRevision
    status: Literal["pending_approval", "approved", "rejected", "expired", "superseded",
                    "dispatched"]
    assignments: list[Assignment]
    preserved_assignments: list[Assignment]
    unassigned_casualty_ids: list[Identifier]
    reasons: dict[str, list[str]]
    rationale: list[str]
    engine: str
    runtime_ms: float
    map_revision: str | None
    objective_values: dict[str, float]
    recommendation: RecommendationMetadata | None
    created_at: AwareDatetime
    expires_at: AwareDatetime
    approval_id: Identifier | None = None
    dispatch_id: Identifier | None = None
    closed_reason: str | None = None


class SimulationApproval(ContractModel):
    mode: SimulationMode = "simulation"
    id: Identifier
    proposal_id: Identifier
    proposal_revision: PositiveRevision
    decision: Literal["approve", "reject"]
    approved_by: Identifier
    operator_id: Identifier
    authenticated: Literal[False] = False
    decided_at: AwareDatetime
    expires_at: AwareDatetime
    status: Literal["active", "consumed", "invalidated", "expired", "recorded"]
    invalidated_reason: str | None = None


class SimulationDispatch(ContractModel):
    mode: SimulationMode = "simulation"
    id: Identifier
    proposal_id: Identifier
    proposal_revision: PositiveRevision
    approval_id: Identifier
    status: Literal["dispatched"] = "dispatched"
    dispatched_by: Identifier
    dispatched_at: AwareDatetime
    assignment_ids: list[Identifier]
    real_device_effect: Literal[False] = False


class SimulatedAssignment(ContractModel):
    mode: SimulationMode = "simulation"
    id: Identifier
    dispatch_id: Identifier
    command_id: Identifier
    device_id: Identifier
    casualty_id: Identifier
    responder_id: Identifier
    route: AStarRoute | None
    status: Literal["dispatched", "delivered", "acknowledged", "completed"]
    created_at: AwareDatetime
    delivered_at: AwareDatetime | None = None
    acknowledged_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    device_event_ids: list[Identifier] = Field(default_factory=list)


class RecommendationState(ContractModel):
    """Most recent solver outcome, including runs that produced no proposal."""

    scene_revision: int
    computed_at: AwareDatetime
    trigger: str
    proposal_id: Identifier | None
    engine: str | None
    unassigned_casualty_ids: list[Identifier]
    reasons: dict[str, list[str]]
    error: str | None = None


class SimulationState(ContractModel):
    """Authoritative snapshot (a copy). Apply only events with a greater sequence."""

    mode: SimulationMode = "simulation"
    schema_version: Literal["1.0"] = "1.0"
    scenario_id: Identifier
    epoch: int
    scene_revision: PositiveRevision
    sequence: int
    generated_at: AwareDatetime
    scenario: Scenario
    required_capabilities: dict[str, list[str]]
    tracks: list[TrackRecord]
    resolved_casualty_ids: list[Identifier]
    current_proposal_id: Identifier | None
    proposals: list[SimulationProposal]
    approvals: list[SimulationApproval]
    dispatches: list[SimulationDispatch]
    assignments: list[SimulatedAssignment]
    last_recommendation: RecommendationState | None


class ActionResponse(ContractModel):
    """Every mutation returns its record plus the post-mutation snapshot.

    ``duplicate`` is true when an idempotent replay or deduplicated ID returned
    the originally stored result.
    """

    mode: SimulationMode = "simulation"
    action: Identifier
    duplicate: bool = False
    result: dict[str, Any] | None
    state: SimulationState


class EventEnvelope(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    mode: SimulationMode = "simulation"
    event_id: Identifier
    type: Identifier
    scenario_id: Identifier
    occurred_at: AwareDatetime
    sequence: PositiveRevision
    payload: dict[str, Any]


class EventPage(ContractModel):
    mode: SimulationMode = "simulation"
    scenario_id: Identifier
    events: list[EventEnvelope]
    latest_sequence: int
    oldest_retained_sequence: int | None
    gap: bool
