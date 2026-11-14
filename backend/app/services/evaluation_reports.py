"""Read-only access to committed synthetic evaluation artifacts."""

import hashlib
import json
from pathlib import Path
from typing import Literal, NoReturn

from pydantic import BaseModel, ConfigDict


REPORT_FILES = {
    "allocation": "benchmark.json",
    "uncertainty": "uncertainty.json",
    "counterfactuals": "scenario_comparison.json",
}
REPORT_ROOT = Path(__file__).resolve().parents[3] / "reports"


def _reject_nonfinite(value: str) -> NoReturn:
    raise ValueError(f"Non-finite JSON value is not a measurement: {value}")


class EvaluationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    available: bool
    sha256: str | None = None
    report: dict | None = None
    unavailable_reason: str | None = None


class EvaluationIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["simulation"] = "simulation"
    synthetic_only: Literal[True] = True
    interpretation: str = (
        "Recorded synthetic measurements, not live telemetry, clinical evidence, "
        "or authorization. Compare outcomes only under the stated report assumptions."
    )
    artifacts: list[EvaluationArtifact]


def evaluation_index(root: Path = REPORT_ROOT) -> EvaluationIndex:
    artifacts = []
    for name, filename in REPORT_FILES.items():
        try:
            with (root / filename).open("rb") as source:
                encoded = source.read(2_000_001)
            if len(encoded) > 2_000_000:
                raise ValueError("Report exceeds bounded artifact size")
            report = json.loads(encoded, parse_constant=_reject_nonfinite)
            if not isinstance(report, dict):
                raise ValueError("Report must be a JSON object")
        except (OSError, ValueError, RecursionError):
            artifacts.append(EvaluationArtifact(
                name=name, available=False,
                unavailable_reason="No valid committed report is available; no result is inferred.",
            ))
        else:
            artifacts.append(EvaluationArtifact(
                name=name, available=True, report=report,
                sha256=hashlib.sha256(encoded).hexdigest(),
            ))
    return EvaluationIndex(artifacts=artifacts)
