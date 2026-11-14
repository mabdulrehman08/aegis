import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.main import create_app
from backend.app.models import CVObservation, MedicalAssessment, Scenario
from backend.app.services.scenarios import DEMO_SCENARIO_PATH, load_demo_scenario


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def scenario_data():
    return json.loads(DEMO_SCENARIO_PATH.read_text(encoding="utf-8"))


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "service": "aegis-backend", "version": "0.1.0"
    }


def test_demo_matches_shared_fixture(client, scenario_data):
    response = client.get("/api/v1/scenarios/demo")
    assert response.status_code == 200
    assert response.json() == scenario_data
    scenario = Scenario.model_validate(response.json())
    assert len(scenario.casualties) == 3
    assert len(scenario.responders) == 2
    assert all(casualty.medical_assessment is None for casualty in scenario.casualties)
    assert all(responder.status == "available" for responder in scenario.responders)


def test_fixture_loads_outside_repository_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_demo_scenario().id == "demo"


def test_demo_requests_do_not_mutate_shared_state(client):
    scenario = client.get("/api/v1/scenarios/demo").json()
    scenario["casualties"].clear()
    assert len(client.get("/api/v1/scenarios/demo").json()["casualties"]) == 3


def test_openapi_exposes_only_foundation_routes(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert set(response.json()["paths"]) == {"/health", "/api/v1/scenarios/demo"}


@pytest.mark.parametrize("path", [
    "/api/v1/observations",
    "/api/v1/dispatch-proposals",
    "/api/v1/dispatch-proposals/proposal-001/approval",
    "/api/v1/dispatches",
])
def test_reserved_write_endpoints_are_unavailable(client, path):
    assert client.post(path, json={}).status_code == 404


def test_demo_is_read_only(client):
    assert client.post("/api/v1/scenarios/demo", json={}).status_code == 405
    assert client.get("/api/v1/scenarios/missing").status_code == 404


def test_scenario_rejects_duplicate_entity_ids(scenario_data):
    scenario_data["responders"][0]["id"] = scenario_data["casualties"][0]["id"]
    with pytest.raises(ValidationError, match="IDs must be unique"):
        Scenario.model_validate(scenario_data)


def test_scenario_rejects_mixed_coordinate_frames(scenario_data):
    scenario_data["casualties"][0]["position"]["frame_id"] = "camera-pixels"
    with pytest.raises(ValidationError, match="coordinate frame"):
        Scenario.model_validate(scenario_data)


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), -float("inf")])
def test_scenario_rejects_nonfinite_coordinates(scenario_data, coordinate):
    scenario_data["casualties"][0]["position"]["x_m"] = coordinate
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario_data)


def test_scenario_rejects_naive_timestamp(scenario_data):
    scenario_data["created_at"] = "2026-11-13T12:00:00"
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario_data)


def test_cv_observation_has_no_clinical_fields():
    observation = {
        "id": "observation-001",
        "source": "cv",
        "observed_at": "2026-11-13T12:00:00Z",
        "position": {"frame_id": "demo-floor-1", "x_m": 4, "y_m": 8},
        "kind": "person_detected",
        "confidence": 0.9,
    }
    assert CVObservation.model_validate(observation).confidence == 0.9
    for forbidden in ["severity", "triage", "injury", "medical_assessment", "priority"]:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            CVObservation.model_validate({**observation, forbidden: "immediate"})
    for invalid_confidence in [-0.1, 1.1, float("nan")]:
        with pytest.raises(ValidationError):
            CVObservation.model_validate({**observation, "confidence": invalid_confidence})


def test_medical_assessment_requires_identified_human():
    assessment = {
        "severity": "delayed",
        "source": "human",
        "assessed_by": "operator-001",
        "assessed_at": "2026-11-13T12:00:00Z",
    }
    assert MedicalAssessment.model_validate(assessment).source == "human"
    for invalid in [{"source": "cv"}, {"assessed_by": ""}, {"assessed_by": None}]:
        with pytest.raises(ValidationError):
            MedicalAssessment.model_validate({**assessment, **invalid})
