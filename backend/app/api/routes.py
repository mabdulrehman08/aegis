from fastapi import APIRouter

from backend.app.models import HealthResponse, Scenario
from backend.app.services.scenarios import load_demo_scenario


router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="aegis-backend", version="0.1.0")


@router.get("/api/v1/scenarios/demo", response_model=Scenario, tags=["scenarios"])
def demo_scenario() -> Scenario:
    return load_demo_scenario()
