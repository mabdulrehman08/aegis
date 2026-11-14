"""Fixed-seed, explicitly human-assessed synthetic optimization inputs."""

from datetime import datetime, timezone
from random import Random

from backend.app.models import (
    Casualty,
    MedicalAssessment,
    Obstacle,
    OccupancyGrid,
    Position,
    Responder,
    Scenario,
    SceneMap,
)


NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
FRAME = "synthetic-grid"


def position(x: float, y: float) -> Position:
    return Position(frame_id=FRAME, x_m=x, y_m=y)


def assessment(severity: str = "delayed") -> MedicalAssessment:
    return MedicalAssessment(
        severity=severity,
        source="human",
        assessed_by="synthetic-human-operator",
        assessed_at=NOW,
    )


def make_scenario(seed: int | None = None) -> tuple[Scenario, dict[str, list[str]]]:
    width = height = 24
    cells = [0] * (width * height)
    if seed is None:
        casualty_positions = [(3.5, 1.5), (1.5, 4.5), (19.5, 19.5)]
        responder_positions = [(1.5, 1.5), (5.5, 1.5)]
        capabilities = [["first_aid"], ["first_aid"]]
        statuses = ["available", "available"]
        severities = ["immediate", "delayed", "minor"]
    else:
        random = Random(seed)
        for row in range(height):
            for column in range(width):
                if random.random() < 0.14:
                    cells[row * width + column] = 100
        coordinates = random.sample(
            [(column + 0.5, row + 0.5) for row in range(height) for column in range(width)],
            18,
        )
        casualty_positions, responder_positions = coordinates[:10], coordinates[10:]
        capabilities = [
            ["first_aid", "extraction"] if index % 3 == 0 else ["first_aid"]
            for index in range(8)
        ]
        statuses = ["available"] * 6 + ["busy", "offline"]
        severities = ["immediate", "delayed", "minor", "expectant", "deceased"] * 2
    for x_coord, y_coord in [*casualty_positions, *responder_positions]:
        cells[int(y_coord) * width + int(x_coord)] = 0
    casualties = [
        Casualty(
            id=f"casualty-{index:03d}",
            position=position(*coordinates),
            medical_assessment=assessment(severities[index]),
        )
        for index, coordinates in enumerate(casualty_positions)
    ]
    if seed is not None:
        casualties[-1] = casualties[-1].model_copy(update={"medical_assessment": None})
    responders = [
        Responder(
            id=f"responder-{index:03d}",
            display_name=f"Synthetic responder {index}",
            position=position(*coordinates),
            status=statuses[index],
            capabilities=capabilities[index],
        )
        for index, coordinates in enumerate(responder_positions)
    ]
    requirements = {
        casualty.id: ["extraction"] if seed is not None and index % 3 == 0 else ["first_aid"]
        for index, casualty in enumerate(casualties)
    }
    obstacles = []
    if seed is not None:
        blocked = next(index for index, value in enumerate(cells) if value == 100)
        column, row = blocked % width, blocked // width
        obstacles = [Obstacle(id="synthetic-barrier", vertices=[
            position(column + 0.1, row + 0.1), position(column + 0.9, row + 0.1),
            position(column + 0.9, row + 0.9), position(column + 0.1, row + 0.9),
        ])]
    return Scenario(
        schema_version="1.0",
        id="grid3" if seed is None else f"seed-{seed}",
        name="Synthetic known three-casualty/two-responder grid" if seed is None else f"Synthetic seed {seed}",
        synthetic=True,
        frame_id=FRAME,
        created_at=NOW,
        casualties=casualties,
        responders=responders,
        scene_map=SceneMap(
            frame_id=FRAME,
            revision="map-1",
            obstacles=obstacles,
            occupancy_grid=OccupancyGrid(
                origin=position(0, 0),
                resolution_m=1,
                width=width,
                height=height,
                cells=cells,
            ),
        ),
    ), requirements
