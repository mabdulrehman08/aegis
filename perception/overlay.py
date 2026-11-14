"""Debug overlay: boxes, IDs, posture, floor coordinates, HUD and a top-down mini-map."""

from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np

from perception.homography import Calibration
from perception.tracker import Track

GREEN = (80, 200, 80)
ORANGE = (0, 165, 255)
RED = (60, 60, 230)
WHITE = (255, 255, 255)
GREY = (150, 150, 150)
CYAN = (230, 200, 0)
SKELETON = [(5, 6), (5, 11), (6, 12), (11, 12), (5, 7), (7, 9), (6, 8), (8, 10),
            (11, 13), (13, 15), (12, 14), (14, 16)]


def track_color(track: Track):
    if track.stale:
        return RED
    return GREEN if track.detected else ORANGE


def _text(image, text: str, origin, scale: float = 0.55, color=WHITE) -> None:
    """Text on a solid dark background so it stays legible over any scene."""
    (width, height), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    x, y = origin
    cv2.rectangle(image, (x - 2, y - height - 3), (x + width + 2, y + baseline), (20, 20, 20), -1)
    cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def draw_calibration(image, calibration: Calibration | None) -> None:
    if calibration is None:
        return
    for u, v in calibration.image_points:
        cv2.drawMarker(image, (int(u), int(v)), CYAN, cv2.MARKER_CROSS, 14, 2)


def draw_tracks(image, tracks: Sequence[Track], keypoint_min_conf: float = 0.5) -> None:
    for track in tracks:
        color = track_color(track)
        x1, y1, x2, y2 = (int(v) for v in track.bbox)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2 if track.detected else 1)
        if track.detected and track.keypoints is not None and track.keypoint_conf is not None:
            kp, kc = track.keypoints, track.keypoint_conf
            for a, b in SKELETON:
                if kc[a] >= keypoint_min_conf and kc[b] >= keypoint_min_conf:
                    cv2.line(image, (int(kp[a][0]), int(kp[a][1])), (int(kp[b][0]), int(kp[b][1])), color, 1)
        if track.ground_px is not None:
            cv2.circle(image, (int(track.ground_px[0]), int(track.ground_px[1])), 5, CYAN, -1)
        seen = "" if track.detected else (" STALE" if track.stale else " unseen")
        lines = [
            f"{track.short_id} {track.state}{seen} {track.confidence_smoothed:.2f} {track.posture.label}",
            (f"({track.published_xy[0]:.2f}, {track.published_xy[1]:.2f}) m"
             if track.published_xy else "no floor position"),
        ]
        for i, line in enumerate(lines):
            _text(image, line, (x1 + 3, y1 + 16 + 17 * i), 0.45, color)


def draw_hud(image, lines: Sequence[str], warnings: Sequence[str] = ()) -> None:
    for i, line in enumerate(lines):
        _text(image, line, (10, 24 + 22 * i), 0.6)
    for i, line in enumerate(warnings):
        _text(image, line, (10, image.shape[0] - 14 - 24 * i), 0.65, RED)


def draw_minimap(image, tracks: Sequence[Track], calibration: Calibration | None,
                 size: int = 240, margin_m: float = 0.5) -> None:
    """Top-down view of mapped positions, +y up, in the corner of the frame."""
    if calibration is None:
        return
    world = calibration.world_points
    points = [world]
    published = [t.published_xy for t in tracks if t.published_xy is not None]
    if published:
        points.append(np.asarray(published))
    allp = np.vstack(points)
    lo = allp.min(axis=0) - margin_m
    hi = allp.max(axis=0) + margin_m
    span = float(max(hi[0] - lo[0], hi[1] - lo[1], 1.0))
    pad = 12
    scale = (size - 2 * pad) / span

    def to_px(x: float, y: float) -> tuple[int, int]:
        return int(pad + (x - lo[0]) * scale), int(size - pad - (y - lo[1]) * scale)

    panel = np.full((size, size, 3), 30, dtype=np.uint8)
    for gx in np.arange(np.floor(lo[0]), hi[0] + 1e-9, 1.0):
        cv2.line(panel, to_px(gx, lo[1]), to_px(gx, hi[1]), (55, 55, 55), 1)
    for gy in np.arange(np.floor(lo[1]), hi[1] + 1e-9, 1.0):
        cv2.line(panel, to_px(lo[0], gy), to_px(hi[0], gy), (55, 55, 55), 1)
    hull = cv2.convexHull(np.array([to_px(x, y) for x, y in world], dtype=np.int32))
    cv2.polylines(panel, [hull], True, CYAN, 1)
    for track in tracks:
        if track.published_xy is None:
            continue
        center = to_px(*track.published_xy)
        cv2.circle(panel, center, 6, track_color(track), -1)
        _text(panel, track.short_id, (center[0] + 7, center[1] - 5), 0.45)
    _text(panel, f"{calibration.frame_id} (1 m grid)", (6, 14), 0.4, GREY)

    height, width = image.shape[:2]
    if height > size + 10 and width > size + 10:
        image[10:10 + size, width - size - 10:width - 10] = panel
