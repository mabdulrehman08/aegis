import json
from pathlib import Path
import re

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models import CVObservation, MedicalAssessment
from backend.app.simulation.models import (
    AssociationRequest,
    CameraObservation,
    CameraRegistration,
    DecisionRequest,
    DispatchRequest,
    SimulatedDeviceEvent,
    SimulationState,
)


def test_documented_teammate_payload_templates_match_schemas():
    document = Path(__file__).resolve().parents[2] / "docs" / "INTEGRATION.md"
    examples = re.findall(r"```json\n(.*?)\n```", document.read_text(), re.S)
    schemas = [CVObservation, AssociationRequest,
               CameraRegistration, CameraObservation, CameraRegistration, CameraObservation,
               MedicalAssessment,
               DecisionRequest, DispatchRequest, SimulatedDeviceEvent]
    assert len(examples) == len(schemas)
    for schema, example in zip(schemas, examples, strict=True):
        schema.model_validate(json.loads(example))


def test_documented_snapshot_payload_is_state_not_nested_state():
    with TestClient(create_app()) as client:
        client.post("/api/v1/simulations/demo/reset", json={"mode": "simulation"})
        with client.websocket_connect("/api/v1/simulations/demo/ws") as websocket:
            event = websocket.receive_json()
            assert event["type"] == "simulation.snapshot"
            state = SimulationState.model_validate(event["payload"])
            assert event["sequence"] == state.sequence
            assert state.mode == "simulation"
