"""Grounded explanations: optional models may select facts, never author state."""

import json
import os
from typing import TYPE_CHECKING, Callable, Literal
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import Field

from backend.app.models import ContractModel, Identifier, Scenario

if TYPE_CHECKING:
    from optimization.models import OptimizationResult


class ExplanationFact(ContractModel):
    id: Identifier
    category: Literal["observed", "human_confirmed", "estimated", "unknown", "procedural"]
    text: Identifier


class ExplanationInput(ContractModel):
    mode: Literal["simulation"] = "simulation"
    facts: list[ExplanationFact]


class FactSelection(ContractModel):
    fact_ids: list[Identifier] = Field(min_length=1)


class Explanation(ContractModel):
    mode: Literal["simulation"] = "simulation"
    source: Literal["deterministic", "llm_fact_selection"]
    facts: list[ExplanationFact]
    fallback_reason: str | None = None


FactSelector = Callable[[ExplanationInput], dict]


def explanation_input(scenario: Scenario, result: "OptimizationResult") -> ExplanationInput:
    facts = [ExplanationFact(
        id="approval-required", category="procedural",
        text="SIMULATION ONLY. Recommendations require explicit human approval; no real dispatch is authorized.",
    )]
    for assignment in result.assignments:
        distance = assignment.route.distance_m if assignment.route is not None else None
        facts.append(ExplanationFact(
            id=f"assignment:{assignment.casualty_id}", category="estimated",
            text=(f"Recommend {assignment.responder_id} for {assignment.casualty_id}; "
                  f"planned route distance in meters: {distance}. This is not a movement-time guarantee."),
        ))
    for casualty in scenario.casualties:
        assessment = casualty.medical_assessment
        facts.append(ExplanationFact(
            id=f"assessment:{casualty.id}",
            category="unknown" if assessment is None else "human_confirmed",
            text=(f"{casualty.id} is unassessed; no medical severity is inferred."
                  if assessment is None else
                  f"{casualty.id}: human-entered label {assessment.severity} by {assessment.assessed_by}."),
        ))
    for casualty_id in result.unassigned_casualty_ids:
        reasons = "; ".join(result.reasons.get(casualty_id, [])) or "No feasible assignment supplied."
        facts.append(ExplanationFact(
            id=f"unassigned:{casualty_id}", category="unknown",
            text=f"{casualty_id} remains unassigned: {reasons}",
        ))
    facts.extend(ExplanationFact(
        id=f"rationale:{index}", category="estimated", text=text,
    ) for index, text in enumerate(result.rationale))
    return ExplanationInput(facts=facts)


def explain(inputs: ExplanationInput, selector: FactSelector | None = None) -> Explanation:
    if selector is None:
        return Explanation(source="deterministic", facts=inputs.facts)
    try:
        selection = FactSelection.model_validate(selector(inputs.model_copy(deep=True)))
        by_id = {fact.id: fact for fact in inputs.facts}
        if len(set(selection.fact_ids)) != len(selection.fact_ids):
            raise ValueError("Duplicate fact IDs")
        if any(fact_id not in by_id for fact_id in selection.fact_ids):
            raise ValueError("Unknown fact IDs")
        ordered = [by_id[fact_id] for fact_id in selection.fact_ids]
        ordered.extend(fact for fact in inputs.facts if fact.id not in selection.fact_ids)
        return Explanation(source="llm_fact_selection", facts=ordered)
    except Exception:
        return Explanation(
            source="deterministic", facts=inputs.facts,
            fallback_reason="Advisory provider unavailable or invalid; using verified template facts.",
        )


def selector_from_env() -> FactSelector | None:
    """Opt in to a provider-neutral JSON fact-ranking gateway; no vendor SDK required."""
    endpoint = os.getenv("AEGIS_ADVISORY_URL")
    if not endpoint:
        return None

    def select(inputs: ExplanationInput) -> dict:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("Advisory gateway must use HTTPS or loopback HTTP")
        headers = {"Content-Type": "application/json"}
        token = os.getenv("AEGIS_ADVISORY_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {
            "instruction": "Rank these supplied facts. Return only {\"fact_ids\":[...]}. Do not write new text.",
            "input": inputs.model_dump(mode="json"),
        }
        request = Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urlopen(request, timeout=2) as response:
            body = response.read(16385)
        if len(body) > 16384:
            raise ValueError("Advisory response too large")
        return json.loads(body)

    return select
