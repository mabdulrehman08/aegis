from datetime import datetime, timezone

import pytest

from backend.app.models import (
    Casualty, OccupancyGrid, Position, Responder, Scenario, SceneMap,
)

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


def position(x_m=0.5, y_m=0.5, frame_id="world"):
    return Position(frame_id=frame_id, x_m=x_m, y_m=y_m)


def scene_map(width=8, height=6, cells=None, resolution=1.0, origin=None, obstacles=None):
    return SceneMap(
        frame_id="world", revision="map-7", obstacles=obstacles or [],
        occupancy_grid=OccupancyGrid(origin=origin or position(0, 0), resolution_m=resolution,
                                     width=width, height=height,
                                     cells=cells if cells is not None else [0] * (width * height)),
    )


def casualty(identifier, x_m=4.5, y_m=0.5, assessment=None):
    return Casualty(id=identifier, position=position(x_m, y_m), medical_assessment=assessment)


def responder(identifier, x_m=0.5, y_m=0.5, status="available", capabilities=None):
    return Responder(id=identifier, display_name=identifier, position=position(x_m, y_m),
                     status=status, capabilities=capabilities or [])


@pytest.fixture
def scenario():
    return Scenario(schema_version="1.0", id="scene", name="Synthetic", synthetic=True,
                    frame_id="world", created_at=NOW, casualties=[casualty("c1"), casualty("c2", 5.5)],
                    responders=[responder("r1"), responder("r2", 1.5)], scene_map=scene_map())
