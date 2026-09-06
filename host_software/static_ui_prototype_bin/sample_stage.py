from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SampleStageError(RuntimeError):
    """Base error for sample rotation stage adapters."""


class SampleStageNotImplemented(SampleStageError):
    """Raised when the real sample stage hardware adapter is not available."""


@dataclass
class SampleStagePosition:
    angle_deg: float | None
    verified: bool
    status: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "angleDeg": self.angle_deg,
            "verified": bool(self.verified),
            "status": self.status,
            "raw": dict(self.raw),
        }


class UnimplementedSampleStage:
    """Explicit production boundary until the STM32 sample-stage protocol exists."""

    implemented = False
    mode = "hardware"

    def home_sample_stage(self) -> dict[str, Any]:
        raise SampleStageNotImplemented("sample stage hardware adapter is not implemented")

    def move_sample_stage_to_angle(self, angle_deg: float, *, direction: str = "CW") -> dict[str, Any]:
        raise SampleStageNotImplemented("sample stage hardware adapter is not implemented")

    def wait_sample_stage_stable(self) -> dict[str, Any]:
        raise SampleStageNotImplemented("sample stage hardware adapter is not implemented")

    def get_sample_stage_position(self) -> SampleStagePosition:
        return SampleStagePosition(None, False, status="unavailable")


class SimulatedSampleStage:
    """Deterministic software-only adapter used by tests and offline orchestration."""

    implemented = True
    mode = "simulation"

    def __init__(self) -> None:
        self.angle_deg = 0.0
        self.calls: list[tuple[Any, ...]] = []

    def home_sample_stage(self) -> dict[str, Any]:
        self.calls.append(("home_sample_stage",))
        self.angle_deg = 0.0
        return {"homed": True, "angleDeg": self.angle_deg, "simulation": True}

    def move_sample_stage_to_angle(self, angle_deg: float, *, direction: str = "CW") -> dict[str, Any]:
        self.calls.append(("move_sample_stage_to_angle", float(angle_deg), str(direction).upper()))
        self.angle_deg = float(angle_deg)
        return {
            "targetAngleDeg": self.angle_deg,
            "direction": str(direction).upper(),
            "commandAccepted": True,
            "simulation": True,
        }

    def wait_sample_stage_stable(self) -> dict[str, Any]:
        self.calls.append(("wait_sample_stage_stable",))
        return {"stable": True, "simulation": True}

    def get_sample_stage_position(self) -> SampleStagePosition:
        self.calls.append(("get_sample_stage_position",))
        return SampleStagePosition(
            self.angle_deg,
            True,
            status="verified",
            raw={"simulation": True},
        )
