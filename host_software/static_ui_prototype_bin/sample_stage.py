from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


SAMPLE_STAGE_PROTOCOL_UNKNOWN = "SAMPLE_STAGE_PROTOCOL_UNKNOWN"


class SampleStageError(RuntimeError):
    """Base error for sample rotation stage adapters."""


class SampleStageNotImplemented(SampleStageError):
    """Raised when the real sample stage hardware adapter is not available."""


@dataclass
class SampleStageStatus:
    connected: bool = False
    available: bool = False
    homed: bool | None = False
    current_angle_deg: float | None = None
    target_angle_deg: float | None = None
    moving: bool = False
    last_command: str = ""
    last_error: str | None = SAMPLE_STAGE_PROTOCOL_UNKNOWN
    fault: str | None = None
    status_timestamp: float = field(default_factory=time.time)
    status_revision: int = 0
    hardware_mode: str = "hardware"
    implemented: bool = False
    protocol_known: bool = False
    controller: str = "unknown"
    transport: str = "unknown"
    position_feedback_supported: bool = False
    home_supported: bool | None = None
    stop_supported: bool | None = None
    origin_supported: bool | None = None
    automatic_homing: bool | None = None
    notes: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": bool(self.connected),
            "available": bool(self.available),
            "homed": self.homed,
            "currentAngleDeg": self.current_angle_deg,
            "targetAngleDeg": self.target_angle_deg,
            "moving": bool(self.moving),
            "lastCommand": self.last_command,
            "lastError": self.last_error,
            "fault": self.fault,
            "statusTimestamp": self.status_timestamp,
            "statusRevision": int(self.status_revision),
            "hardwareMode": self.hardware_mode,
            "implemented": bool(self.implemented),
            "protocolKnown": bool(self.protocol_known),
            "controller": self.controller,
            "transport": self.transport,
            "positionFeedbackSupported": bool(self.position_feedback_supported),
            "homeSupported": self.home_supported,
            "stopSupported": self.stop_supported,
            "originSupported": self.origin_supported,
            "automaticHoming": self.automatic_homing,
            "notes": list(self.notes),
            "raw": dict(self.raw),
        }


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

    def __init__(self, *, reason: str = SAMPLE_STAGE_PROTOCOL_UNKNOWN) -> None:
        self.reason = str(reason or SAMPLE_STAGE_PROTOCOL_UNKNOWN)
        self._revision = 0
        self._last_command = ""

    @property
    def is_connected(self) -> bool:
        return False

    @property
    def is_moving(self) -> bool:
        return False

    def connect(self) -> dict[str, Any]:
        return self._not_implemented("connect")

    def disconnect(self) -> dict[str, Any]:
        self._last_command = "disconnect"
        self._revision += 1
        return {"connected": False, "available": False, "reason": self.reason}

    def home(self) -> dict[str, Any]:
        return self._not_implemented("home")

    def move_to(self, angle_deg: float, *, direction: str = "CW") -> dict[str, Any]:
        del angle_deg, direction
        return self._not_implemented("move_to")

    def move_relative(self, delta_deg: float, *, direction: str = "CW") -> dict[str, Any]:
        del delta_deg, direction
        return self._not_implemented("move_relative")

    def stop(self) -> dict[str, Any]:
        return self._not_implemented("stop")

    def safe_stop(self) -> dict[str, Any]:
        self._last_command = "safe_stop"
        self._revision += 1
        return {
            "commandSent": False,
            "ok": False,
            "reason": self.reason,
            "message": "sample stage protocol is unknown; no hardware stop command was sent",
        }

    def get_status(self) -> SampleStageStatus:
        return SampleStageStatus(
            connected=False,
            available=False,
            homed=False,
            current_angle_deg=None,
            target_angle_deg=None,
            moving=False,
            last_command=self._last_command,
            last_error=self.reason,
            fault=self.reason,
            status_revision=self._revision,
            hardware_mode="hardware",
            implemented=False,
            protocol_known=False,
            controller="unknown",
            transport="unknown",
            position_feedback_supported=False,
            home_supported=None,
            stop_supported=None,
            origin_supported=None,
            automatic_homing=None,
            notes=[
                "Real sample stage controller, transport, baudrate and command protocol are not documented in this repository.",
                "Do not reuse the current STM32 filter-wheel motor as SampleStage.",
            ],
        )

    def get_position(self) -> SampleStagePosition:
        return self.get_sample_stage_position()

    def home_sample_stage(self) -> dict[str, Any]:
        return self.home()

    def move_sample_stage_to_angle(self, angle_deg: float, *, direction: str = "CW") -> dict[str, Any]:
        return self.move_to(angle_deg, direction=direction)

    def wait_sample_stage_stable(self) -> dict[str, Any]:
        return self._not_implemented("wait_stable")

    def get_sample_stage_position(self) -> SampleStagePosition:
        return SampleStagePosition(None, False, status="unavailable")

    def _not_implemented(self, command: str) -> dict[str, Any]:
        self._last_command = command
        self._revision += 1
        raise SampleStageNotImplemented(f"{self.reason}: sample stage hardware adapter is not implemented")


