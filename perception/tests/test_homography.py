import numpy as np
import pytest

from perception.homography import (
    Calibration,
    CalibrationError,
    build_calibration,
    compute_homography,
    reprojection_errors,
)
from perception.tests.helpers import FLOOR_SQUARE, pinhole_calibration, world_to_pixel


def test_four_points_recover_the_floor_plane_exactly():
    calibration = pinhole_calibration()
    probes = [(0.5, 0.5), (1.5, 2.2), (2.9, 0.1), (4.0, 5.0)]  # last one outside the square
    mapped, valid = calibration.image_to_world([world_to_pixel(x, y) for x, y in probes])
    assert valid.all()
    np.testing.assert_allclose(mapped, probes, atol=1e-6)
    assert calibration.rms_error_m < 1e-6  # exact fit with 4 points (float precision)


def test_world_to_image_is_the_inverse():
    calibration = pinhole_calibration()
    pixels, valid = calibration.world_to_image([(1.0, 1.0), (2.0, 2.5)])
    assert valid.all()
    np.testing.assert_allclose(pixels, [world_to_pixel(1, 1), world_to_pixel(2, 2.5)], atol=1e-6)


def test_extra_points_give_a_real_reprojection_error():
    rng = np.random.default_rng(0)
    world = FLOOR_SQUARE + [(1.5, 1.5), (0.5, 2.5)]
    pixels = np.array([world_to_pixel(x, y) for x, y in world]) + rng.normal(0, 2.0, (6, 2))
    calibration = build_calibration(pixels, world, "demo-floor-1", (1280, 720))
    errors = reprojection_errors(calibration.homography, pixels, world)
    assert 0 < calibration.rms_error_m < 0.05      # 2 px noise -> a few cm
    assert calibration.max_error_m == pytest.approx(errors.max())


def test_points_above_the_horizon_are_invalid_not_wild():
    calibration = pinhole_calibration()
    horizon_v = 360.0 - 900.0 * np.tan(np.radians(35.0))   # about -270 px
    _, valid = calibration.image_to_world([(640, horizon_v - 150), (640, 700)])
    assert valid.tolist() == [False, True]
    assert calibration.point_to_world((640, horizon_v - 150)) is None


def test_degenerate_points_are_rejected():
    with pytest.raises(CalibrationError, match="collinear"):
        compute_homography([(0, 0), (1, 1), (2, 2), (3, 3)], FLOOR_SQUARE)
    with pytest.raises(CalibrationError, match="collinear"):
        compute_homography([(0, 0), (1, 0), (2, 0), (3, 5)], FLOOR_SQUARE)
    with pytest.raises(CalibrationError, match="At least 4"):
        compute_homography([(0, 0), (1, 0), (1, 1)], [(0, 0), (1, 0), (1, 1)])
    with pytest.raises(CalibrationError, match="counts differ"):
        compute_homography([(0, 0), (1, 0), (1, 1), (0, 1)], FLOOR_SQUARE[:3] + [(0, 2), (5, 5)])


def test_rescaling_to_another_resolution_keeps_the_mapping():
    calibration = pinhole_calibration()
    half = calibration.for_image_size(640, 360)
    u, v = world_to_pixel(1.2, 1.8)
    assert half.point_to_world((u / 2, v / 2)) == pytest.approx((1.2, 1.8), abs=1e-6)
    with pytest.raises(CalibrationError, match="aspect ratio"):
        calibration.for_image_size(640, 480)


def test_json_round_trip(tmp_path):
    calibration = pinhole_calibration()
    path = tmp_path / "homography.json"
    calibration.save(path)
    loaded = Calibration.load(path)
    assert loaded.frame_id == "demo-floor-1"
    assert loaded.calibration_ref == calibration.calibration_ref
    assert loaded.calibration_ref.startswith("perception-demo-floor-1-")
    assert loaded.point_to_world(world_to_pixel(2, 1)) == pytest.approx((2, 1), abs=1e-6)
