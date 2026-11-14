from fastapi import FastAPI

from backend.app.api.routes import router
from backend.app.simulation.routes import router as simulation_router
from backend.app.simulation.store import SimulationStore


def create_app() -> FastAPI:
    application = FastAPI(
        title="AEGIS Development API",
        version="0.1.0",
        description="Synthetic incident coordination. Simulation-only actions; no real dispatch or clinical inference.",
    )
    application.include_router(router)
    application.state.simulation_store = SimulationStore()
    application.include_router(simulation_router)
    return application


app = create_app()
