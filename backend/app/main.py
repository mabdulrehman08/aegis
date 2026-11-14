from fastapi import FastAPI

from backend.app.api.routes import router


def create_app() -> FastAPI:
    application = FastAPI(
        title="AEGIS Development API",
        version="0.1.0",
        description="Read-only synthetic data. No dispatch or clinical inference.",
    )
    application.include_router(router)
    return application


app = create_app()
