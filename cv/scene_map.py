"""Check that a calibrated staged area lies on walkable cells of the scene map.

The staged floor is the convex hull of the calibration's world points. It is
sampled every ``step_m`` and each sample is looked up in the backend's occupancy
grid (0 free, 100 blocked, -1 unknown) and obstacle polygons. Positions the
camera publishes inside a blocked or unknown cell would give the router a
casualty it cannot reach, so the calibration tool refuses such an area.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from cv.zones import point_in_polygon


@dataclass(frozen=True)
class AreaCheck:
    bounds: tuple[float, float, float, float]          # min x, min y, max x, max y
    samples: int
    outside: int
    blocked_cells: tuple[tuple[int, int], ...] = ()   # (column, row)
    unknown_cells: tuple[tuple[int, int], ...] = ()
    obstacles: tuple[str, ...] = ()
    map_size_m: tuple[float, float] = (0.0, 0.0)
    revision: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def walkable(self) -> bool:
        return self.samples > 0 and not (self.outside or self.blocked_cells
                                         or self.unknown_cells or self.obstacles)


def fetch_scene_map(base_url: str, scenario_id: str, timeout_s: float = 2.0) -> dict | None:
    """The current scene map from the backend state, or None when it has none."""
    import requests

    response = requests.get(f"{base_url.rstrip('/')}/api/v1/simulations/{scenario_id}/state",
                            timeout=timeout_s)
    response.raise_for_status()
    return (response.json().get("scenario") or {}).get("scene_map")


def convex_hull(points: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
    ordered = sorted({(float(x), float(y)) for x, y in points})
    if len(ordered) <= 2:
        return ordered

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def check_area(scene_map: dict[str, Any], world_points: Sequence[Sequence[float]],
               step_m: float = 0.25) -> AreaCheck:
    hull = convex_hull(world_points)
    xs, ys = [p[0] for p in hull], [p[1] for p in hull]
    bounds = (min(xs), min(ys), max(xs), max(ys))
    samples = [(x, y) for x in _steps(bounds[0], bounds[2], step_m)
               for y in _steps(bounds[1], bounds[3], step_m) if point_in_polygon(hull, x, y)]
    samples += hull

    grid = scene_map.get("occupancy_grid")
    notes = []
    outside, blocked, unknown, size = 0, set(), set(), (0.0, 0.0)
    if grid:
        origin, resolution = grid["origin"], float(grid["resolution_m"])
        width, height, cells = int(grid["width"]), int(grid["height"]), grid["cells"]
        size = (width * resolution, height * resolution)
        for x, y in samples:
            column = math.floor((x - origin["x_m"]) / resolution)
            row = math.floor((y - origin["y_m"]) / resolution)
            # A sample exactly on the far edge belongs to the last cell.
            if math.isclose(x, origin["x_m"] + size[0]):
                column = width - 1
            if math.isclose(y, origin["y_m"] + size[1]):
                row = height - 1
            if not (0 <= column < width and 0 <= row < height):
                outside += 1
                continue
            value = cells[row * width + column]
            if value == 100:
                blocked.add((column, row))
            elif value == -1:
                unknown.add((column, row))
    else:
        notes.append("the scene map has no occupancy grid; only obstacles were checked")
    hit = sorted({obstacle["id"] for obstacle in scene_map.get("obstacles") or []
                  for x, y in samples
                  if point_in_polygon([(v["x_m"], v["y_m"]) for v in obstacle["vertices"]], x, y)})
    return AreaCheck(bounds=bounds, samples=len(samples), outside=outside,
                     blocked_cells=tuple(sorted(blocked)), unknown_cells=tuple(sorted(unknown)),
                     obstacles=tuple(hit), map_size_m=size,
                     revision=str(scene_map.get("revision", "")), notes=notes)


def describe(check: AreaCheck) -> str:
    x0, y0, x1, y1 = check.bounds
    head = f"staged area x {x0:.2f}..{x1:.2f} m, y {y0:.2f}..{y1:.2f} m (map revision {check.revision})"
    if check.walkable:
        return f"OK: {head} is inside the map and every sampled cell is walkable."
    problems = []
    if check.outside:
        problems.append(f"{check.outside} of {check.samples} samples are outside the "
                        f"{check.map_size_m[0]:g} x {check.map_size_m[1]:g} m map")
    if check.blocked_cells:
        problems.append("blocked cells (column, row): " + ", ".join(map(str, check.blocked_cells[:12])))
    if check.unknown_cells:
        problems.append("unknown cells: " + ", ".join(map(str, check.unknown_cells[:12])))
    if check.obstacles:
        problems.append("overlaps obstacles: " + ", ".join(check.obstacles))
    return f"NOT WALKABLE: {head}\n  - " + "\n  - ".join(problems + check.notes)


def _steps(lo: float, hi: float, step: float) -> list[float]:
    count = int(math.floor((hi - lo) / step + 1e-9))
    return [lo + i * step for i in range(count + 1)]
