"""Recordings for replay and evaluation: ``<name>.mp4`` plus a ``<name>.json`` sidecar.

The sidecar keeps what a replay needs to behave like the live run: the measured
FPS and per-frame times, the camera and mapping in use, and the floor
calibration that was valid for that camera pose. Recordings stay local
(``cv/recordings/`` is git-ignored); never commit captured imagery.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SIDECAR_VERSION = 1


def sidecar_path(video: str | Path) -> Path:
    return Path(video).with_suffix(".json")


def load_sidecar(video: str | Path) -> dict[str, Any] | None:
    path = sidecar_path(video)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != SIDECAR_VERSION:
        raise ValueError(f"{path}: not a version {SIDECAR_VERSION} recording sidecar")
    return data


def write_sidecar(video: str | Path, data: dict[str, Any]) -> Path:
    path = sidecar_path(video)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": SIDECAR_VERSION, **data}, indent=2) + "\n",
                    encoding="utf-8")
    return path


def sidecar_frame_times(sidecar: dict[str, Any] | None) -> list[float] | None:
    times = (sidecar or {}).get("frame_times_s")
    return [float(t) for t in times] if isinstance(times, list) and times else None
