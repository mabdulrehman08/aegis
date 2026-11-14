"""Shared synthetic fixtures: a pinhole camera looking down at a floor plane."""

from __future__ import annotations

import math

import numpy as np

from perception.detector import Detection
from perception.homography import build_calibration

F, CX, CY = 900.0, 640.0, 360.0
CAMERA_POSITION = np.array([1.5, -2.0, 2.5])   # meters; floor is z = 0
TILT = math.radians(35.0)                       # pitched down from horizontal


def _rotation() -> np.ndarray:
    x_axis = np.array([1.0, 0.0, 0.0])
    z_axis = np.array([0.0, math.cos(TILT), -math.sin(TILT)])  # forward
    y_axis = np.cross(z_axis, x_axis)                          # image down
    return np.vstack([x_axis, y_axis, z_axis])


def world_to_pixel(x: float, y: float, z: float = 0.0) -> tuple[float, float]:
    camera = _rotation() @ (np.array([x, y, z]) - CAMERA_POSITION)
    return F * camera[0] / camera[2] + CX, F * camera[1] / camera[2] + CY


FLOOR_SQUARE = [(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 3.0)]


def pinhole_calibration(frame_id: str = "demo-floor-1"):
    pixels = [world_to_pixel(x, y) for x, y in FLOOR_SQUARE]
    return build_calibration(pixels, FLOOR_SQUARE, frame_id, (1280, 720))


def scale_calibration(frame_id: str = "test-frame"):
    """100 px = 1 m, axes aligned: easy arithmetic for tracker tests."""
    pixels = [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]
    world = [(0, 0), (10, 0), (10, 10), (0, 10)]
    return build_calibration(pixels, world, frame_id, (1000, 1000))


def keypoints(points: dict[int, tuple[float, float]], conf: float = 0.9):
    xy = np.zeros((17, 2))
    kc = np.zeros(17)
    for index, (u, v) in points.items():
        xy[index] = (u, v)
        kc[index] = conf
    return xy, kc


def det(raw_id, bbox, confidence=0.8, kps=None) -> Detection:
    xy, kc = kps if kps is not None else (None, None)
    return Detection(raw_id=raw_id, bbox=bbox, confidence=confidence, keypoints=xy, keypoint_conf=kc)


def standing_box(cx_px: float, bottom_px: float):
    """A tall box (aspect 3) whose bottom-center is (cx_px, bottom_px)."""
    return (cx_px - 20, bottom_px - 120, cx_px + 20, bottom_px)
