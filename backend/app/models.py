"""Typed v1 schemas for the read-only development API."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


Identifier = Annotated[str, Field(min_length=1)]
FiniteCoordinate = Annotated[float, Field(allow_inf_nan=False)]


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


class CVObservation(ContractModel):
    id: Identifier
    source: Literal["cv"]
    observed_at: AwareDatetime
    position: Position
    kind: Literal["person_detected"]
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class Scenario(ContractModel):
    schema_version: Literal["1.0"]
    id: Identifier
    name: Identifier
    synthetic: Literal[True]
    frame_id: Identifier
    created_at: AwareDatetime
    casualties: list[Casualty]
    responders: list[Responder]

    @model_validator(mode="after")
    def validate_entities(self) -> "Scenario":
        entities = [*self.casualties, *self.responders]
        identifiers = [entity.id for entity in entities]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Scenario entity IDs must be unique")
        if any(entity.position.frame_id != self.frame_id for entity in entities):
            raise ValueError("All positions must use the scenario coordinate frame")
        return self


class HealthResponse(ContractModel):
    status: Literal["ok"]
    service: Literal["aegis-backend"]
    version: Literal["0.1.0"]
