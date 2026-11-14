"""Optimization boundary models use the authoritative backend entity schemas."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from backend.app.models import Assignment, CVObservation, RecommendationMetadata, Scenario


class OptimizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Scenario
    required_capabilities: dict[str, list[str]] = Field(default_factory=dict)
    active_assignments: list[Assignment] = Field(default_factory=list)
    observations: dict[str, CVObservation] = Field(default_factory=dict)
    scene_revision: int = Field(default=1, gt=0)
    now: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    solver: Literal["ortools", "fallback", "nearest"] = "ortools"
    freshness_seconds: float = Field(default=30, ge=0, allow_inf_nan=False)
    min_confidence: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    position_tolerance_m: float = Field(default=0.25, ge=0, allow_inf_nan=False)


class OptimizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments: list[Assignment]
    preserved_assignments: list[Assignment] = Field(default_factory=list)
    unassigned_casualty_ids: list[str]
    reasons: dict[str, list[str]]
    metadata: RecommendationMetadata
    runtime_ms: float
    scene_revision: int
    map_revision: str | None
    rationale: list[str]
    objective_values: dict[str, float]
    engine: str
