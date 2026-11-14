"""Typed v1 payloads; reserved schemas do not enable integration features."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


Identifier = Annotated[str, Field(min_length=1)]
FiniteCoordinate = Annotated[float, Field(allow_inf_nan=False)]
NonnegativeNumber = Annotated[float, Field(ge=0, allow_inf_nan=False)]
PositiveInteger = Annotated[int, Field(gt=0, strict=True)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Position(ContractModel):
    frame_id: Identifier
    x_m: FiniteCoordinate
    y_m: FiniteCoordinate


class MedicalAssessment(ContractModel):
    severity: Literal["immediate", "delayed", "minor", "expectant", "deceased"]
    source: Literal["human"]
    assessed_by: Identifier
    assessed_at: AwareDatetime


class Casualty(ContractModel):
    id: Identifier
    position: Position
    medical_assessment: MedicalAssessment | None


class Responder(ContractModel):
    id: Identifier
    display_name: Identifier
    position: Position
    status: Literal["available", "busy", "offline"]
    capabilities: list[Identifier] = Field(default_factory=list)


class CVTrack(ContractModel):
    track_id: Identifier
    state: Literal["visible", "occluded", "lost"]
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> "CVTrack":
        if self.first_seen_at > self.last_seen_at:
            raise ValueError("first_seen_at must not follow last_seen_at")
        return self


class CVObservation(ContractModel):
    id: Identifier
    source: Literal["cv"]
    observed_at: AwareDatetime
    position: Position
    kind: Literal["person_detected"]
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    track: CVTrack | None = None

    @model_validator(mode="after")
    def validate_track_time(self) -> "CVObservation":
        if self.track is not None and self.track.last_seen_at > self.observed_at:
            raise ValueError("last_seen_at must not follow observed_at")
        return self


class Obstacle(ContractModel):
    id: Identifier
    vertices: Annotated[list[Position], Field(min_length=3)]


class OccupancyGrid(ContractModel):
    origin: Position
    resolution_m: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    width: PositiveInteger
    height: PositiveInteger
    cells: list[Annotated[int, Field(strict=True)]]

    @model_validator(mode="after")
    def validate_cell_count(self) -> "OccupancyGrid":
        if len(self.cells) != self.width * self.height:
            raise ValueError("cells must contain width * height entries")
        if any(cell not in {-1, 0, 100} for cell in self.cells):
            raise ValueError("cells must contain only -1, 0, or 100")
        return self


class SceneMap(ContractModel):
    frame_id: Identifier
    revision: Identifier
    obstacles: list[Obstacle]
    occupancy_grid: OccupancyGrid | None

    @model_validator(mode="after")
    def validate_map(self) -> "SceneMap":
        identifiers = [obstacle.id for obstacle in self.obstacles]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Obstacle IDs must be unique")
        positions = [vertex for obstacle in self.obstacles for vertex in obstacle.vertices]
        if self.occupancy_grid is not None:
            positions.append(self.occupancy_grid.origin)
        if any(position.frame_id != self.frame_id for position in positions):
            raise ValueError("Map positions must use the map coordinate frame")
        return self


class AStarRoute(ContractModel):
    algorithm: Literal["astar"]
    map_revision: Identifier
    waypoints: Annotated[list[Position], Field(min_length=2)]
    distance_m: NonnegativeNumber

    @model_validator(mode="after")
    def validate_route_frame(self) -> "AStarRoute":
        if any(point.frame_id != self.waypoints[0].frame_id for point in self.waypoints):
            raise ValueError("Route waypoints must use one coordinate frame")
        return self


class RecommendationMetadata(ContractModel):
    solver_name: Identifier
    solver_version: Identifier
    generated_at: AwareDatetime
    objective: Identifier
    status: Literal["feasible", "partial", "infeasible"]
    unassigned_casualty_ids: list[Identifier]
    objective_value: FiniteCoordinate | None = None


class Assignment(ContractModel):
    casualty_id: Identifier
    responder_id: Identifier
    route: AStarRoute | None = None


class DispatchProposal(ContractModel):
    id: Identifier
    scenario_id: Identifier
    revision: PositiveInteger
    status: Literal["pending_approval", "approved", "rejected", "expired"]
    assignments: list[Assignment]
    created_at: AwareDatetime
    expires_at: AwareDatetime
    recommendation: RecommendationMetadata | None = None


class DeviceAssignmentEvent(ContractModel):
    id: Identifier
    scenario_id: Identifier
    device_id: Identifier
    dispatch_id: Identifier
    command_id: Identifier
    casualty_id: Identifier
    responder_id: Identifier
    kind: Literal["delivered", "acknowledged", "completed"]
    occurred_at: AwareDatetime
    mode: Literal["simulation", "real"]


class Scenario(ContractModel):
    schema_version: Literal["1.0"]
    id: Identifier
    name: Identifier
    synthetic: Literal[True]
    frame_id: Identifier
    created_at: AwareDatetime
    casualties: list[Casualty]
    responders: list[Responder]
    scene_map: SceneMap | None = None

    @model_validator(mode="after")
    def validate_entities(self) -> "Scenario":
        entities = [*self.casualties, *self.responders]
        identifiers = [entity.id for entity in entities]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Scenario entity IDs must be unique")
        if any(entity.position.frame_id != self.frame_id for entity in entities):
            raise ValueError("All positions must use the scenario coordinate frame")
        if self.scene_map is not None and self.scene_map.frame_id != self.frame_id:
            raise ValueError("Scene map must use the scenario coordinate frame")
        return self


class HealthResponse(ContractModel):
    status: Literal["ok"]
    service: Literal["aegis-backend"]
    version: Literal["0.1.0"]
