"""Typed pipeline settings, loaded from ``perception/config/pipeline.yaml``.

Unknown keys are rejected so a typo cannot silently fall back to a default.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path
from typing import Any

PERCEPTION_DIR = Path(__file__).resolve().parent
CONFIG_DIR = PERCEPTION_DIR / "config"
MODELS_DIR = PERCEPTION_DIR / "models"
DEFAULT_CONFIG = CONFIG_DIR / "pipeline.yaml"


@dataclass(frozen=True)
class CameraSettings:
    index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30
    reconnect_after_s: float = 2.0
    retry_interval_s: float = 1.0


@dataclass(frozen=True)
class DetectorSettings:
    model: str = "yolo11n-pose.pt"
    conf: float = 0.25
    imgsz: int = 640
    device: str = "auto"
    tracker: str = "bytetrack.yaml"


@dataclass(frozen=True)
class MappingSettings:
    homography: str = "homography.json"
    frame_id: str = "demo-floor-1"


@dataclass(frozen=True)
class TrackingSettings:
    occlusion_grace_s: float = 0.5
    stale_after_s: float = 2.0
    lost_after_s: float = 30.0
    position_alpha: float = 0.35
    confidence_alpha: float = 0.3
    deadband_m: float = 0.15
    posture_window: int = 15
    keypoint_min_conf: float = 0.5
    lying_max_torso_m: float = 0.75
    reattach: bool = True
    reattach_radius_m: float = 0.6


@dataclass(frozen=True)
class PublisherSettings:
    base_url: str = "http://127.0.0.1:8000"
    scenario_id: str = "demo"
    endpoint: str = "camera"
    camera_id: str = "logitech-1"
    actor: str = "perception-askar"
    rate_hz: float = 5.0
    heartbeat_s: float = 1.0
    timeout_s: float = 1.5
    max_backoff_s: float = 5.0
    mock_log: str = "artifacts/mock_observations.jsonl"


@dataclass(frozen=True)
class Settings:
    camera: CameraSettings = field(default_factory=CameraSettings)
    detector: DetectorSettings = field(default_factory=DetectorSettings)
    mapping: MappingSettings = field(default_factory=MappingSettings)
    tracking: TrackingSettings = field(default_factory=TrackingSettings)
    publisher: PublisherSettings = field(default_factory=PublisherSettings)


def _merge(instance: Any, data: Any, prefix: str) -> Any:
    if not isinstance(data, dict):
        raise ValueError(f"Setting {prefix.rstrip('.') or '<root>'} must be a mapping")
    known = {item.name for item in fields(instance)}
    updates = {}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"Unknown setting {prefix}{key}")
        current = getattr(instance, key)
        if is_dataclass(current):
            updates[key] = _merge(current, value, f"{prefix}{key}.")
        elif isinstance(current, bool) != isinstance(value, bool) or (
            isinstance(current, (int, float)) and not isinstance(value, (int, float))
        ):
            raise ValueError(f"Setting {prefix}{key} has the wrong type: {value!r}")
        else:
            updates[key] = type(current)(value) if isinstance(current, float) else value
    return replace(instance, **updates)


def load_settings(path: str | Path | None = DEFAULT_CONFIG) -> Settings:
    settings = Settings()
    if path is None:
        return settings
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    import yaml

    return _merge(settings, yaml.safe_load(path.read_text(encoding="utf-8")) or {}, "")


def resolve(path: str | Path, base: Path = CONFIG_DIR) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else base / candidate


def resolve_model(name: str) -> str:
    """Bare model names live in perception/models/ (downloaded on first use)."""
    candidate = Path(name)
    if candidate.exists() or candidate.parent != Path("."):
        return name
    return str(MODELS_DIR / name)
