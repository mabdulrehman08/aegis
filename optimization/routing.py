"""Four-neighbor A* on calibrated grids; polygon contact blocks whole cells.

Unknown occupancy and missing grids are never interpreted as free space. This
point-agent planner does not certify responder clearance or authorize dispatch.
"""

import heapq
import math

from backend.app.models import AStarRoute, Position, SceneMap

Point = tuple[float, float]
Cell = tuple[int, int]


def _cross(first: Point, second: Point, third: Point) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (
        second[1] - first[1]
    ) * (third[0] - first[0])


def _on_segment(first: Point, second: Point, point: Point) -> bool:
    return (
        _cross(first, second, point) == 0
        and min(first[0], second[0]) <= point[0] <= max(first[0], second[0])
        and min(first[1], second[1]) <= point[1] <= max(first[1], second[1])
    )


def _intersects(first: Point, second: Point, third: Point, fourth: Point) -> bool:
    crosses = (
        _cross(first, second, third), _cross(first, second, fourth),
        _cross(third, fourth, first), _cross(third, fourth, second),
    )
    if not all(math.isfinite(value) for value in crosses):
        raise ValueError("Geometry exceeds finite arithmetic bounds")
    return (
        ((crosses[0] > 0 > crosses[1]) or (crosses[1] > 0 > crosses[0]))
        and ((crosses[2] > 0 > crosses[3]) or (crosses[3] > 0 > crosses[2]))
    ) or any((
        _on_segment(first, second, third), _on_segment(first, second, fourth),
        _on_segment(third, fourth, first), _on_segment(third, fourth, second),
    ))


def _edges(vertices: list[Point]) -> list[tuple[Point, Point]]:
    return list(zip(vertices, vertices[1:] + vertices[:1]))


def _inside(point: Point, vertices: list[Point]) -> bool:
    inside = False
    for first, second in _edges(vertices):
        if _on_segment(first, second, point):
            return True
        if (first[1] > point[1]) != (second[1] > point[1]):
            crossing = first[0] + (point[1] - first[1]) * (
                second[0] - first[0]
            ) / (second[1] - first[1])
            if point[0] < crossing:
                inside = not inside
    return inside


