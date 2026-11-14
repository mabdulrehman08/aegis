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
    CVTrack,
    Identifier,
    Position,
    RecommendationMetadata,
    Scenario,
)

SimulationMode = Literal["simulation"]
PositiveRevision = Annotated[int, Field(ge=1, strict=True)]
CapabilityTag = Annotated[str, Field(min_length=1, pattern=r"\S")]


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


NonblankIdentifier = Annotated[str, Field(min_length=1, pattern=r"\S")]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class CameraRegistration(ContractModel):
    """SIMULATION camera mapping. ``calibration_ref`` is a human/external
    assertion that a world calibration exists; the backend computes nothing."""

    mode: SimulationMode
    actor: Identifier
    mapping_mode: Literal["calibrated_world", "predefined_zones"]
    calibration_ref: NonblankIdentifier | None = None
    zones: dict[NonblankIdentifier, Position] | None = None

    @model_validator(mode="after")
    def validate_mapping(self) -> "CameraRegistration":
        if self.mapping_mode == "calibrated_world":
            if self.calibration_ref is None:
                raise ValueError("calibrated_world requires a nonempty calibration_ref")
            if self.zones is not None:
                raise ValueError("calibrated_world cameras must not define zones")
        elif not self.zones:
            raise ValueError("predefined_zones requires at least one named zone Position")
        return self


class CameraObservation(ContractModel):
    """Camera-local detection report; exactly one of position / zone_id.

    No severity or clinical fields exist; ``source``/``kind`` are implied
    (``cv`` / ``person_detected``)."""

    mode: SimulationMode
    id: Identifier
    observed_at: AwareDatetime
    confidence: Confidence
    track: CVTrack
    position: Position | None = None
    zone_id: NonblankIdentifier | None = None

    @model_validator(mode="after")
    def validate_location(self) -> "CameraObservation":
        if (self.position is None) == (self.zone_id is None):
            raise ValueError("Provide exactly one of position or zone_id")
        if self.track.last_seen_at > self.observed_at:
            raise ValueError("last_seen_at must not follow observed_at")
        return self


class CameraRecord(ContractModel):
    mode: SimulationMode = "simulation"
    camera_id: Identifier
    epoch: int
    mapping_mode: Literal["calibrated_world", "predefined_zones"]
    calibration_ref: str | None
    zones: dict[str, Position] | None
    registered_by: Identifier
    registered_at: AwareDatetime


class CameraProvenance(ContractModel):
    """Where a track's latest location came from. ``zone_estimate`` positions
    are the registered zone's reference point, not an observed coordinate."""

    camera_id: Identifier
    local_observation_id: Identifier
    local_track_id: Identifier
    mapping_mode: Literal["calibrated_world", "predefined_zones"]
    location_basis: Literal["calibrated_world", "zone_estimate"]
    zone_id: str | None
    calibration_ref: str | None


class TrackRecord(ContractModel):
    track_id: Identifier
    casualty_id: Identifier | None
    latest_observation: CVObservation
    observation_count: int
    camera: CameraProvenance | None = None


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
    location_provenance: dict[str, CameraProvenance] = Field(default_factory=dict)
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
    location_provenance: dict[str, CameraProvenance] = Field(default_factory=dict)
    location_rationale: list[str] = Field(default_factory=list)
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
    cameras: list[CameraRecord] = Field(default_factory=list)
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