class SimulatedSampleStage:
    """Deterministic software-only adapter used by tests and offline orchestration."""

    implemented = True
    mode = "simulation"

    def __init__(self) -> None:
        self.angle_deg = 0.0
        self.target_angle_deg: float | None = 0.0
        self.connected = True
        self.homed = False
        self.moving = False
        self.status_revision = 0
        self.last_command = ""
        self.last_error: str | None = None
        self.calls: list[tuple[Any, ...]] = []

    @property
    def is_connected(self) -> bool:
        return self.connected

    @property
    def is_moving(self) -> bool:
        return self.moving

    def connect(self) -> dict[str, Any]:
        self.calls.append(("connect",))
        self.connected = True
        self._mark("connect")
        return {"connected": True, "available": True, "simulation": True}

    def disconnect(self) -> dict[str, Any]:
        self.calls.append(("disconnect",))
        self.connected = False
        self._mark("disconnect")
        return {"connected": False, "available": False, "simulation": True}

    def home(self) -> dict[str, Any]:
        self.calls.append(("home_sample_stage",))
        self.angle_deg = 0.0
        self.target_angle_deg = 0.0
        self.homed = True
        self._mark("home")
        return {"homed": True, "angleDeg": self.angle_deg, "simulation": True}

    def move_to(self, angle_deg: float, *, direction: str = "CW") -> dict[str, Any]:
        self.calls.append(("move_sample_stage_to_angle", float(angle_deg), str(direction).upper()))
        self.target_angle_deg = _normalize_angle(float(angle_deg))
        self.moving = True
        self.angle_deg = self.target_angle_deg
        self.moving = False
        self._mark("move_to")
        return {
            "targetAngleDeg": self.angle_deg,
            "direction": str(direction).upper(),
            "commandAccepted": True,
            "simulation": True,
        }

    def move_relative(self, delta_deg: float, *, direction: str = "CW") -> dict[str, Any]:
        target = _normalize_angle(self.angle_deg + float(delta_deg))
        self.calls.append(("move_sample_stage_relative", float(delta_deg), str(direction).upper()))
        return self.move_to(target, direction=direction)

    def stop(self) -> dict[str, Any]:
        self.calls.append(("stop",))
        self.moving = False
        self._mark("stop")
        return {"stopped": True, "simulation": True}

    def safe_stop(self) -> dict[str, Any]:
        self.calls.append(("safe_stop",))
        self.moving = False
        self._mark("safe_stop")
        return {"stopped": True, "safeStop": True, "simulation": True}

    def get_status(self) -> SampleStageStatus:
        return SampleStageStatus(
            connected=self.connected,
            available=self.connected,
            homed=self.homed,
            current_angle_deg=self.angle_deg,
            target_angle_deg=self.target_angle_deg,
            moving=self.moving,
            last_command=self.last_command,
            last_error=self.last_error,
            fault=None,
            status_revision=self.status_revision,
            hardware_mode="simulation",
            implemented=True,
            protocol_known=True,
            controller="simulated",
            transport="in_process",
            position_feedback_supported=True,
            home_supported=True,
            stop_supported=True,
            origin_supported=True,
            automatic_homing=True,
            notes=["Software-only fake adapter; not hardware evidence."],
            raw={"simulation": True},
        )

    def get_position(self) -> SampleStagePosition:
        return self.get_sample_stage_position()

    def home_sample_stage(self) -> dict[str, Any]:
        return self.home()

    def move_sample_stage_to_angle(self, angle_deg: float, *, direction: str = "CW") -> dict[str, Any]:
        return self.move_to(angle_deg, direction=direction)

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

    def _mark(self, command: str) -> None:
        self.last_command = command
        self.last_error = None
        self.status_revision += 1


def _normalize_angle(value: float) -> float:
    normalized = value % 360.0
    if abs(normalized) < 1e-9:
        return 0.0
    return normalized
