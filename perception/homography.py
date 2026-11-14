"""Image-pixel → floor-plane mapping (planar homography) and ground-point choice.

The floor is a plane, so a 3x3 homography maps any image point that lies on the
floor to world meters in the calibrated frame. Points off the floor (a standing
person's shoulders) are mapped as if they were on it; that is why the ground
point selection below matters. No 3D reconstruction is attempted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

HIP_KEYPOINTS = (11, 12)


class CalibrationError(ValueError):
    pass


def _as_points(points: Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
    array = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if not np.all(np.isfinite(array)):
        raise CalibrationError("Points must be finite numbers")
    return array


def compute_homography(image_points, world_points) -> np.ndarray:
    """Least-squares homography over all points (exact for 4 points)."""
    import cv2

    image = _as_points(image_points)
    world = _as_points(world_points)
    if len(image) != len(world):
        raise CalibrationError("Image and world point counts differ")
    if len(image) < 4:
        raise CalibrationError("At least 4 point pairs are required")
    for name, points in (("image", image), ("world", world)):
        if not _has_general_quad(points):
            raise CalibrationError(
                f"No 4 {name} points are in general position (three or more are collinear); "
                "spread them out")
    matrix, _ = cv2.findHomography(image, world, 0)
    if matrix is None or not np.all(np.isfinite(matrix)) or abs(np.linalg.det(matrix)) < 1e-12:
        raise CalibrationError("Could not compute a homography from these points")
    return matrix / matrix[2, 2]


def _has_general_quad(points: np.ndarray) -> bool:
    """True if some 4 points have no three (nearly) collinear."""
    from itertools import combinations

    tolerance = 1e-6 * max(1.0, float(np.ptp(points, axis=0).prod()))

    def area(a, b, c) -> float:
        return abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2.0

    return any(
        all(area(*triple) > tolerance for triple in combinations(quad, 3))
        for quad in combinations(points, 4)
    )


def project(matrix: np.ndarray, points, expected_sign: float | None = None):
    """Apply a homography. Returns ``(xy, valid)``.

    ``valid`` is false where the homogeneous scale is ~0 or has the opposite sign
    of the calibration points: those pixels are at/above the horizon and have no
    floor position. They are reported as invalid instead of a wild coordinate.
    """
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    homogeneous = np.hstack([pts, np.ones((len(pts), 1))]) @ matrix.T
    scale = homogeneous[:, 2]
    valid = np.abs(scale) > 1e-9
    if expected_sign is not None:
        valid &= np.sign(scale) == expected_sign
    with np.errstate(divide="ignore", invalid="ignore"):
        xy = homogeneous[:, :2] / scale[:, None]
    valid &= np.all(np.isfinite(xy), axis=1)
    return xy, valid


def reprojection_errors(matrix: np.ndarray, image_points, world_points) -> np.ndarray:
    """Per-point distance in world meters between mapped and entered positions."""
    mapped, _ = project(matrix, image_points)
    return np.linalg.norm(mapped - _as_points(world_points), axis=1)


def make_calibration_ref(matrix: np.ndarray, frame_id: str) -> str:
    digest = hashlib.sha256(np.round(matrix, 9).tobytes() + frame_id.encode()).hexdigest()[:10]
    return f"perception-{frame_id}-{digest}"


@dataclass(frozen=True)
class Calibration:
    homography: np.ndarray            # image pixels -> world meters
    frame_id: str
    image_size: tuple[int, int]       # (width, height) the points were clicked at
    image_points: np.ndarray
    world_points: np.ndarray
    rms_error_m: float
    max_error_m: float
    created_at: str
    calibration_ref: str

    @property
    def scale_sign(self) -> float:
        homogeneous = np.hstack([self.image_points, np.ones((len(self.image_points), 1))]) @ self.homography.T
        return float(np.sign(np.median(homogeneous[:, 2])))

    def image_to_world(self, points):
        return project(self.homography, points, self.scale_sign)

    def world_to_image(self, points):
        return project(np.linalg.inv(self.homography), points)

    def point_to_world(self, point) -> tuple[float, float] | None:
        xy, valid = self.image_to_world([point])
        return (float(xy[0, 0]), float(xy[0, 1])) if valid[0] else None

    def for_image_size(self, width: int, height: int) -> "Calibration":
        """Same camera at another resolution: rescale pixels (aspect must match)."""
        cal_w, cal_h = self.image_size
        if (width, height) == (cal_w, cal_h):
            return self
        if abs(width / height - cal_w / cal_h) > 0.01:
            raise CalibrationError(
                f"Frame {width}x{height} has a different aspect ratio than the calibration "
                f"{cal_w}x{cal_h}; recalibrate at this resolution"
            )
        scale = np.diag([cal_w / width, cal_h / height, 1.0])
        return Calibration(
            homography=self.homography @ scale, frame_id=self.frame_id, image_size=(width, height),
            image_points=self.image_points / np.array([cal_w / width, cal_h / height]),
            world_points=self.world_points, rms_error_m=self.rms_error_m,
            max_error_m=self.max_error_m, created_at=self.created_at,
            calibration_ref=self.calibration_ref,
        )

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "frame_id": self.frame_id,
            "image_size": list(self.image_size),
            "image_points": self.image_points.round(2).tolist(),
            "world_points": self.world_points.round(4).tolist(),
            "homography": self.homography.tolist(),
            "rms_error_m": round(self.rms_error_m, 4),
            "max_error_m": round(self.max_error_m, 4),
            "created_at": self.created_at,
            "calibration_ref": self.calibration_ref,
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Calibration":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        matrix = np.asarray(data["homography"], dtype=np.float64)
        if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
            raise CalibrationError(f"{path}: homography must be a finite 3x3 matrix")
        return cls(
            homography=matrix, frame_id=str(data["frame_id"]),
            image_size=(int(data["image_size"][0]), int(data["image_size"][1])),
            image_points=_as_points(data["image_points"]), world_points=_as_points(data["world_points"]),
            rms_error_m=float(data["rms_error_m"]), max_error_m=float(data["max_error_m"]),
            created_at=str(data["created_at"]), calibration_ref=str(data["calibration_ref"]),
        )


def build_calibration(image_points, world_points, frame_id: str, image_size: tuple[int, int]) -> Calibration:
    if not frame_id:
        raise CalibrationError("frame_id is required")
    image = _as_points(image_points)
    world = _as_points(world_points)
    matrix = compute_homography(image, world)
    errors = reprojection_errors(matrix, image, world)
    return Calibration(
        homography=matrix, frame_id=frame_id, image_size=(int(image_size[0]), int(image_size[1])),
        image_points=image, world_points=world,
        rms_error_m=float(np.sqrt(np.mean(errors ** 2))), max_error_m=float(errors.max()),
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        calibration_ref=make_calibration_ref(matrix, frame_id),
    )


def ground_point(bbox, posture: str, keypoints=None, keypoint_conf=None,
                 min_conf: float = 0.5) -> tuple[tuple[float, float], str]:
    """Image point where the person touches the floor, and how it was chosen.

    Upright people stand on the bottom edge of their box. For a lying person the
    bottom edge is just the body part nearest the camera, so the hips (or the
    box center) are used instead. Unknown posture uses the box center as the
    least-wrong choice for a person who may be lying.
    """
    x1, y1, x2, y2 = (float(value) for value in bbox)
    if posture in ("standing", "sitting"):
        return ((x1 + x2) / 2.0, y2), "bbox_bottom_center"
    if posture == "lying" and keypoints is not None and keypoint_conf is not None:
        hips = [keypoints[i] for i in HIP_KEYPOINTS if keypoint_conf[i] >= min_conf]
        if hips:
            hip = np.mean(np.asarray(hips, dtype=np.float64), axis=0)
            return (float(hip[0]), float(hip[1])), "hip_midpoint"
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0), "bbox_center"