class _GridRouter:
    """Request-local geometry preparation, shared by candidate routes."""

    def __init__(self, scene_map: SceneMap):
        self.scene_map = SceneMap.model_validate(scene_map.model_dump())
        self.grid = self.scene_map.occupancy_grid
        self.polygons: list[list[Point]] = []
        self.blocked: dict[Cell, bool] = {}
        for obstacle in self.scene_map.obstacles:
            vertices = [(point.x_m, point.y_m) for point in obstacle.vertices]
            if vertices[0] == vertices[-1]:
                vertices.pop()
            if len(set(vertices)) != len(vertices) or len(vertices) < 3:
                raise ValueError("Obstacle must have distinct polygon vertices")
            area = sum(_cross(vertices[0], vertices[index], vertices[index + 1])
                       for index in range(1, len(vertices) - 1))
            if not math.isfinite(area) or area == 0:
                raise ValueError("Obstacle polygon must have finite nonzero area")
            edges = _edges(vertices)
            for index, (first, second) in enumerate(edges):
                for other in range(index + 1, len(edges)):
                    if other == index + 1 or (index == 0 and other == len(edges) - 1):
                        continue
                    if _intersects(first, second, *edges[other]):
                        raise ValueError("Obstacle polygon must be simple")
            self.polygons.append(vertices)
        if self.grid is not None:
            grid = self.grid
            upper_x = grid.origin.x_m + grid.width * grid.resolution_m
            upper_y = grid.origin.y_m + grid.height * grid.resolution_m
            if not all(math.isfinite(value) for value in (upper_x, upper_y)):
                raise ValueError("Grid extent must be finite")
            if (grid.origin.x_m + grid.resolution_m / 2 == grid.origin.x_m
                    or grid.origin.y_m + grid.resolution_m / 2 == grid.origin.y_m
                    or upper_x - grid.resolution_m / 2 == upper_x
                    or upper_y - grid.resolution_m / 2 == upper_y):
                raise ValueError("Grid resolution is below coordinate precision")

    def _is_blocked(self, cell: Cell) -> bool:
        if cell in self.blocked:
            return self.blocked[cell]
        grid = self.grid
        column, row = cell
        if grid is None or not (0 <= column < grid.width and 0 <= row < grid.height):
            return True
        blocked = grid.cells[row * grid.width + column] != 0
        lower_x = grid.origin.x_m + column * grid.resolution_m
        lower_y = grid.origin.y_m + row * grid.resolution_m
        upper_x, upper_y = lower_x + grid.resolution_m, lower_y + grid.resolution_m
        corners = [(lower_x, lower_y), (upper_x, lower_y),
                   (upper_x, upper_y), (lower_x, upper_y)]
        if not blocked:
            for vertices in self.polygons:
                if (max(point[0] for point in vertices) < lower_x
                        or min(point[0] for point in vertices) > upper_x
                        or max(point[1] for point in vertices) < lower_y
                        or min(point[1] for point in vertices) > upper_y):
                    continue
                if (any(lower_x <= point[0] <= upper_x and lower_y <= point[1] <= upper_y
                        for point in vertices)
                        or any(_inside(corner, vertices) for corner in corners)
                        or any(_intersects(*edge, *boundary)
                               for edge in _edges(vertices) for boundary in _edges(corners))):
                    blocked = True
                    break
        self.blocked[cell] = blocked
        return blocked

    def _cell(self, position: Position) -> Cell | None:
        grid = self.grid
        if grid is None:
            return None
        offset_x = (position.x_m - grid.origin.x_m) / grid.resolution_m
        offset_y = (position.y_m - grid.origin.y_m) / grid.resolution_m
        if not (0 <= offset_x < grid.width and 0 <= offset_y < grid.height):
            return None
        cell = (math.floor(offset_x), math.floor(offset_y))
        if self._is_blocked(cell):
            return None
        columns = [cell[0]] + ([cell[0] - 1] if offset_x.is_integer() and cell[0] > 0 else [])
        rows = [cell[1]] + ([cell[1] - 1] if offset_y.is_integer() and cell[1] > 0 else [])
        if any(self._is_blocked((column, row)) for column in columns for row in rows):
            return None
        return cell

    def plan(self, start: Position, goal: Position) -> AStarRoute | None:
        start = Position.model_validate(start.model_dump())
        goal = Position.model_validate(goal.model_dump())
        if start.frame_id != self.scene_map.frame_id or goal.frame_id != self.scene_map.frame_id:
            raise ValueError("Route endpoints must use the map coordinate frame")
        start_cell, goal_cell = self._cell(start), self._cell(goal)
        if start_cell is None or goal_cell is None:
            return None
        frontier = [(0, 0, start_cell)]
        distances = {start_cell: 0}
        parents: dict[Cell, Cell] = {}
        while frontier:
            _, distance, current = heapq.heappop(frontier)
            if distance != distances[current]:
                continue
            if current == goal_cell:
                cells = [current]
                while current != start_cell:
                    current = parents[current]
                    cells.append(current)
                cells.reverse()
                grid = self.grid
                waypoints = [start]
                if start_cell != goal_cell:
                    for column, row in cells:
                        point = Position(frame_id=self.scene_map.frame_id,
                                         x_m=grid.origin.x_m + (column + 0.5) * grid.resolution_m,
                                         y_m=grid.origin.y_m + (row + 0.5) * grid.resolution_m)
                        if point != waypoints[-1]:
                            waypoints.append(point)
                if goal != waypoints[-1] or len(waypoints) == 1:
                    waypoints.append(goal)
                length = sum(math.hypot(second.x_m - first.x_m, second.y_m - first.y_m)
                             for first, second in zip(waypoints, waypoints[1:]))
                if not math.isfinite(length):
                    raise ValueError("Route length must be finite")
                return AStarRoute(algorithm="astar", map_revision=self.scene_map.revision,
                                  waypoints=waypoints, distance_m=length)
            column, row = current
            for neighbor in ((column - 1, row), (column, row - 1),
                             (column, row + 1), (column + 1, row)):
                candidate = distance + 1
                if not self._is_blocked(neighbor) and candidate < distances.get(neighbor, math.inf):
                    distances[neighbor] = candidate
                    parents[neighbor] = current
                    heuristic = abs(neighbor[0] - goal_cell[0]) + abs(neighbor[1] - goal_cell[1])
                    heapq.heappush(frontier, (candidate + heuristic, candidate, neighbor))
        return None


def plan_route(scene_map: SceneMap, start: Position, goal: Position) -> AStarRoute | None:
    """Return exact world endpoints and route revision, or None if inaccessible.

    Malformed geometry, nonfinite coordinates and frame mismatches raise ValueError.
    Grid bounds are half-open at the top and right. Polygon boundary contact is
    blocked, including polygons crossing a cell without containing its center.
    """
    return _GridRouter(scene_map).plan(start, goal)
