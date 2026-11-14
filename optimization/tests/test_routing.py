import math

import pytest

from backend.app.models import Obstacle, Position
from optimization.routing import plan_route
from optimization.tests.conftest import position, scene_map


def polygon(vertices):
    return Obstacle(id="obstacle", vertices=[position(*vertex) for vertex in vertices])


def test_world_coordinates_resolution_origin_endpoints_and_distance():
    grid = scene_map(resolution=2, origin=position(-10, 20))
    start, goal = position(-9.8, 20.2), position(-0.4, 27.8)
    route = plan_route(grid, start, goal)
    assert route is not None
    assert route.waypoints[0] == start
    assert route.waypoints[-1] == goal
    assert route.map_revision == "map-7"
    assert route.distance_m == pytest.approx(sum(
        math.hypot(second.x_m - first.x_m, second.y_m - first.y_m)
        for first, second in zip(route.waypoints, route.waypoints[1:])
    ))
    centers = route.waypoints[1:-1]
    for first, second in zip(centers, centers[1:]):
        assert abs(second.x_m - first.x_m) + abs(second.y_m - first.y_m) == 2


@pytest.mark.parametrize("cell_value", [-1, 100])
def test_unknown_and_occupied_walls_block(cell_value):
    grid = scene_map(width=3, height=3,
                     cells=[0, cell_value, 0] * 3)
    assert plan_route(grid, position(0.5, 0.5), position(2.5, 0.5)) is None


def test_four_neighbors_never_cut_diagonal():
    grid = scene_map(width=2, height=2, cells=[0, 100, 100, 0])
    assert plan_route(grid, position(), position(1.5, 1.5)) is None


def test_astar_detours_through_only_gap():
    grid = scene_map(width=3, height=3, cells=[0, 100, 0, 0, 100, 0, 0, 0, 0])
    route = plan_route(grid, position(), position(2.5, 0.5))
    assert route.distance_m == 6
    assert position(1.5, 2.5) in route.waypoints


@pytest.mark.parametrize("endpoint", [(-0.01, 0.5), (8, 0.5), (0.5, 6), (0.5, -1)])
def test_outside_and_upper_bounds_block(endpoint):
    assert plan_route(scene_map(), position(*endpoint), position()) is None
    assert plan_route(scene_map(), position(), position(*endpoint)) is None


def test_lower_bound_and_same_cell_exact_segment():
    route = plan_route(scene_map(), position(0, 0), position(0.8, 0.8))
    assert route.waypoints == [position(0, 0), position(0.8, 0.8)]
    assert route.distance_m == pytest.approx(math.hypot(0.8, 0.8))
    identical = plan_route(scene_map(), position(), position())
    assert identical.distance_m == 0
    assert identical.waypoints == [position(), position()]


@pytest.mark.parametrize("vertices", [
    [(1.1, 0.1), (1.2, 0.1), (1.2, 0.2), (1.1, 0.2)],
    [(1.1, -1), (1.2, -1), (1.2, 2), (1.1, 2)],
    [(-1, -1), (4, -1), (4, 2), (-1, 2)],
    [(1, 1), (2, 1), (1.5, 2)],
])
def test_polygon_cell_contact_crossing_containment_and_tiny_polygons_block(vertices):
    grid = scene_map(width=3, height=1, obstacles=[polygon(vertices)])
    assert plan_route(grid, position(), position(2.5, 0.5)) is None


def test_polygon_detour_and_no_route_segment_crosses_polygon():
    grid = scene_map(width=5, height=4, obstacles=[polygon([
        (2.1, 0.1), (2.9, 0.1), (2.9, 1.9), (2.1, 1.9),
    ])])
    route = plan_route(grid, position(), position(4.5, 0.5))
    assert route is not None
    assert route.distance_m == 8
    assert any(point.y_m == 2.5 for point in route.waypoints)


def test_internal_boundary_endpoint_touching_unknown_is_blocked():
    grid = scene_map(width=2, height=1, cells=[-1, 0])
    assert plan_route(grid, position(1, 0.5), position(1.5, 0.5)) is None


@pytest.mark.parametrize("vertices", [
    [(0, 0), (1, 1), (2, 2)],
    [(0, 0), (2, 2), (0, 2), (2, 0)],
    [(0, 0), (1, 0), (1, 0), (0, 1)],
])
def test_invalid_polygons_rejected(vertices):
    with pytest.raises(ValueError):
        plan_route(scene_map(obstacles=[polygon(vertices)]), position(), position(4.5, 0.5))


def test_missing_occupancy_is_not_free_space():
    grid = scene_map().model_copy(update={"occupancy_grid": None})
    assert plan_route(grid, position(), position(3.5, 0.5)) is None


def test_wrong_frames_and_nonfinite_constructed_positions_rejected():
    with pytest.raises(ValueError):
        plan_route(scene_map(), position(frame_id="other"), position())
    with pytest.raises(ValueError):
        plan_route(scene_map(), Position.model_construct(frame_id="world", x_m=math.nan, y_m=0), position())


def test_finite_but_overflowing_grid_extent_rejected():
    grid = scene_map(resolution=1e308)
    with pytest.raises(ValueError, match="finite"):
        plan_route(grid, position(), position())


def test_routing_is_deterministic_and_does_not_mutate_map():
    grid = scene_map()
    before = grid.model_dump()
    assert plan_route(grid, position(), position(7.5, 5.5)) == plan_route(
        grid, position(), position(7.5, 5.5))
    assert grid.model_dump() == before
