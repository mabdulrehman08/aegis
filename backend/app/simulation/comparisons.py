"""SIMULATION counterfactual comparison schemas over ``optimization.analysis``.

Comparisons are read-only: they rerun the real solver on detached copies of
one captured request and never install proposals, approve, or dispatch.
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from backend.app.models import ContractModel, Identifier
from backend.app.simulation.models import (
    INTELLIGENCE_INTERPRETATION,
    ComparisonSource,
    IntelligenceSettings,
    SimulationMode,
    SimulationRequest,
)
from optimization.analysis import MAX_SCENARIOS, CounterfactualAnalysis, CounterfactualScenario

COMPARISON_LIMITATION = (
    "Each scenario is a hypothetical rerun of the real solver on an explicitly changed copy of "
    "the captured request. When context is stale the live scene has moved on: rerun the "
    "comparison; never act on a stale result.")


class ComparisonRequest(SimulationRequest):
    """At most ``MAX_SCENARIOS`` scenarios (plus one baseline solve).

    ``policy`` overrides the scenario's current intelligence settings for the
    captured base request only; it never changes stored policy. ``solver``
    overrides the store's default engine for every run.
    """

    scenarios: Annotated[list[CounterfactualScenario],
                         Field(min_length=1, max_length=MAX_SCENARIOS)]
    policy: IntelligenceSettings | None = None
    solver: Literal["ortools", "fallback", "nearest"] | None = None


class ComparisonReport(ContractModel):
    """``context`` is ``stale`` when the epoch or scene revision changed, or the
    captured ``source_valid_until`` passed, before results were returned."""

    mode: SimulationMode = "simulation"
    schema_version: Literal["1.0"] = "1.0"
    scenario_id: Identifier
    read_only: Literal[True] = True
    installs_proposal: Literal[False] = False
    authorizes_dispatch: Literal[False] = False
    source: ComparisonSource
    evaluated_at: AwareDatetime
    context: Literal["current", "stale"]
    stale_reasons: list[Literal["epoch_changed", "scene_revision_changed",
                                "source_validity_horizon_reached"]]
    analysis: CounterfactualAnalysis
    limitations: list[str] = Field(default_factory=lambda: [COMPARISON_LIMITATION])
    interpretation: str = INTELLIGENCE_INTERPRETATION
