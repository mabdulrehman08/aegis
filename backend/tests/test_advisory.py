import pytest

from backend.app.services.advisory import (
    ExplanationFact, ExplanationInput, explain, explanation_input, selector_from_env,
)
from backend.app.services.scenarios import load_demo_scenario
from optimization.models import OptimizationRequest
from optimization.solver import optimize


@pytest.fixture
def facts():
    return ExplanationInput(facts=[
        ExplanationFact(id="approval", category="procedural", text="Human approval required."),
        ExplanationFact(id="unknown", category="unknown", text="Casualty is unassessed."),
    ])


def test_explanations_work_without_llm(facts):
    result = explain(facts)
    assert result.source == "deterministic"
    assert result.facts == facts.facts


def test_model_can_only_reorder_verified_facts_and_cannot_hide_unknowns(facts):
    result = explain(facts, lambda inputs: {"fact_ids": ["unknown"]})
    assert result.source == "llm_fact_selection"
    assert {fact.id for fact in result.facts} == {"approval", "unknown"}


@pytest.mark.parametrize("response", [
    {"fact_ids": ["invented"]},
    {"fact_ids": ["unknown", "unknown"]},
    {"fact_ids": []},
    {"fact_ids": ["unknown"], "diagnosis": "fabrication"},
    {"dispatch": True},
])
def test_invalid_model_output_cannot_change_state_or_text(facts, response):
    result = explain(facts, lambda inputs: response)
    assert result.source == "deterministic"
    assert result.facts == facts.facts


def test_provider_failure_uses_template(facts):
    def unavailable(inputs):
        raise TimeoutError("offline")
    assert explain(facts, unavailable).fallback_reason


def test_provider_cannot_mutate_authoritative_facts(facts):
    def mutate(inputs):
        inputs.facts[0].text = "dispatch now"
        return {"fact_ids": ["approval"]}
    assert explain(facts, mutate).facts[0].text == "Human approval required."


def test_no_provider_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("AEGIS_ADVISORY_URL", raising=False)
    assert selector_from_env() is None


def test_actual_optimizer_explanation_preserves_unknown_assessments():
    scenario = load_demo_scenario()
    before = scenario.model_dump(mode="json")
    result = optimize(OptimizationRequest(scenario=scenario))
    explanation = explain(explanation_input(scenario, result))
    assessment_facts = [fact for fact in explanation.facts if fact.id.startswith("assessment:")]
    assert len(assessment_facts) == 3
    assert all(fact.category == "unknown" and "unassessed" in fact.text for fact in assessment_facts)
    assert any(fact.id == "approval-required" for fact in explanation.facts)
    assert scenario.model_dump(mode="json") == before


def test_invalid_gateway_configuration_falls_back_without_network(facts, monkeypatch):
    monkeypatch.setenv("AEGIS_ADVISORY_URL", "http://not-loopback.example/advisory")
    result = explain(facts, selector_from_env())
    assert result.source == "deterministic"
    assert result.facts == facts.facts
