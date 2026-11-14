"""Monotonic pipeline time with a fixed mapping to UTC wall-clock timestamps.

All internal timing (staleness, occlusion, latency) uses the monotonic clock so
wall-clock adjustments cannot reorder events. Timestamps sent to the backend are
derived from one anchor, so they are monotonic too.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable


class Timebase:
    def __init__(self, monotonic: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.monotonic = monotonic
        self._mono_anchor = monotonic()
        self._wall_anchor = wall()

    def now(self) -> float:
        return self.monotonic()

    def to_wall(self, t: float) -> datetime:
        return self._wall_anchor + timedelta(seconds=t - self._mono_anchor)

    def iso(self, t: float) -> str:
        return iso_utc(self.to_wall(t))


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def session_id(moment: datetime | None = None) -> str:
    """Tracker session prefix; a new process never reuses a previous session's IDs."""
    moment = moment or datetime.now(timezone.utc)
    return "s" + moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
