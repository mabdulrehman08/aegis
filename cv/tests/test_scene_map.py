import pytest

from cv.scene_map import check_area, convex_hull, describe
from cv.tools.calibrate import main as calibrate_main


def demo_map():
    """Same layout as the backend demo map: 24 x 20 m, wall at column 8, rows 2..12."""
    width, height = 24, 20
    cells = [0] * (width * height)
    for row in range(2, 13):
        cells[row * width + 8] = 100

    def point(x, y):
        return {"frame_id": "demo-floor-1", "x_m": x, "y_m": y}

    return {"frame_id": "demo-floor-1", "revision": "demo-map-1",
            "obstacles": [{"id": "sim-wall-a", "vertices": [point(8, 2), point(9, 2), point(9, 13), point(8, 13)]}],
            "occupancy_grid": {"origin": point(0, 0), "resolution_m": 1.0,
                               "width": width, "height": height, "cells": cells}}


def square(x0, y0, side=4.0):
    return [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]


def test_recommended_staged_area_is_walkable():
    check = check_area(demo_map(), square(12, 6))
    assert check.walkable, describe(check)
    assert check.bounds == (12, 6, 16, 10)


def test_area_over_the_wall_is_rejected_with_the_cells():
    check = check_area(demo_map(), square(6, 4))
    assert not check.walkable
    assert (8, 4) in check.blocked_cells and check.obstacles == ("sim-wall-a",)
    assert "NOT WALKABLE" in describe(check)


def test_area_past_the_map_edge_is_rejected():
    check = check_area(demo_map(), square(21, 17))
    assert check.outside > 0 and not check.walkable


def test_area_touching_the_far_map_edge_is_inside():
    assert check_area(demo_map(), square(20, 16)).walkable


def test_convex_hull_ignores_interior_points():
    hull = convex_hull([(0, 0), (2, 0), (2, 2), (0, 2), (1, 1)])
    assert sorted(hull) == [(0, 0), (0, 2), (2, 0), (2, 2)]


def test_matches_the_backend_demo_map():
    pytest.importorskip("pydantic")
    from backend.app.simulation.store import demo_scene_map

    backend_map = demo_scene_map("demo-floor-1").model_dump(mode="json")
    assert check_area(backend_map, square(12, 6)).walkable
    assert not check_area(backend_map, square(6, 4)).walkable


def test_calibrate_refuses_to_save_a_blocked_area(tmp_path, monkeypatch):
    import cv2
    import numpy as np

    image = tmp_path / "frame.png"
    cv2.imwrite(str(image), np.zeros((720, 1280, 3), dtype=np.uint8))
    monkeypatch.setattr("cv.scene_map.fetch_scene_map", lambda *_: demo_map())
    common = ["--image", str(image), "--points", "100,600;1180,600;1000,300;280,300",
              "--world", "0,0;4,0;4,4;0,4", "--yes"]
    blocked = tmp_path / "blocked.json"
    assert calibrate_main(common + ["--offset", "6", "4", "--output", str(blocked)]) == 3
    assert not blocked.exists()
    good = tmp_path / "good.json"
    assert calibrate_main(common + ["--offset", "12", "6", "--output", str(good)]) == 0
    assert good.exists()
