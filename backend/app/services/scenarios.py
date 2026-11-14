"""Read synthetic fixtures without maintaining mutable application state."""

from pathlib import Path

from backend.app.models import Scenario


DEMO_SCENARIO_PATH = (
    Path(__file__).resolve().parents[3] / "shared" / "mock" / "scenario.demo.json"
)


def load_demo_scenario() -> Scenario:
    return Scenario.model_validate_json(DEMO_SCENARIO_PATH.read_text(encoding="utf-8"))
