"""Optimization boundary models use the authoritative backend entity schemas."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from backend.app.models import Assignment, CVObservation, RecommendationMetadata, Scenario

ObjectivePolicy = Literal["distance_first", "stability_first", "bounded_travel_stability"]


class OptimizationRequest(BaseModel):
    """Solver input.

    ``previous_assignments`` are earlier UNAPPROVED recommendations and only act as
    soft stability preferences. Approved or acknowledged work belongs exclusively in
    ``active_assignments`` and is always hard reserved. ``assignment_exclusions`` are
    caller-policy reasons that block NEW work for a casualty; they never alter
    observations, assessments, positions or active work.
    """

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
    previous_assignments: list[Assignment] = Field(default_factory=list)
    objective_policy: ObjectivePolicy = "distance_first"
    stability_travel_budget_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    assignment_exclusions: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _budget_matches_policy(self) -> "OptimizationRequest":
        bounded = self.objective_policy == "bounded_travel_stability"
        if bounded and self.stability_travel_budget_m is None:
            raise ValueError("bounded_travel_stability requires stability_travel_budget_m")
        if not bounded and self.stability_travel_budget_m is not None:
            raise ValueError("stability_travel_budget_m is only valid for bounded_travel_stability")
        return self


class ObjectiveTier(BaseModel):
    """One measured objective tier of the actual returned assignments.

    ``proven_optimal`` is true only when CP-SAT proved every stage optimal; fallback
    and nearest engines report measured values without any optimality claim.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    direction: Literal["maximize", "minimize"]
    value: float
    proven_optimal: bool


class PreviousAssignmentOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    casualty_id: str
    responder_id: str
    outcome: Literal["retained", "changed_responder", "not_reassigned", "blocked_active_casualty",
                     "blocked_active_responder", "blocked_infeasible"]
    reasons: list[str] = Field(default_factory=list)


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
    objective_policy: ObjectivePolicy = "distance_first"
    objective_tiers: list[ObjectiveTier] = Field(default_factory=list)
    previous_assignment_outcomes: list[PreviousAssignmentOutcome] = Field(default_factory=list)
    applied_assignment_exclusions: dict[str, list[str]] = Field(default_factory=dict)
    stability_budget_enforced: bool = False
