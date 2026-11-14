"""Explicit zone mapping for a camera without a precise floor calibration.

Each zone is a polygon in normalized image coordinates (0..1, origin top-left)
plus a reference point on the scene map. A person whose ground point lies inside
the polygon is reported as that zone. The backend places them at the zone's
reference point and labels the location ``zone_estimate``: no metric position is
claimed. Zones are checked in order; the first match wins.

    mapping:
      mode: zones
      zones:
        - id: zone-b
          x_m: 3.0
          y_m: 2.0
          image_polygon: [[0, 0], [1, 0], [1, 1], [0, 1]]   # optional, default whole frame
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

FULL_FRAME = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
_KEYS = {"id", "x_m", "y_m", "image_polygon"}


class ZoneError(ValueError):
    pass


@dataclass(frozen=True)
class Zone:
    zone_id: str
    x_m: float
    y_m: float
    polygon: tuple[tuple[float, float], ...] = FULL_FRAME

    def contains(self, u: float, v: float) -> bool:
        """Point-in-polygon in normalized coordinates; the boundary counts as inside."""
        points = self.polygon
        inside = False
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
            if _on_segment(u, v, x1, y1, x2, y2):
                return True
            if (y1 > v) != (y2 > v) and u < x1 + (v - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        return inside


@dataclass(frozen=True)
class ZoneMap:
    frame_id: str
    zones: tuple[Zone, ...]

    def zone_at(self, point_px: Sequence[float] | None,
                image_size: tuple[int, int] | None) -> Zone | None:
        if point_px is None or not image_size:
            return None
        width, height = image_size
        u = min(1.0, max(0.0, float(point_px[0]) / width))
        v = min(1.0, max(0.0, float(point_px[1]) / height))
        return next((zone for zone in self.zones if zone.contains(u, v)), None)

    def get(self, zone_id: str) -> Zone:
        return next(zone for zone in self.zones if zone.zone_id == zone_id)

    def registration(self) -> dict[str, dict]:
        """The ``zones`` object of a ``predefined_zones`` camera registration."""
        return {zone.zone_id: {"frame_id": self.frame_id, "x_m": zone.x_m, "y_m": zone.y_m}
                for zone in self.zones}


def parse_zones(raw: Any, frame_id: str) -> ZoneMap:
    if not frame_id:
        raise ZoneError("mapping.frame_id is required for zones")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ZoneError("mapping.zones must be a non-empty list when mapping.mode is zones")
    zones, seen = [], set()
    for index, item in enumerate(raw):
        where = f"mapping.zones[{index}]"
        if not isinstance(item, dict):
            raise ZoneError(f"{where} must be a mapping")
        unknown = set(item) - _KEYS
        if unknown:
            raise ZoneError(f"{where}: unknown keys {sorted(unknown)}")
        zone_id = str(item.get("id") or "").strip()
        if not zone_id:
            raise ZoneError(f"{where}: id is required")
        if zone_id in seen:
            raise ZoneError(f"{where}: duplicate zone id {zone_id!r}")
        seen.add(zone_id)
        x_m, y_m = _number(item.get("x_m"), f"{where}.x_m"), _number(item.get("y_m"), f"{where}.y_m")
        polygon = _polygon(item.get("image_polygon"), f"{where}.image_polygon")
        zones.append(Zone(zone_id, x_m, y_m, polygon))
    return ZoneMap(frame_id, tuple(zones))


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ZoneError(f"{where} must be a finite number")
    return float(value)


def _polygon(value: Any, where: str) -> tuple[tuple[float, float], ...]:
    if value is None:
        return FULL_FRAME
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise ZoneError(f"{where} needs at least 3 [u, v] points")
    points = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ZoneError(f"{where}: each point is [u, v]")
        u, v = _number(point[0], where), _number(point[1], where)
        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            raise ZoneError(f"{where}: coordinates are normalized to 0..1")
        points.append((u, v))
    return tuple(points)


def _on_segment(u, v, x1, y1, x2, y2, eps: float = 1e-9) -> bool:
    cross = (x2 - x1) * (v - y1) - (y2 - y1) * (u - x1)
    if abs(cross) > eps:
        return False
    return min(x1, x2) - eps <= u <= max(x1, x2) + eps and min(y1, y2) - eps <= v <= max(y1, y2) + eps
