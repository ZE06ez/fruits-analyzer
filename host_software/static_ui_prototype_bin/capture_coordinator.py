from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from rotation_plan import build_capture_rotation_plan
from sample_stage import SampleStageNotImplemented, SampleStagePosition, SimulatedSampleStage, UnimplementedSampleStage


class CaptureState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    WAITING_OPERATOR = "waiting_operator"
    CALIBRATING = "calibrating"
    CAPTURING = "capturing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CaptureStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class CaptureCoordinatorError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        step: str = "",
        code: str = "capture_error",
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.step = step
        self.code = code
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "code": self.code,
            "message": str(self),
            "cause": str(self.cause) if self.cause else "",
        }


class CaptureCancelled(CaptureCoordinatorError):
    def __init__(self, *, step: str = "") -> None:
        super().__init__("采集已取消", step=step, code="capture_cancelled")


class CaptureStepTimeout(CaptureCoordinatorError):
    def __init__(self, *, step: str, timeout_ms: int, duration_ms: int) -> None:
        super().__init__(
            f"采集步骤超时：{step}",
            step=step,
            code="step_timeout",
        )
        self.timeout_ms = timeout_ms
        self.duration_ms = duration_ms

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["timeoutMs"] = self.timeout_ms
        data["durationMs"] = self.duration_ms
        return data


class CaptureSafetyError(CaptureCoordinatorError):
    def __init__(self, message: str, *, step: str = "", cause: Exception | None = None) -> None:
        super().__init__(message, step=step, code="safety_error", cause=cause)


class CaptureReferenceType(str, Enum):
    SAMPLE = "sample"
    DARK = "dark"
    WHITE = "white"


@dataclass
class CaptureStep:
    id: str
    name: str
    status: CaptureStepStatus = CaptureStepStatus.PENDING
    started_at: float | None = None
    finished_at: float | None = None
    duration_ms: int | None = None
    timeout_ms: int | None = None
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "durationMs": self.duration_ms,
            "timeoutMs": self.timeout_ms,
            "error": self.error,
            "result": self.result,
        }


@dataclass
class CaptureStepPlan:
    id: str
    name: str
    state: CaptureState
    timeout_ms: int | None = None
    action: Callable[[], dict[str, Any] | None] | None = None


@dataclass(frozen=True)
class MultispectralBandPlan:
    band_id: str
    wheel_position: int
    wavelength_nm: int | None
    enabled: bool = True
    bandwidth_nm: float | None = None
    exposure_us: float | None = None
    gain: float | None = None
    source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bandId": self.band_id,
            "wheelPosition": self.wheel_position,
            "wavelengthNm": self.wavelength_nm,
            "enabled": self.enabled,
            "bandwidthNm": self.bandwidth_nm,
            "exposureUs": self.exposure_us,
            "gain": self.gain,
            "source": dict(self.source),
        }


@dataclass(frozen=True)
class MultispectralCapturePlan:
    bands: list[MultispectralBandPlan]
    filter_config_source: str = ""
    filter_config_version: str = ""
    development_config: bool = True
    settling_ms: int = 250

    def enabled_bands(self) -> list[MultispectralBandPlan]:
        return [band for band in self.bands if band.enabled]

    def to_dict(self) -> dict[str, Any]:
        return {
            "filterConfigSource": self.filter_config_source,
            "filterConfigVersion": self.filter_config_version,
            "developmentConfig": self.development_config,
            "settlingMs": self.settling_ms,
            "bands": [band.to_dict() for band in self.bands],
            "enabledBandIds": [band.band_id for band in self.enabled_bands()],
        }


@dataclass(frozen=True)
class CalibrationSet:
    calibration_id: str
    created_at: float
    camera_stable_id: str = ""
    camera_identity: dict[str, Any] = field(default_factory=dict)
    filter_config_source: str = ""
    filter_config_version: str = ""
    development_config: bool = True
    bands: list[dict[str, Any]] = field(default_factory=list)
    dark_frames: list[dict[str, Any]] = field(default_factory=list)
    white_frames: list[dict[str, Any]] = field(default_factory=list)
    completed_dark_bands: list[str] = field(default_factory=list)
    completed_white_bands: list[str] = field(default_factory=list)
    missing_dark_bands: list[str] = field(default_factory=list)
    missing_white_bands: list[str] = field(default_factory=list)
    status: str = "incomplete"
    calibration_complete: bool = False
    partial_capture: bool = False
    same_band_settings_matched: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "calibrationId": self.calibration_id,
            "createdAt": self.created_at,
            "cameraStableId": self.camera_stable_id,
            "cameraIdentity": dict(self.camera_identity),
            "filterConfigSource": self.filter_config_source,
            "filterConfigVersion": self.filter_config_version,
            "developmentConfig": self.development_config,
            "bands": [dict(band) for band in self.bands],
            "enabledBandIds": [str(band.get("bandId") or "") for band in self.bands if band.get("enabled", True)],
            "dark": {
                "status": "completed" if self.dark_frames and not self.missing_dark_bands else "incomplete",
                "frames": [dict(frame) for frame in self.dark_frames],
                "completedBands": list(self.completed_dark_bands),
                "missingBands": list(self.missing_dark_bands),
            },
            "white": {
                "status": "completed" if self.white_frames and not self.missing_white_bands else "incomplete",
                "frames": [dict(frame) for frame in self.white_frames],
                "completedBands": list(self.completed_white_bands),
                "missingBands": list(self.missing_white_bands),
            },
            "completedDarkBands": list(self.completed_dark_bands),
            "completedWhiteBands": list(self.completed_white_bands),
            "missingDarkBands": list(self.missing_dark_bands),
            "missingWhiteBands": list(self.missing_white_bands),
            "status": self.status,
            "calibrationComplete": self.calibration_complete,
            "partialCapture": self.partial_capture,
            "sameBandSettingsMatched": self.same_band_settings_matched,
        }


@dataclass(frozen=True)
class SampleViewPlan:
    view_id: str
    capture_order: int
    logical_angle_deg: float
    mechanical_angle_deg: float
    direction: str = "CW"
    closure_view: bool = False
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "viewId": self.view_id,
            "view_id": self.view_id,
            "captureOrder": int(self.capture_order),
            "capture_order": int(self.capture_order),
            "logicalAngleDeg": float(self.logical_angle_deg),
            "logical_angle_deg": float(self.logical_angle_deg),
            "mechanicalAngleDeg": float(self.mechanical_angle_deg),
            "mechanical_angle_deg": float(self.mechanical_angle_deg),
            "direction": self.direction,
            "closureView": bool(self.closure_view),
            "closure_view": bool(self.closure_view),
            "status": self.status,
        }


@dataclass(frozen=True)
class SampleMultiViewPlan:
    views: list[SampleViewPlan]
    rotation_plan: dict[str, Any]
    sample_stage_mode: str = "hardware"
    sample_stage_settling_ms: int = 300
    return_home: bool = True
    calibration_id: str | None = None

    def normal_views(self) -> list[SampleViewPlan]:
        return [view for view in self.views if not view.closure_view]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sampleStageMode": self.sample_stage_mode,
            "sampleStageSettlingMs": int(self.sample_stage_settling_ms),
            "returnHome": bool(self.return_home),
            "calibrationId": self.calibration_id,
            "viewCount": len(self.normal_views()),
            "totalCaptureViews": len(self.views),
            "rotationPlan": dict(self.rotation_plan),
            "views": [view.to_dict() for view in self.views],
        }


@dataclass(frozen=True)
class TrueCapturePlan:
    sample_id: str = ""
    capture_mode: str = "single_view"
    rgb_enabled: bool = True
    multispectral_enabled: bool = True
    calibration_mode: str = "existing"
    calibration_id: str | None = None
    capture_dark: bool = False
    capture_white: bool = False
    rotation_plan: dict[str, Any] = field(default_factory=dict)
    band_plan: MultispectralCapturePlan | list[MultispectralBandPlan] | list[dict[str, Any]] | None = None
    filter_config_path: str | Path | None = None
    settling_ms: int | None = None
    sample_stage_settling_ms: int = 300
    rgb_dir_name: str = "rgb"
    multispectral_dir_name: str = "multispectral"
    output_dir: str | Path | None = None
    return_home: bool = True
    require_calibration: bool = True
    sample_stage_mode: str = "hardware"
    operator_confirmed_dark: bool = False
    operator_confirmed_white: bool = False
    rgb_led_mask: int = 0x03
    tungsten_mask: int = 0x03

    def to_dict(self) -> dict[str, Any]:
        return {
            "sampleId": self.sample_id,
            "captureMode": self.capture_mode,
            "singleView": self.capture_mode == "single_view",
            "multiView": self.capture_mode == "multi_view",
            "rgbEnabled": self.rgb_enabled,
            "multispectralEnabled": self.multispectral_enabled,
            "calibrationMode": self.calibration_mode,
            "calibrationId": self.calibration_id,
            "captureDark": self.capture_dark,
            "captureWhite": self.capture_white,
            "rotationPlan": dict(self.rotation_plan),
            "rgbDirName": self.rgb_dir_name,
            "multispectralDirName": self.multispectral_dir_name,
            "outputDir": str(self.output_dir or ""),
            "returnHome": self.return_home,
            "requireCalibration": self.require_calibration,
            "sampleStageMode": self.sample_stage_mode,
            "sampleStageSettlingMs": self.sample_stage_settling_ms,
        }


@dataclass
class CaptureRun:
    capture_id: str
    sample_id: str = ""
    state: CaptureState = CaptureState.IDLE
    current_step: str | None = None
    progress: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    steps: list[CaptureStep] = field(default_factory=list)
    error: dict[str, Any] | None = None
    cancel_requested: bool = False
    output_dir: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        current_step = next((step for step in self.steps if step.id == self.current_step), None)
        current_view = _current_view_from_step(self.current_step or "")
        current_band = _current_band_from_step(self.current_step or "")
        views = self.metadata.get("views") if isinstance(self.metadata, dict) else []
        enabled_bands = []
        sequence = self.metadata.get("multispectralSequence") if isinstance(self.metadata, dict) else {}
        if isinstance(sequence, dict):
            enabled_bands = sequence.get("enabledBandIds") or sequence.get("enabledBands") or []
        return {
            "captureId": self.capture_id,
            "sampleId": self.sample_id,
            "state": self.state.value,
            "status": self.state.value,
            "currentStep": self.current_step,
            "stepName": current_step.name if current_step is not None else "",
            "progress": self.progress,
            "currentView": current_view,
            "totalViews": len(views) if isinstance(views, list) else None,
            "currentBand": current_band,
            "totalBands": len(enabled_bands) if isinstance(enabled_bands, list) else None,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "error": self.error,
            "cancelRequested": self.cancel_requested,
            "outputDir": self.output_dir,
            "steps": [step.to_dict() for step in self.steps],
            "metadata": self.metadata,
            "mode": self.mode,
        }


class CaptureCoordinator:
    """Synchronous capture orchestration skeleton.

    This class owns capture state and step execution boundaries only. It does
    not directly open cameras, write serial bytes, or save production frames.
    """

    DEFAULT_STEP_TIMEOUTS_MS = {
        "prepare": 30_000,
        "safety_check": 10_000,
        "capture": 60_000,
        "finalize": 30_000,
        "write_metadata": 10_000,
        "hardware_precheck": 10_000,
        "door_close": 15_000,
        "fan_on": 5_000,
        "capture_safety_check": 5_000,
        "rgb_light_prepare": 5_000,
        "multispectral_light_prepare": 5_000,
        "lighting_shutdown": 5_000,
        "dark_lighting_shutdown": 5_000,
        "lighting_off_verify": 5_000,
        "operator_confirmation": 30_000,
        "rgb_capture": 60_000,
        "multispectral_capture": 60_000,
        "filter_wheel_home": 15_000,
        "filter_wheel_move": 10_000,
        "filter_wheel_position_verify": 5_000,
        "filter_wheel_settle": 5_000,
        "band_camera_settings": 5_000,
        "multispectral_sequence": 180_000,
        "sample_stage_home": 30_000,
        "sample_stage_move": 30_000,
        "sample_stage_position_verify": 10_000,
        "sample_stage_settle": 10_000,
        "view_complete_verify": 5_000,
        "return_home": 30_000,
    }

    def __init__(
        self,
        *,
        camera_manager: Any | None = None,
        device_manager: Any | None = None,
        hardware_controller: Any | None = None,
        sample_stage_controller: Any | None = None,
        safe_stop_callback: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
        capture_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.camera_manager = camera_manager
        self.device_manager = device_manager
        self.hardware_controller = hardware_controller
        self.sample_stage_controller = sample_stage_controller
        self.safe_stop_callback = safe_stop_callback
        self.clock = clock
        self.sleep_fn = sleep_fn
        self.capture_id_factory = capture_id_factory or (lambda: time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
        self._run = CaptureRun(capture_id="")
        self._active_view: SampleViewPlan | None = None
        self._active_multiview_plan: SampleMultiViewPlan | None = None
        self._active_multispectral_view_id: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return self._run.to_dict()

    def request_cancel(self) -> dict[str, Any]:
        self._run.cancel_requested = True
        if self._run.state == CaptureState.IDLE:
            self._run.state = CaptureState.CANCELLED
            self._run.finished_at = self.clock()
        elif self._run.state not in {CaptureState.COMPLETED, CaptureState.CANCELLED, CaptureState.FAILED}:
            self._run.state = CaptureState.CANCELLING
        return self.snapshot()

    def run_dry_run(
        self,
        *,
        sample_id: str = "",
        output_dir: str | Path | None = None,
        steps: list[CaptureStepPlan] | None = None,
    ) -> dict[str, Any]:
        plans = steps or self.default_dry_run_steps()
        return self._run_steps(sample_id=sample_id, output_dir=output_dir, steps=plans)

    def run_preparation(
        self,
        *,
        mode: str,
        sample_id: str = "",
        output_dir: str | Path | None = None,
        rgb_led_mask: int = 0x03,
        tungsten_mask: int = 0x03,
        steps: list[CaptureStepPlan] | None = None,
    ) -> dict[str, Any]:
        """Run hardware safety preparation without opening real capture."""

        normalized_mode = self._normalize_mode(mode)
        plans = steps or self.hardware_preparation_steps(
            mode=normalized_mode,
            rgb_led_mask=rgb_led_mask,
            tungsten_mask=tungsten_mask,
        )
        return self._run_steps(
            sample_id=sample_id,
            output_dir=output_dir,
            steps=plans,
            mode=normalized_mode,
        )

    def run_rgb_capture(
        self,
        *,
        sample_id: str = "",
        output_dir: str | Path,
        rgb_dir_name: str = "rgb",
        view_index: int = 0,
        view_id: str | None = None,
        filename: str | None = None,
        rgb_led_mask: int = 0x03,
    ) -> dict[str, Any]:
        """Run protected RGB preparation, capture one RGB frame, save PNG, and shut light down."""

        rgb_dir_name = self._validate_direct_dir_name(rgb_dir_name, field="rgb_dir_name")
        view_index = int(view_index)
        view_id = view_id or f"view_{view_index:03d}"
        filename = filename or f"rgb_view_{view_index:03d}.png"
        self._validate_filename(filename)
        preparation = self.hardware_preparation_steps(mode="rgb", rgb_led_mask=rgb_led_mask)
        plans = []
        for plan in preparation:
            if plan.id == "lighting_shutdown":
                plans.append(
                    CaptureStepPlan(
                        "rgb_capture",
                        "采集并保存 RGB 正式帧",
                        CaptureState.CAPTURING,
                        self.DEFAULT_STEP_TIMEOUTS_MS["rgb_capture"],
                        action=lambda: self._capture_rgb_frame(
                            rgb_dir_name=rgb_dir_name,
                            view_index=view_index,
                            view_id=view_id,
                            filename=filename,
                        ),
                    )
                )
            plans.append(plan)
        return self._run_steps(
            sample_id=sample_id,
            output_dir=output_dir,
            steps=plans,
            mode="rgb",
        )

    def run_multispectral_capture(
        self,
        *,
        sample_id: str = "",
        output_dir: str | Path,
        multispectral_dir_name: str = "multispectral",
        frame_index: int = 0,
        view_id: str | None = None,
        filename: str | None = None,
        tungsten_mask: int = 0x03,
    ) -> dict[str, Any]:
        """Run protected DVP2 mono single-frame capture, save raw PNG, and shut light down."""

        multispectral_dir_name = self._validate_direct_dir_name(multispectral_dir_name, field="multispectral_dir_name")
        frame_index = int(frame_index)
        view_id = view_id or f"view_{frame_index:03d}"
        filename = filename or f"multispectral_frame_{frame_index:03d}.png"
        self._validate_filename(filename, role="多光谱")
        preparation = self.hardware_preparation_steps(mode="multispectral", tungsten_mask=tungsten_mask)
        plans = []
        for plan in preparation:
            if plan.id == "lighting_shutdown":
                plans.append(
                    CaptureStepPlan(
                        "multispectral_capture",
                        "采集并保存多光谱正式单帧",
                        CaptureState.CAPTURING,
                        self.DEFAULT_STEP_TIMEOUTS_MS["multispectral_capture"],
                        action=lambda: self._capture_multispectral_frame(
                            multispectral_dir_name=multispectral_dir_name,
                            frame_index=frame_index,
                            view_id=view_id,
                            filename=filename,
                        ),
                    )
                )
            plans.append(plan)
        return self._run_steps(
            sample_id=sample_id,
            output_dir=output_dir,
            steps=plans,
            mode="multispectral",
        )

    def run_multispectral_sequence(
        self,
        *,
        sample_id: str = "",
        output_dir: str | Path,
        multispectral_dir_name: str = "multispectral",
        band_plan: MultispectralCapturePlan | list[MultispectralBandPlan] | list[dict[str, Any]] | None = None,
        filter_config_path: str | Path | None = None,
        settling_ms: int | None = None,
        tungsten_mask: int = 0x03,
    ) -> dict[str, Any]:
        """Run one protected DVP2 band sequence for a single sample view."""

        multispectral_dir_name = self._validate_direct_dir_name(multispectral_dir_name, field="multispectral_dir_name")
        plan = self._build_multispectral_capture_plan(
            band_plan=band_plan,
            filter_config_path=filter_config_path,
            settling_ms=settling_ms,
        )
        enabled = plan.enabled_bands()
        if not enabled:
            raise ValueError("multispectral sequence requires at least one enabled band")

        preparation = self.hardware_preparation_steps(mode="multispectral", tungsten_mask=tungsten_mask)
        sequence_steps = self._multispectral_band_sequence_steps(
            capture_type=CaptureReferenceType.SAMPLE,
            plan=plan,
            target_dir_name=multispectral_dir_name,
        )

        steps: list[CaptureStepPlan] = []
        for plan_step in preparation:
            if plan_step.id == "lighting_shutdown":
                steps.extend(sequence_steps)
            steps.append(plan_step)
        return self._run_steps(
            sample_id=sample_id,
            output_dir=output_dir,
            steps=steps,
            mode="multispectral_sequence",
        )

    def run_dark_reference_capture(
        self,
        *,
        sample_id: str = "",
        output_dir: str | Path,
        band_plan: MultispectralCapturePlan | list[MultispectralBandPlan] | list[dict[str, Any]] | None = None,
        filter_config_path: str | Path | None = None,
        settling_ms: int | None = None,
        calibration_id: str | None = None,
        operator_confirmed: bool = False,
        confirmation_callback: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Capture dark reference raw frames for every enabled multispectral band."""

        plan = self._build_multispectral_capture_plan(
            band_plan=band_plan,
            filter_config_path=filter_config_path,
            settling_ms=settling_ms,
        )
        if not plan.enabled_bands():
            raise ValueError("dark reference capture requires at least one enabled band")
        calibration_id = self._normalize_calibration_id(calibration_id)
        steps = self._dark_reference_preparation_steps(
            operator_confirmed=operator_confirmed,
            confirmation_callback=confirmation_callback,
        )
        steps.extend(self._multispectral_band_sequence_steps(
            capture_type=CaptureReferenceType.DARK,
            plan=plan,
            target_dir_name="calibration/dark",
            calibration_id=calibration_id,
        ))
        steps.append(CaptureStepPlan(
            "lighting_shutdown",
            "关闭采集光源",
            CaptureState.FINALIZING,
            self.DEFAULT_STEP_TIMEOUTS_MS["lighting_shutdown"],
            action=self._shutdown_lighting,
        ))
        result = self._run_steps(
            sample_id=sample_id,
            output_dir=output_dir,
            steps=steps,
            mode="dark_reference",
        )
        return self._finalize_calibration_capture(
            plan=plan,
            capture_type=CaptureReferenceType.DARK,
            calibration_id=calibration_id,
            previous=result,
        )

    def run_white_reference_capture(
        self,
        *,
        sample_id: str = "",
        output_dir: str | Path,
        band_plan: MultispectralCapturePlan | list[MultispectralBandPlan] | list[dict[str, Any]] | None = None,
        filter_config_path: str | Path | None = None,
        settling_ms: int | None = None,
        tungsten_mask: int = 0x03,
        calibration_id: str | None = None,
        operator_confirmed: bool = False,
        confirmation_callback: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Capture white reference raw frames for every enabled multispectral band."""

        plan = self._build_multispectral_capture_plan(
            band_plan=band_plan,
            filter_config_path=filter_config_path,
            settling_ms=settling_ms,
        )
        if not plan.enabled_bands():
            raise ValueError("white reference capture requires at least one enabled band")
        calibration_id = self._normalize_calibration_id(calibration_id)
        steps = self._white_reference_preparation_steps(
            tungsten_mask=tungsten_mask,
            operator_confirmed=operator_confirmed,
            confirmation_callback=confirmation_callback,
        )
        steps.extend(self._multispectral_band_sequence_steps(
            capture_type=CaptureReferenceType.WHITE,
            plan=plan,
            target_dir_name="calibration/white",
            calibration_id=calibration_id,
        ))
        steps.append(CaptureStepPlan(
            "lighting_shutdown",
            "关闭采集光源",
            CaptureState.FINALIZING,
            self.DEFAULT_STEP_TIMEOUTS_MS["lighting_shutdown"],
            action=self._shutdown_lighting,
        ))
        result = self._run_steps(
            sample_id=sample_id,
            output_dir=output_dir,
            steps=steps,
            mode="white_reference",
        )
        return self._finalize_calibration_capture(
            plan=plan,
            capture_type=CaptureReferenceType.WHITE,
            calibration_id=calibration_id,
            previous=result,
        )

    def run_sample_multiview_capture(
        self,
        *,
        sample_id: str = "",
        output_dir: str | Path,
        rgb_dir_name: str = "rgb",
        multispectral_dir_name: str = "multispectral",
        rotation_plan: dict[str, Any] | None = None,
        sample_rotation: dict[str, Any] | None = None,
        band_plan: MultispectralCapturePlan | list[MultispectralBandPlan] | list[dict[str, Any]] | None = None,
        filter_config_path: str | Path | None = None,
        settling_ms: int | None = None,
        sample_stage_settling_ms: int = 300,
        sample_stage_mode: str = "hardware",
        return_home: bool = True,
        calibration_id: str | None = None,
        require_calibration: bool = False,
        rgb_led_mask: int = 0x03,
        tungsten_mask: int = 0x03,
    ) -> dict[str, Any]:
        """Capture one sample as multiple views, each with RGB plus a multispectral sequence."""

        rgb_dir_name = self._validate_direct_dir_name(rgb_dir_name, field="rgb_dir_name")
        multispectral_dir_name = self._validate_direct_dir_name(multispectral_dir_name, field="multispectral_dir_name")
        band_sequence_plan = self._build_multispectral_capture_plan(
            band_plan=band_plan,
            filter_config_path=filter_config_path,
            settling_ms=settling_ms,
        )
        if not band_sequence_plan.enabled_bands():
            raise ValueError("sample multiview capture requires at least one enabled multispectral band")
        multiview_plan = self._build_sample_multiview_plan(
            rotation_plan=rotation_plan,
            sample_rotation=sample_rotation,
            sample_stage_mode=sample_stage_mode,
            sample_stage_settling_ms=sample_stage_settling_ms,
            return_home=return_home,
            calibration_id=calibration_id,
        )
        if not multiview_plan.views:
            raise ValueError("sample multiview capture requires at least one view")

        steps: list[CaptureStepPlan] = [
            CaptureStepPlan(
                "hardware_precheck",
                "硬件安全预检查",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["hardware_precheck"],
                action=self._hardware_precheck,
            ),
            CaptureStepPlan(
                "calibration_check",
                "检查 CalibrationSet 引用",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["capture_safety_check"],
                action=lambda: self._check_multiview_calibration(
                    calibration_id=calibration_id,
                    require_calibration=require_calibration,
                ),
            ),
            CaptureStepPlan(
                "door_close",
                "关闭升降门",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["door_close"],
                action=self._door_close,
            ),
            CaptureStepPlan(
                "fan_on",
                "开启风扇",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["fan_on"],
                action=self._fan_on,
            ),
        ]
        if self._multiview_requires_sample_stage(multiview_plan):
            steps.append(CaptureStepPlan(
                "sample_stage_home",
                "样品台 HOME",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["sample_stage_home"],
                action=lambda: self._sample_stage_home(multiview_plan),
            ))

        for view_index, view in enumerate(multiview_plan.views):
            view_dir = f"views/{view.view_id}"
            rgb_view_dir = f"{view_dir}/rgb"
            ms_view_dir = f"{view_dir}/multispectral"
            steps.extend([
                CaptureStepPlan(
                    f"view_begin:{view.view_id}",
                    f"开始视角 {view.view_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["capture_safety_check"],
                    action=lambda view=view, view_index=view_index: self._begin_sample_view(view, view_index),
                ),
                CaptureStepPlan(
                    f"sample_stage_move:{view.view_id}",
                    f"移动样品台到 {view.logical_angle_deg:g}°",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["sample_stage_move"],
                    action=lambda view=view, plan=multiview_plan: self._move_sample_stage_to_view(view, plan),
                ),
                CaptureStepPlan(
                    f"sample_stage_position_verify:{view.view_id}",
                    f"确认样品台位置 {view.view_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["sample_stage_position_verify"],
                    action=lambda view=view, plan=multiview_plan: self._verify_sample_stage_position(view, plan),
                ),
                CaptureStepPlan(
                    f"sample_stage_settle:{view.view_id}",
                    f"样品台稳定 {view.view_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["sample_stage_settle"],
                    action=lambda view=view, plan=multiview_plan: self._settle_sample_stage(view, plan),
                ),
                CaptureStepPlan(
                    f"rgb_light_prepare:{view.view_id}",
                    f"准备 RGB 光源 {view.view_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["rgb_light_prepare"],
                    action=lambda mask=rgb_led_mask: self._prepare_rgb_lighting(mask),
                ),
                CaptureStepPlan(
                    f"capture_safety_check:rgb:{view.view_id}",
                    f"确认 RGB 采集安全 {view.view_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["capture_safety_check"],
                    action=lambda: self._capture_safety_check("rgb"),
                ),
                CaptureStepPlan(
                    f"rgb_capture:{view.view_id}",
                    f"采集并保存 RGB {view.view_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["rgb_capture"],
                    action=lambda view=view, view_index=view_index, rgb_view_dir=rgb_view_dir: self._capture_rgb_frame(
                        rgb_dir_name=rgb_view_dir,
                        view_index=view_index,
                        view_id=view.view_id,
                        filename=f"rgb_{view.view_id}.png",
                    ),
                ),
                CaptureStepPlan(
                    f"lighting_shutdown:rgb:{view.view_id}",
                    f"关闭 RGB 光源 {view.view_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["lighting_shutdown"],
                    action=self._shutdown_lighting,
                ),
                CaptureStepPlan(
                    f"multispectral_light_prepare:{view.view_id}",
                    f"准备多光谱光源 {view.view_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["multispectral_light_prepare"],
                    action=lambda mask=tungsten_mask: self._prepare_multispectral_lighting(mask),
                ),
                CaptureStepPlan(
                    f"capture_safety_check:multispectral:{view.view_id}",
                    f"确认多光谱采集安全 {view.view_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["capture_safety_check"],
                    action=lambda: self._capture_safety_check("multispectral"),
                ),
            ])
            steps.extend(self._multispectral_band_sequence_steps(
                capture_type=CaptureReferenceType.SAMPLE,
                plan=band_sequence_plan,
                target_dir_name=ms_view_dir,
                calibration_id=calibration_id,
                view_id=view.view_id,
                view_index=view_index,
            ))
            steps.extend([
                CaptureStepPlan(
                    f"lighting_shutdown:multispectral:{view.view_id}",
                    f"关闭多光谱光源 {view.view_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["lighting_shutdown"],
                    action=self._shutdown_lighting,
                ),
                CaptureStepPlan(
                    f"view_complete_verify:{view.view_id}",
                    f"确认视角完整性 {view.view_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["view_complete_verify"],
                    action=lambda view=view, plan=band_sequence_plan: self._verify_sample_view_complete(view, plan),
                ),
            ])

        if return_home and self._multiview_requires_sample_stage(multiview_plan):
            steps.append(CaptureStepPlan(
                "return_home",
                "样品台返回 HOME",
                CaptureState.FINALIZING,
                self.DEFAULT_STEP_TIMEOUTS_MS["return_home"],
                action=lambda: self._return_sample_stage_home(multiview_plan),
            ))
        steps.append(CaptureStepPlan(
            "lighting_shutdown",
            "关闭采集光源",
            CaptureState.FINALIZING,
            self.DEFAULT_STEP_TIMEOUTS_MS["lighting_shutdown"],
            action=self._shutdown_lighting,
        ))
        steps.append(CaptureStepPlan(
            "multiview_finalize",
            "整理多视角采集 metadata",
            CaptureState.FINALIZING,
            self.DEFAULT_STEP_TIMEOUTS_MS["finalize"],
            action=self._finalize_sample_multiview_capture,
        ))

        self._active_multiview_plan = multiview_plan
        try:
            self._run_steps(
                sample_id=sample_id,
                output_dir=output_dir,
                steps=steps,
                mode="sample_multiview",
            )
            self._write_sample_multiview_files()
            return self.snapshot()
        finally:
            self._active_view = None
            self._active_multiview_plan = None
            self._active_multispectral_view_id = None

    def run_true_capture(self, plan: TrueCapturePlan) -> dict[str, Any]:
        """Orchestrate one formal capture request with existing protected paths."""

        capture_mode = str(plan.capture_mode or "single_view").strip().lower()
        if capture_mode not in {"single_view", "multi_view"}:
            raise ValueError("capture_mode must be single_view or multi_view")
        if not plan.rgb_enabled and not plan.multispectral_enabled:
            raise ValueError("true capture requires RGB or multispectral capture")
        if not plan.output_dir:
            raise ValueError("true capture requires output_dir")

        calibration_id = self._normalize_calibration_id(plan.calibration_id) if (
            plan.capture_dark or plan.capture_white or plan.calibration_id
        ) else None
        orchestration: dict[str, Any] = {
            "plan": plan.to_dict(),
            "calibrationRuns": [],
            "sampleRun": None,
            "captureStatus": "in_progress",
            "offlineDatasetUsed": False,
        }

        if plan.capture_dark:
            dark = self.run_dark_reference_capture(
                sample_id=plan.sample_id,
                output_dir=plan.output_dir,
                band_plan=plan.band_plan,
                filter_config_path=plan.filter_config_path,
                settling_ms=plan.settling_ms,
                calibration_id=calibration_id,
                operator_confirmed=plan.operator_confirmed_dark,
            )
            orchestration["calibrationRuns"].append({
                "type": "dark",
                "state": dark.get("state"),
                "captureId": dark.get("captureId"),
                "error": dark.get("error"),
            })
            if dark.get("state") != CaptureState.COMPLETED.value:
                self._run.metadata["trueCapture"] = {**orchestration, "captureStatus": dark.get("state")}
                return self.snapshot()

        if plan.capture_white:
            white = self.run_white_reference_capture(
                sample_id=plan.sample_id,
                output_dir=plan.output_dir,
                band_plan=plan.band_plan,
                filter_config_path=plan.filter_config_path,
                settling_ms=plan.settling_ms,
                calibration_id=calibration_id,
                operator_confirmed=plan.operator_confirmed_white,
                tungsten_mask=plan.tungsten_mask,
            )
            orchestration["calibrationRuns"].append({
                "type": "white",
                "state": white.get("state"),
                "captureId": white.get("captureId"),
                "error": white.get("error"),
            })
            if white.get("state") != CaptureState.COMPLETED.value:
                self._run.metadata["trueCapture"] = {**orchestration, "captureStatus": white.get("state")}
                return self.snapshot()

        if plan.multispectral_enabled:
            sample_rotation = None if capture_mode == "multi_view" else {"enabled": False}
            sample = self.run_sample_multiview_capture(
                sample_id=plan.sample_id,
                output_dir=plan.output_dir,
                rgb_dir_name=plan.rgb_dir_name,
                multispectral_dir_name=plan.multispectral_dir_name,
                rotation_plan=plan.rotation_plan if capture_mode == "multi_view" else None,
                sample_rotation=sample_rotation,
                band_plan=plan.band_plan,
                filter_config_path=plan.filter_config_path,
                settling_ms=plan.settling_ms,
                sample_stage_settling_ms=plan.sample_stage_settling_ms,
                sample_stage_mode=plan.sample_stage_mode,
                return_home=plan.return_home,
                calibration_id=calibration_id,
                require_calibration=bool(plan.require_calibration and calibration_id),
                rgb_led_mask=plan.rgb_led_mask,
                tungsten_mask=plan.tungsten_mask,
            )
        else:
            sample = self.run_rgb_capture(
                sample_id=plan.sample_id,
                output_dir=plan.output_dir,
                rgb_dir_name=plan.rgb_dir_name,
                rgb_led_mask=plan.rgb_led_mask,
            )
        orchestration["sampleRun"] = {
            "state": sample.get("state"),
            "captureId": sample.get("captureId"),
            "error": sample.get("error"),
        }
        orchestration["captureStatus"] = sample.get("state")
        self._run.metadata["trueCapture"] = orchestration
        self._run.metadata["trueCapturePlan"] = plan.to_dict()
        self._run.metadata["capture_status"] = "completed" if sample.get("state") == "completed" else sample.get("state")
        self._run.metadata["captureIncomplete"] = sample.get("state") != "completed"
        self._write_current_sample_files()
        return self.snapshot()

    def default_dry_run_steps(self) -> list[CaptureStepPlan]:
        return [
            CaptureStepPlan("prepare", "准备采集上下文", CaptureState.PREPARING, self.DEFAULT_STEP_TIMEOUTS_MS["prepare"]),
            CaptureStepPlan("safety_check", "安全状态检查", CaptureState.PREPARING, self.DEFAULT_STEP_TIMEOUTS_MS["safety_check"]),
            CaptureStepPlan("capture", "采集步骤占位", CaptureState.CAPTURING, self.DEFAULT_STEP_TIMEOUTS_MS["capture"]),
            CaptureStepPlan("finalize", "整理采集结果", CaptureState.FINALIZING, self.DEFAULT_STEP_TIMEOUTS_MS["finalize"]),
            CaptureStepPlan("write_metadata", "写入 metadata 骨架", CaptureState.FINALIZING, self.DEFAULT_STEP_TIMEOUTS_MS["write_metadata"]),
        ]

    def hardware_preparation_steps(
        self,
        *,
        mode: str,
        rgb_led_mask: int = 0x03,
        tungsten_mask: int = 0x03,
    ) -> list[CaptureStepPlan]:
        normalized_mode = self._normalize_mode(mode)
        lighting_step = (
            CaptureStepPlan(
                "rgb_light_prepare",
                "准备 RGB 光源",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["rgb_light_prepare"],
                action=lambda: self._prepare_rgb_lighting(rgb_led_mask),
            )
            if normalized_mode == "rgb"
            else CaptureStepPlan(
                "multispectral_light_prepare",
                "准备多光谱光源",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["multispectral_light_prepare"],
                action=lambda: self._prepare_multispectral_lighting(tungsten_mask),
            )
        )
        return [
            CaptureStepPlan(
                "hardware_precheck",
                "硬件安全预检查",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["hardware_precheck"],
                action=self._hardware_precheck,
            ),
            CaptureStepPlan(
                "door_close",
                "关闭升降门",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["door_close"],
                action=self._door_close,
            ),
            CaptureStepPlan(
                "fan_on",
                "开启风扇",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["fan_on"],
                action=self._fan_on,
            ),
            lighting_step,
            CaptureStepPlan(
                "capture_safety_check",
                "确认采集安全 interlock",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["capture_safety_check"],
                action=lambda: self._capture_safety_check(normalized_mode),
            ),
            CaptureStepPlan(
                "lighting_shutdown",
                "关闭采集光源",
                CaptureState.FINALIZING,
                self.DEFAULT_STEP_TIMEOUTS_MS["lighting_shutdown"],
                action=self._shutdown_lighting,
            ),
        ]

    def _dark_reference_preparation_steps(
        self,
        *,
        operator_confirmed: bool,
        confirmation_callback: Callable[[str, dict[str, Any]], bool] | None,
    ) -> list[CaptureStepPlan]:
        return [
            CaptureStepPlan(
                "hardware_precheck",
                "硬件安全预检查",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["hardware_precheck"],
                action=self._hardware_precheck,
            ),
            CaptureStepPlan(
                "door_close",
                "关闭升降门",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["door_close"],
                action=self._door_close,
            ),
            CaptureStepPlan(
                "fan_on",
                "开启风扇",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["fan_on"],
                action=self._fan_on,
            ),
            CaptureStepPlan(
                "dark_lighting_shutdown",
                "关闭暗场采集光源",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["dark_lighting_shutdown"],
                action=self._shutdown_lighting,
            ),
            CaptureStepPlan(
                "lighting_off_verify",
                "确认采集光源已关闭",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["lighting_off_verify"],
                action=self._verify_lighting_off,
            ),
            CaptureStepPlan(
                "operator_confirmation:dark",
                "确认暗场遮光状态",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["operator_confirmation"],
                action=lambda: self._confirm_operator_reference_setup(
                    CaptureReferenceType.DARK,
                    operator_confirmed=operator_confirmed,
                    confirmation_callback=confirmation_callback,
                ),
            ),
        ]

    def _white_reference_preparation_steps(
        self,
        *,
        tungsten_mask: int,
        operator_confirmed: bool,
        confirmation_callback: Callable[[str, dict[str, Any]], bool] | None,
    ) -> list[CaptureStepPlan]:
        return [
            CaptureStepPlan(
                "hardware_precheck",
                "硬件安全预检查",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["hardware_precheck"],
                action=self._hardware_precheck,
            ),
            CaptureStepPlan(
                "door_close",
                "关闭升降门",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["door_close"],
                action=self._door_close,
            ),
            CaptureStepPlan(
                "fan_on",
                "开启风扇",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["fan_on"],
                action=self._fan_on,
            ),
            CaptureStepPlan(
                "operator_confirmation:white",
                "确认白板已放置",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["operator_confirmation"],
                action=lambda: self._confirm_operator_reference_setup(
                    CaptureReferenceType.WHITE,
                    operator_confirmed=operator_confirmed,
                    confirmation_callback=confirmation_callback,
                ),
            ),
            CaptureStepPlan(
                "multispectral_light_prepare",
                "准备多光谱光源",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["multispectral_light_prepare"],
                action=lambda: self._prepare_multispectral_lighting(tungsten_mask),
            ),
            CaptureStepPlan(
                "capture_safety_check",
                "确认采集安全 interlock",
                CaptureState.PREPARING,
                self.DEFAULT_STEP_TIMEOUTS_MS["capture_safety_check"],
                action=lambda: self._capture_safety_check("multispectral"),
            ),
        ]

    def _multispectral_band_sequence_steps(
        self,
        *,
        capture_type: CaptureReferenceType,
        plan: MultispectralCapturePlan,
        target_dir_name: str,
        calibration_id: str | None = None,
        view_id: str | None = None,
        view_index: int | None = None,
    ) -> list[CaptureStepPlan]:
        enabled = plan.enabled_bands()
        sequence_steps: list[CaptureStepPlan] = [
            CaptureStepPlan(
                "filter_wheel_home" if view_id is None else f"filter_wheel_home:{view_id}",
                "滤光轮 HOME" if view_id is None else f"滤光轮 HOME {view_id}",
                CaptureState.CAPTURING,
                self.DEFAULT_STEP_TIMEOUTS_MS["filter_wheel_home"],
                action=lambda: self._filter_wheel_home(
                    plan,
                    capture_type=capture_type,
                    calibration_id=calibration_id,
                    view_id=view_id,
                    view_index=view_index,
                ),
            )
        ]
        for band_index, band in enumerate(enabled):
            sequence_steps.extend([
                CaptureStepPlan(
                    f"filter_wheel_move:{band.band_id}" if view_id is None else f"filter_wheel_move:{view_id}:{band.band_id}",
                    f"滤光轮移动到 {band.band_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["filter_wheel_move"],
                    action=lambda band=band: self._filter_wheel_move_to_band(band),
                ),
                CaptureStepPlan(
                    f"filter_wheel_position_verify:{band.band_id}" if view_id is None else f"filter_wheel_position_verify:{view_id}:{band.band_id}",
                    f"确认滤光轮位置 {band.band_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["filter_wheel_position_verify"],
                    action=lambda band=band: self._verify_filter_wheel_position(band),
                ),
                CaptureStepPlan(
                    f"filter_wheel_settle:{band.band_id}" if view_id is None else f"filter_wheel_settle:{view_id}:{band.band_id}",
                    f"滤光轮稳定 {band.band_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["filter_wheel_settle"],
                    action=lambda band=band, plan=plan: self._settle_filter_wheel(band, plan),
                ),
                CaptureStepPlan(
                    f"band_camera_settings:{band.band_id}" if view_id is None else f"band_camera_settings:{view_id}:{band.band_id}",
                    f"设置多光谱相机参数 {band.band_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["band_camera_settings"],
                    action=lambda band=band: self._apply_band_camera_settings(band),
                ),
                CaptureStepPlan(
                    f"multispectral_capture:{band.band_id}" if view_id is None else f"multispectral_capture:{view_id}:{band.band_id}",
                    f"采集并保存多光谱波段 {band.band_id}",
                    CaptureState.CAPTURING,
                    self.DEFAULT_STEP_TIMEOUTS_MS["multispectral_capture"],
                    action=lambda band=band, band_index=band_index, plan=plan, view_id=view_id, view_index=view_index: self._capture_multispectral_band_frame(
                        multispectral_dir_name=target_dir_name,
                        band=band,
                        band_index=band_index,
                        plan=plan,
                        capture_type=capture_type,
                        calibration_id=calibration_id,
                        view_id=view_id or "view_000",
                        view_index=0 if view_index is None else int(view_index),
                    ),
                ),
            ])
        return sequence_steps

    def build_metadata(self) -> dict[str, Any]:
        camera_status = None
        if self.camera_manager is not None and hasattr(self.camera_manager, "status"):
            try:
                camera_status = self.camera_manager.status()
            except Exception:
                camera_status = None
        existing = self._run.metadata or {}
        return {
            "capture_id": self._run.capture_id or None,
            "sample_id": self._run.sample_id or None,
            "mode": self._run.mode or None,
            "state": self._run.state.value,
            "started_at": self._run.started_at,
            "finished_at": self._run.finished_at,
            "camera_settings": camera_status,
            "bands": list(existing.get("bands") or []),
            "views": list(existing.get("views") or []),
            "frames": list(existing.get("frames") or []),
            "rotationPlan": existing.get("rotationPlan"),
            "sampleRotation": existing.get("sampleRotation"),
            "filterWheel": existing.get("filterWheel"),
            "multiViewPlan": existing.get("multiViewPlan"),
            "multispectralSequences": list(existing.get("multispectralSequences") or []),
            "completedViews": list(existing.get("completedViews") or []),
            "failedView": existing.get("failedView"),
            "pendingViews": list(existing.get("pendingViews") or []),
            "multiViewCaptureComplete": existing.get("multiViewCaptureComplete"),
            "partialCapture": existing.get("partialCapture"),
            "cancelled": existing.get("cancelled"),
            "returnedHome": existing.get("returnedHome"),
            "homeStatus": existing.get("homeStatus"),
            "sampleIdGroupingRequired": existing.get("sampleIdGroupingRequired"),
            "sampleIdGroupingRule": existing.get("sampleIdGroupingRule"),
            "multispectralSequence": existing.get("multispectralSequence"),
            "multispectralSequenceComplete": bool(existing.get("multispectralSequenceComplete")),
            "captureType": existing.get("captureType"),
            "calibrationId": existing.get("calibrationId"),
            "calibrationSet": existing.get("calibrationSet"),
            "calibrationComplete": bool(existing.get("calibrationComplete")) if existing.get("calibrationComplete") is not None else None,
            "operatorConfirmation": existing.get("operatorConfirmation"),
            "steps": [step.to_dict() for step in self._run.steps],
            "error": self._run.error,
        }

    def write_metadata_skeleton(self) -> Path | None:
        if not self._run.output_dir:
            return None
        output_dir = Path(self._run.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "capture_metadata_skeleton.json"
        path.write_text(json.dumps(self._run.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def safe_stop(self) -> None:
        errors: list[str] = []
        try:
            if self.safe_stop_callback is not None:
                self.safe_stop_callback()
        except Exception as exc:
            errors.append(str(exc))
        stage = self.sample_stage_controller or getattr(self.device_manager, "sample_stage_controller", None) or getattr(self.device_manager, "sample_stage", None)
        if stage is not None and hasattr(stage, "safe_stop"):
            try:
                stage.safe_stop()
            except Exception as exc:
                errors.append(str(exc))
        if self.hardware_controller is not None and hasattr(self.hardware_controller, "safe_stop"):
            try:
                self.hardware_controller.safe_stop()
            except Exception as exc:
                errors.append(str(exc))
        else:
            controller = getattr(self.device_manager, "controller", None)
            if controller is not None and hasattr(controller, "safe_stop"):
                try:
                    controller.safe_stop()
                except Exception as exc:
                    errors.append(str(exc))
        if errors:
            message = "; ".join(error for error in errors if error)
            if self._run.error is None:
                self._run.error = CaptureSafetyError("安全停止失败", cause=RuntimeError(message)).to_dict()
            else:
                self._run.error["safeStopError"] = message

    def _run_steps(
        self,
        *,
        sample_id: str = "",
        output_dir: str | Path | None = None,
        steps: list[CaptureStepPlan],
        mode: str = "",
    ) -> dict[str, Any]:
        plans = steps
        self._run = CaptureRun(
            capture_id=self.capture_id_factory(),
            sample_id=str(sample_id or ""),
            state=CaptureState.PREPARING,
            started_at=self.clock(),
            output_dir=str(output_dir or ""),
            mode=str(mode or ""),
        )
        self._run.steps = [
            CaptureStep(id=plan.id, name=plan.name, timeout_ms=plan.timeout_ms)
            for plan in plans
        ]
        if self._active_multiview_plan is not None:
            self._initialize_multiview_metadata(self._active_multiview_plan)

        try:
            for index, plan in enumerate(plans):
                self._check_cancel(plan.id)
                self._run.state = plan.state
                self._run.current_step = plan.id
                self._execute_step(self._run.steps[index], plan)
                self._run.progress = int(((index + 1) / max(len(plans), 1)) * 100)
            self._run.state = CaptureState.COMPLETED
            self._run.current_step = None
            self._run.finished_at = self.clock()
            self._run.metadata = self.build_metadata()
            self.write_metadata_skeleton()
        except CaptureCancelled as exc:
            self._run.state = CaptureState.CANCELLING
            self._run.error = exc.to_dict()
            self._record_sequence_cancelled()
            self._record_multiview_cancelled()
            self.safe_stop()
            self._cancel_pending_steps(exc.step)
            self._run.state = CaptureState.CANCELLED
            self._run.current_step = None
            self._run.finished_at = self.clock()
            self._run.metadata = self.build_metadata()
        except CaptureCoordinatorError as exc:
            self._fail_current_step(exc)
            self._run.error = exc.to_dict()
            self._record_sequence_failure_from_error(exc)
            self._record_multiview_failure_from_error(exc)
            self.safe_stop()
            self._run.state = CaptureState.FAILED
            self._run.current_step = None
            self._run.finished_at = self.clock()
            self._run.metadata = self.build_metadata()
        except Exception as exc:
            wrapped = CaptureCoordinatorError(
                str(exc) or exc.__class__.__name__,
                step=self._run.current_step or "",
                code="step_error",
                cause=exc,
            )
            self._fail_current_step(wrapped)
            self._run.error = wrapped.to_dict()
            self._record_sequence_failure_from_error(wrapped)
            self._record_multiview_failure_from_error(wrapped)
            self.safe_stop()
            self._run.state = CaptureState.FAILED
            self._run.current_step = None
            self._run.finished_at = self.clock()
            self._run.metadata = self.build_metadata()
        return self.snapshot()

    def _execute_step(self, step: CaptureStep, plan: CaptureStepPlan) -> None:
        step.status = CaptureStepStatus.RUNNING
        step.started_at = self.clock()
        if plan.action is not None:
            result = plan.action()
            if result is not None:
                step.result = result
        self._check_cancel(plan.id)
        step.finished_at = self.clock()
        step.duration_ms = int(round((step.finished_at - step.started_at) * 1000))
        timeout_ms = plan.timeout_ms
        if timeout_ms is not None and step.duration_ms > timeout_ms:
            raise CaptureStepTimeout(step=plan.id, timeout_ms=timeout_ms, duration_ms=step.duration_ms)
        step.status = CaptureStepStatus.COMPLETED

    def _controller(self, step: str) -> Any:
        controller = self.hardware_controller or getattr(self.device_manager, "controller", None)
        if controller is None:
            raise CaptureSafetyError("硬件控制器不可用", step=step)
        return controller

    def _hardware_precheck(self) -> dict[str, Any]:
        step = "hardware_precheck"
        controller = self._controller(step)
        if hasattr(controller, "ping"):
            try:
                controller.ping()
            except Exception as exc:
                raise CaptureSafetyError("STM32 PING 失败", step=step, cause=exc) from exc
        fault_code = None
        if hasattr(controller, "get_error_status"):
            try:
                fault_code = controller.get_error_status()
            except Exception as exc:
                raise CaptureSafetyError("无法读取 STM32 故障状态", step=step, cause=exc) from exc
            if fault_code not in (None, 0x00):
                raise CaptureSafetyError(f"STM32 当前故障码为 0x{fault_code:02X}", step=step)
        return {"controller": "available", "ping": True, "faultCode": fault_code}

    def _door_close(self) -> dict[str, Any]:
        step = "door_close"
        controller = self._controller(step)
        try:
            controller.door_close()
        except Exception as exc:
            raise CaptureSafetyError("升降门关闭命令失败", step=step, cause=exc) from exc
        result: dict[str, Any] = {
            "requestedState": "closed",
            "command": "door_close",
            "commandSent": True,
        }
        if hasattr(controller, "get_door_status"):
            try:
                door_state = controller.get_door_status()
            except Exception as exc:
                raise CaptureSafetyError("无法读取升降门状态", step=step, cause=exc) from exc
            actual_state = self._state_name(door_state)
            result["actualState"] = actual_state
            result["physicalDoorConfirmed"] = actual_state == "closed"
            if actual_state != "closed":
                raise CaptureSafetyError("升降门未关闭到位", step=step)
        else:
            result["physicalDoorConfirmed"] = False
            result["note"] = "door close command sent; physical feedback unavailable"
        return result

    def _fan_on(self) -> dict[str, Any]:
        step = "fan_on"
        controller = self._controller(step)
        try:
            controller.fan_on()
        except Exception as exc:
            raise CaptureSafetyError("风扇开启命令失败", step=step, cause=exc) from exc
        result: dict[str, Any] = {"requestedState": True, "command": "fan_on", "commandSent": True}
        if hasattr(controller, "get_output_status"):
            try:
                outputs = controller.get_output_status()
            except Exception as exc:
                raise CaptureSafetyError("无法读取风扇输出状态", step=step, cause=exc) from exc
            fan_on = bool(getattr(outputs, "fan_on", False))
            result["actualState"] = fan_on
            if not fan_on:
                raise CaptureSafetyError("风扇未开启", step=step)
        return result

    def _prepare_rgb_lighting(self, mask: int) -> dict[str, Any]:
        step = "rgb_light_prepare"
        controller = self._controller(step)
        try:
            controller.tungsten_set(0x00)
            controller.rgb_led_set(mask)
            if hasattr(controller, "ensure_rgb_capture_ready"):
                controller.ensure_rgb_capture_ready()
        except Exception as exc:
            raise CaptureSafetyError("RGB 光源准备失败", step=step, cause=exc) from exc
        return {
            "mode": "rgb",
            "rgbLedMask": mask,
            "tungstenMask": 0x00,
            "interlockChecked": hasattr(controller, "ensure_rgb_capture_ready"),
        }

    def _prepare_multispectral_lighting(self, mask: int) -> dict[str, Any]:
        step = "multispectral_light_prepare"
        controller = self._controller(step)
        try:
            controller.rgb_led_set(0x00)
            controller.tungsten_set(mask)
            if hasattr(controller, "ensure_multispectral_capture_ready"):
                controller.ensure_multispectral_capture_ready()
        except Exception as exc:
            raise CaptureSafetyError("多光谱光源准备失败", step=step, cause=exc) from exc
        return {
            "mode": "multispectral",
            "rgbLedMask": 0x00,
            "tungstenMask": mask,
            "interlockChecked": hasattr(controller, "ensure_multispectral_capture_ready"),
        }

    def _capture_safety_check(self, mode: str) -> dict[str, Any]:
        step = "capture_safety_check"
        controller = self._controller(step)
        try:
            if mode == "rgb" and hasattr(controller, "ensure_rgb_capture_ready"):
                controller.ensure_rgb_capture_ready()
            elif mode == "multispectral" and hasattr(controller, "ensure_multispectral_capture_ready"):
                controller.ensure_multispectral_capture_ready()
        except Exception as exc:
            raise CaptureSafetyError("采集安全 interlock 未通过", step=step, cause=exc) from exc
        return {"mode": mode, "interlockChecked": True}

    def _shutdown_lighting(self) -> dict[str, Any]:
        step = "lighting_shutdown"
        controller = self._controller(step)
        try:
            controller.tungsten_set(0x00)
            controller.rgb_led_set(0x00)
        except Exception as exc:
            raise CaptureSafetyError("关闭采集光源失败", step=step, cause=exc) from exc
        return {
            "rgbLedMask": 0x00,
            "tungstenMask": 0x00,
            "commandSent": True,
        }

    def _verify_lighting_off(self) -> dict[str, Any]:
        step = "lighting_off_verify"
        controller = self._controller(step)
        result: dict[str, Any] = {
            "rgbLedMask": 0x00,
            "tungstenMask": 0x00,
            "lightsOffConfirmed": False,
        }
        if not hasattr(controller, "get_output_status"):
            result["note"] = "output status unavailable; using lighting shutdown command boundary only"
            return result
        try:
            outputs = controller.get_output_status()
        except Exception as exc:
            raise CaptureSafetyError("无法读取光源输出状态", step=step, cause=exc) from exc
        rgb_on = bool(getattr(outputs, "rgb_led_1_on", False) or getattr(outputs, "rgb_led_2_on", False))
        tungsten_on = bool(getattr(outputs, "tungsten_1_on", False) or getattr(outputs, "tungsten_2_on", False))
        result.update({
            "rgbLed1On": bool(getattr(outputs, "rgb_led_1_on", False)),
            "rgbLed2On": bool(getattr(outputs, "rgb_led_2_on", False)),
            "tungsten1On": bool(getattr(outputs, "tungsten_1_on", False)),
            "tungsten2On": bool(getattr(outputs, "tungsten_2_on", False)),
            "lightsOffConfirmed": not rgb_on and not tungsten_on,
        })
        if rgb_on or tungsten_on:
            raise CaptureSafetyError("暗场采集前光源未关闭", step=step)
        return result

    def _confirm_operator_reference_setup(
        self,
        capture_type: CaptureReferenceType,
        *,
        operator_confirmed: bool,
        confirmation_callback: Callable[[str, dict[str, Any]], bool] | None,
    ) -> dict[str, Any]:
        prompt = (
            "请确认镜头/光路已遮光，准备开始暗场采集"
            if capture_type == CaptureReferenceType.DARK
            else "请确认标准白板已放置在样品位置，准备开始白板采集"
        )
        context = {
            "captureType": capture_type.value,
            "prompt": prompt,
            "blockingUiState": "waiting_for_dark_setup" if capture_type == CaptureReferenceType.DARK else "waiting_for_white_reference",
        }
        confirmed = bool(operator_confirmed)
        if not confirmed and confirmation_callback is not None:
            try:
                confirmed = bool(confirmation_callback(capture_type.value, dict(context)))
            except Exception as exc:
                raise CaptureCoordinatorError("人工确认回调失败", step=f"operator_confirmation:{capture_type.value}", code="operator_confirmation_failed", cause=exc) from exc
        self._run.metadata.setdefault("operatorConfirmation", {})[capture_type.value] = {
            **context,
            "required": True,
            "confirmed": confirmed,
            "status": "confirmed" if confirmed else "required",
        }
        if not confirmed:
            raise CaptureCoordinatorError(
                "需要操作员确认后才能开始参考帧采集",
                step=f"operator_confirmation:{capture_type.value}",
                code="operator_confirmation_required",
            )
        return {
            "captureType": capture_type.value,
            "confirmed": True,
            "blockingUiState": context["blockingUiState"],
        }

    def _capture_rgb_frame(
        self,
        *,
        rgb_dir_name: str,
        view_index: int,
        view_id: str,
        filename: str,
    ) -> dict[str, Any]:
        step = "rgb_capture"
        if self.camera_manager is None or not hasattr(self.camera_manager, "capture_rgb_frame"):
            raise CaptureCoordinatorError("RGB 相机管理器不可用", step=step, code="rgb_camera_unavailable")
        if not self._run.output_dir:
            raise CaptureCoordinatorError("RGB 保存目录不可用", step=step, code="rgb_output_dir_missing")
        self._check_cancel(step)
        try:
            frame, capture_meta = self.camera_manager.capture_rgb_frame()
        except CaptureCoordinatorError:
            raise
        except Exception as exc:
            raise CaptureCoordinatorError("RGB 正式取帧失败", step=step, code="rgb_capture_failed", cause=exc) from exc
        array_info = self._validate_rgb_frame(frame, step=step)
        self._check_cancel(step)

        output_dir = Path(self._run.output_dir)
        rgb_dir = output_dir / rgb_dir_name
        target = rgb_dir / filename
        try:
            saved = self._write_rgb_png(frame.data, target)
        except CaptureCoordinatorError:
            raise
        except Exception as exc:
            raise CaptureCoordinatorError("RGB PNG 保存失败", step=step, code="rgb_save_failed", cause=exc) from exc

        frame_metadata = self._build_rgb_frame_metadata(
            saved_path=saved,
            output_dir=output_dir,
            rgb_dir_name=rgb_dir_name,
            view_index=view_index,
            view_id=view_id,
            frame=frame,
            array_info=array_info,
            capture_meta=capture_meta,
        )
        self._run.metadata.setdefault("frames", []).append(frame_metadata)
        view = self._upsert_view_metadata(view_id=view_id, view_index=view_index)
        view.setdefault("rgb_files", []).append(frame_metadata["relativePath"])
        view["rgb"] = dict(frame_metadata)
        view["rgbComplete"] = True
        return {
            "saved": True,
            "path": frame_metadata["relativePath"],
            "width": frame_metadata["width"],
            "height": frame_metadata["height"],
            "dtype": frame_metadata["dtype"],
            "channels": frame_metadata["channels"],
            "previewWasRunning": bool(capture_meta.get("previewWasRunning")),
        }

    def _capture_multispectral_frame(
        self,
        *,
        multispectral_dir_name: str,
        frame_index: int,
        view_id: str,
        filename: str,
    ) -> dict[str, Any]:
        step = "multispectral_capture"
        if self.camera_manager is None or not hasattr(self.camera_manager, "capture_multispectral_frame"):
            raise CaptureCoordinatorError("多光谱相机管理器不可用", step=step, code="multispectral_camera_unavailable")
        if not self._run.output_dir:
            raise CaptureCoordinatorError("多光谱保存目录不可用", step=step, code="multispectral_output_dir_missing")
        self._check_cancel(step)
        try:
            frame, capture_meta = self.camera_manager.capture_multispectral_frame()
        except CaptureCoordinatorError:
            raise
        except Exception as exc:
            raise CaptureCoordinatorError("DVP2 多光谱正式取帧失败", step=step, code="multispectral_capture_failed", cause=exc) from exc
        array_info = self._validate_multispectral_frame(frame, step=step)
        self._check_cancel(step)

        output_dir = Path(self._run.output_dir)
        multispectral_dir = output_dir / multispectral_dir_name
        target = multispectral_dir / filename
        try:
            saved = self._write_multispectral_png(frame.data, target, expected=array_info)
        except CaptureCoordinatorError:
            raise
        except Exception as exc:
            raise CaptureCoordinatorError("多光谱 PNG 保存失败", step=step, code="multispectral_save_failed", cause=exc) from exc

        frame_metadata = self._build_multispectral_frame_metadata(
            saved_path=saved,
            output_dir=output_dir,
            multispectral_dir_name=multispectral_dir_name,
            frame_index=frame_index,
            view_id=view_id,
            frame=frame,
            array_info=array_info,
            capture_meta=capture_meta,
        )
        frame_metadata["focus"] = self._evaluate_focus_metadata(frame)
        self._run.metadata.setdefault("frames", []).append(frame_metadata)
        self._run.metadata["captureType"] = "sample"
        view = self._upsert_view_metadata(view_id=view_id, view_index=frame_index)
        view.setdefault("multispectral_files", []).append(frame_metadata["relativePath"])
        view["multispectral"] = {
            "bands": [dict(frame_metadata)],
            "filterWheelSynchronized": False,
        }
        view["filterWheelSynchronized"] = False
        view["multispectralComplete"] = True
        return {
            "saved": True,
            "path": frame_metadata["relativePath"],
            "width": frame_metadata["width"],
            "height": frame_metadata["height"],
            "dtype": frame_metadata["dtype"],
            "channels": frame_metadata["channels"],
            "pixelFormat": frame_metadata["pixelFormat"],
            "previewWasRunning": bool(capture_meta.get("previewWasRunning")),
            "filterWheelSynchronized": False,
        }

    def _capture_multispectral_band_frame(
        self,
        *,
        multispectral_dir_name: str,
        band: MultispectralBandPlan,
        band_index: int,
        plan: MultispectralCapturePlan,
        capture_type: CaptureReferenceType = CaptureReferenceType.SAMPLE,
        calibration_id: str | None = None,
        view_id: str = "view_000",
        view_index: int = 0,
    ) -> dict[str, Any]:
        step = f"multispectral_capture:{band.band_id}"
        if self.camera_manager is None or not hasattr(self.camera_manager, "capture_multispectral_frame"):
            self._mark_sequence_band_failed(band, "multispectral_camera_unavailable")
            raise CaptureCoordinatorError("多光谱相机管理器不可用", step=step, code="multispectral_camera_unavailable")
        if not self._run.output_dir:
            self._mark_sequence_band_failed(band, "multispectral_output_dir_missing")
            raise CaptureCoordinatorError("多光谱保存目录不可用", step=step, code="multispectral_output_dir_missing")
        self._check_cancel(step)
        try:
            frame, capture_meta = self.camera_manager.capture_multispectral_frame()
        except CaptureCoordinatorError as exc:
            self._mark_sequence_band_failed(band, exc.code)
            raise
        except Exception as exc:
            self._mark_sequence_band_failed(band, "multispectral_capture_failed")
            raise CaptureCoordinatorError("DVP2 多光谱波段取帧失败", step=step, code="multispectral_capture_failed", cause=exc) from exc
        array_info = self._validate_multispectral_frame(frame, step=step)
        self._check_cancel(step)

        output_dir = Path(self._run.output_dir)
        filename = self._band_filename(band, band_index)
        target = output_dir / multispectral_dir_name / filename
        try:
            saved = self._write_multispectral_png(frame.data, target, expected=array_info)
        except CaptureCoordinatorError as exc:
            self._mark_sequence_band_failed(band, exc.code)
            raise
        except Exception as exc:
            self._mark_sequence_band_failed(band, "multispectral_save_failed")
            raise CaptureCoordinatorError("多光谱波段 PNG 保存失败", step=step, code="multispectral_save_failed", cause=exc) from exc

        wheel = self._sequence_state().get("filterWheel") or {}
        frame_metadata = self._build_multispectral_frame_metadata(
            saved_path=saved,
            output_dir=output_dir,
            multispectral_dir_name=multispectral_dir_name,
            frame_index=view_index,
            view_id=view_id,
            frame=frame,
            array_info=array_info,
            capture_meta=capture_meta,
        )
        frame_metadata.update({
            "id": f"{view_id}_{band.band_id}",
            "bandId": band.band_id,
            "bandIndex": band_index,
            "wavelengthNm": band.wavelength_nm,
            "bandwidthNm": band.bandwidth_nm,
            "bandAssignment": band.band_id,
            "filterWheelSynchronized": True,
            "filterWheel": {
                "position": band.wheel_position,
                "targetPosition": band.wheel_position,
                "confirmedPosition": wheel.get("position"),
                "homed": bool(wheel.get("homed")),
                "synchronized": True,
                "settlingMs": plan.settling_ms,
            },
            "captureBoundary": "dvp2_multispectral_sequence_band",
            "captureType": capture_type.value,
            "calibrationType": capture_type.value,
            "calibrationId": calibration_id,
            "requestedExposureUs": band.exposure_us,
            "requestedGain": band.gain,
        })
        band_state = self._sequence_band_state(band)
        actual = dict(band_state.get("actualSettings") or frame_metadata.get("actualSettings") or {})
        if actual:
            frame_metadata["actualSettings"] = actual
        frame_metadata["actualExposureUs"] = _optional_number(actual.get("exposure")) if actual.get("exposure") is not None else frame_metadata.get("actualExposureUs")
        frame_metadata["actualGain"] = _optional_number(actual.get("gain")) if actual.get("gain") is not None else frame_metadata.get("actualGain")
        frame_metadata.setdefault("fileVerified", True)
        self._add_reference_quality_metadata(
            frame_metadata=frame_metadata,
            frame=frame,
            capture_meta=capture_meta,
            capture_type=capture_type,
        )
        if capture_type == CaptureReferenceType.SAMPLE:
            frame_metadata["focus"] = self._evaluate_focus_metadata(
                frame,
                band_id=band.band_id,
                wavelength_nm=band.wavelength_nm,
            )
        self._run.metadata.setdefault("frames", []).append(frame_metadata)
        if capture_type == CaptureReferenceType.SAMPLE:
            view = self._upsert_view_metadata(view_id=view_id, view_index=view_index)
            view.setdefault("multispectral_files", []).append(frame_metadata["relativePath"])
            view.setdefault("multispectral", {}).setdefault("bands", []).append(dict(frame_metadata))
            view["multispectralComplete"] = False
        self._mark_sequence_band_completed(band, frame_metadata)
        self._sync_sequence_completion()
        return {
            "saved": True,
            "bandId": band.band_id,
            "wavelengthNm": band.wavelength_nm,
            "filterWheelSynchronized": True,
            "path": frame_metadata["relativePath"],
            "width": frame_metadata["width"],
            "height": frame_metadata["height"],
            "dtype": frame_metadata["dtype"],
            "pixelFormat": frame_metadata["pixelFormat"],
            "captureType": capture_type.value,
        }

    def _filter_wheel_home(
        self,
        plan: MultispectralCapturePlan,
        *,
        capture_type: CaptureReferenceType = CaptureReferenceType.SAMPLE,
        calibration_id: str | None = None,
        view_id: str | None = None,
        view_index: int | None = None,
    ) -> dict[str, Any]:
        step = "filter_wheel_home"
        self._active_multispectral_view_id = view_id
        state = self._ensure_sequence_metadata(plan)
        state["captureType"] = capture_type.value
        if view_id:
            state["viewId"] = view_id
            state["viewIndex"] = 0 if view_index is None else int(view_index)
        self._run.metadata["captureType"] = capture_type.value
        if calibration_id:
            state["calibrationId"] = calibration_id
            self._run.metadata["calibrationId"] = calibration_id
        controller = self._controller(step)
        try:
            controller.wheel_home()
            position = self._read_wheel_position(step)
        except CaptureCoordinatorError:
            raise
        except Exception as exc:
            raise CaptureSafetyError("滤光轮 HOME 失败", step=step, cause=exc) from exc
        if position is None:
            raise CaptureCoordinatorError("滤光轮 HOME 后位置未知", step=step, code="filter_wheel_position_unknown")
        state["filterWheel"].update({"homed": True, "position": position})
        return {"homed": True, "position": position}

    def _filter_wheel_move_to_band(self, band: MultispectralBandPlan) -> dict[str, Any]:
        step = f"filter_wheel_move:{band.band_id}"
        state = self._sequence_state()
        current = state.get("filterWheel", {}).get("position")
        if current is None:
            self._mark_sequence_band_failed(band, "filter_wheel_position_unknown")
            raise CaptureCoordinatorError("滤光轮当前位置未知，拒绝开始多波段采集", step=step, code="filter_wheel_position_unknown")
        delta = self._wheel_position_delta(current, band.wheel_position)
        try:
            if delta:
                self._controller(step).wheel_move_relative(delta)
        except Exception as exc:
            self._mark_sequence_band_failed(band, "filter_wheel_move_failed")
            raise CaptureSafetyError("滤光轮移动失败", step=step, cause=exc) from exc
        state["filterWheel"]["position"] = band.wheel_position if delta == 0 else None
        self._sequence_band_state(band).update({"status": "moving", "targetPosition": band.wheel_position, "moveDelta": delta})
        return {"bandId": band.band_id, "fromPosition": current, "targetPosition": band.wheel_position, "delta": delta}

    def _verify_filter_wheel_position(self, band: MultispectralBandPlan) -> dict[str, Any]:
        step = f"filter_wheel_position_verify:{band.band_id}"
        try:
            position = self._read_wheel_position(step)
        except CaptureCoordinatorError:
            self._mark_sequence_band_failed(band, "filter_wheel_position_unknown")
            raise
        except Exception as exc:
            self._mark_sequence_band_failed(band, "filter_wheel_position_verify_failed")
            raise CaptureSafetyError("滤光轮位置确认失败", step=step, cause=exc) from exc
        if position != band.wheel_position:
            self._mark_sequence_band_failed(band, "filter_wheel_position_mismatch")
            raise CaptureCoordinatorError(
                f"滤光轮位置不匹配：期望 {band.wheel_position}，实际 {position}",
                step=step,
                code="filter_wheel_position_mismatch",
            )
        state = self._sequence_state()
        state["filterWheel"].update({"homed": True, "position": position})
        self._sequence_band_state(band).update({"status": "position_verified", "confirmedPosition": position})
        return {"bandId": band.band_id, "targetPosition": band.wheel_position, "confirmedPosition": position}

    def _settle_filter_wheel(self, band: MultispectralBandPlan, plan: MultispectralCapturePlan) -> dict[str, Any]:
        step = f"filter_wheel_settle:{band.band_id}"
        delay_s = max(int(plan.settling_ms), 0) / 1000.0
        self._check_cancel(step)
        self.sleep_fn(delay_s)
        self._sequence_band_state(band).update({"settled": True, "settlingMs": int(plan.settling_ms)})
        return {"bandId": band.band_id, "settlingMs": int(plan.settling_ms)}

    def _apply_band_camera_settings(self, band: MultispectralBandPlan) -> dict[str, Any]:
        step = f"band_camera_settings:{band.band_id}"
        payload: dict[str, Any] = {}
        if band.exposure_us is not None:
            payload["exposure"] = band.exposure_us
        if band.gain is not None:
            payload["gain"] = band.gain
        if not payload:
            result = {"settingResults": {}, "status": {}, "summary": {}, "usedCurrentSettings": True}
        else:
            if self.camera_manager is None or not hasattr(self.camera_manager, "apply_multispectral_settings"):
                self._mark_sequence_band_failed(band, "multispectral_camera_unavailable")
                raise CaptureCoordinatorError("多光谱相机管理器不可用", step=step, code="multispectral_camera_unavailable")
            try:
                result = self.camera_manager.apply_multispectral_settings(payload)
            except Exception as exc:
                self._mark_sequence_band_failed(band, "band_camera_settings_failed")
                raise CaptureCoordinatorError("多光谱波段相机参数设置失败", step=step, code="band_camera_settings_failed", cause=exc) from exc
        self._sequence_band_state(band).update({
            "requestedSettings": dict(payload),
            "actualSettings": dict((result.get("status") or {}).get("actual") or {}),
            "settingResults": dict(result.get("settingResults") or {}),
        })
        return {"bandId": band.band_id, "requestedSettings": payload, "settingResults": result.get("settingResults") or {}}

    def _build_sample_multiview_plan(
        self,
        *,
        rotation_plan: dict[str, Any] | None,
        sample_rotation: dict[str, Any] | None,
        sample_stage_mode: str,
        sample_stage_settling_ms: int,
        return_home: bool,
        calibration_id: str | None,
    ) -> SampleMultiViewPlan:
        raw_plan = rotation_plan if isinstance(rotation_plan, dict) else None
        if raw_plan is None:
            raw_plan = build_capture_rotation_plan(sample_rotation or {})
        views: list[SampleViewPlan] = []
        for index, item in enumerate(raw_plan.get("views") or [], start=1):
            if not isinstance(item, dict):
                continue
            view_id = str(item.get("viewId") or item.get("view_id") or f"view_{index - 1:03d}")
            logical = item.get("logicalAngleDeg", item.get("logical_angle_deg", 0.0))
            mechanical = item.get("mechanicalAngleDeg", item.get("mechanical_angle_deg", logical))
            views.append(SampleViewPlan(
                view_id=view_id,
                capture_order=int(item.get("captureOrder", item.get("capture_order", index))),
                logical_angle_deg=float(logical),
                mechanical_angle_deg=float(mechanical),
                direction=str(item.get("direction") or raw_plan.get("direction") or "CW").upper(),
                closure_view=bool(item.get("closureView", item.get("closure_view", False))),
            ))
        if not views:
            raw_plan = build_capture_rotation_plan({"enabled": False})
            views = [
                SampleViewPlan(
                    view_id="view_000",
                    capture_order=1,
                    logical_angle_deg=0.0,
                    mechanical_angle_deg=0.0,
                    direction=str(raw_plan.get("direction") or "CW"),
                    closure_view=False,
                )
            ]
        return SampleMultiViewPlan(
            views=views,
            rotation_plan=dict(raw_plan),
            sample_stage_mode=str(sample_stage_mode or "hardware").strip().lower(),
            sample_stage_settling_ms=max(int(sample_stage_settling_ms), 0),
            return_home=bool(return_home),
            calibration_id=calibration_id,
        )

    def _initialize_multiview_metadata(self, plan: SampleMultiViewPlan) -> None:
        views = []
        for view in plan.views:
            item = view.to_dict()
            item.update({
                "sampleId": self._run.sample_id,
                "sample_id": self._run.sample_id,
                "rgb": None,
                "multispectral": {"bands": []},
                "rgbComplete": False,
                "multispectralComplete": False,
                "viewComplete": False,
                "missingBands": [],
                "sampleRotation": {
                    "viewId": view.view_id,
                    "logicalAngleDeg": view.logical_angle_deg,
                    "mechanicalAngleDeg": view.mechanical_angle_deg,
                    "direction": view.direction,
                    "verified": False,
                    "settlingMs": plan.sample_stage_settling_ms,
                    "positionVerification": "pending",
                    "controlDomain": "sample_rotation",
                    "stageMode": plan.sample_stage_mode,
                },
                "filterWheel": {
                    "controlDomain": "filter_wheel_rotation",
                    "independentFromSampleRotation": True,
                },
            })
            views.append(item)
        self._run.metadata.update({
            "captureType": "sample",
            "sampleId": self._run.sample_id,
            "sample_id": self._run.sample_id,
            "calibrationId": plan.calibration_id,
            "rotationPlan": dict(plan.rotation_plan),
            "sampleRotation": dict(plan.rotation_plan),
            "filterWheel": {
                "controlDomain": "filter_wheel_rotation",
                "independentFromSampleRotation": True,
            },
            "multiViewPlan": plan.to_dict(),
            "views": views,
            "completedViews": [],
            "failedView": None,
            "pendingViews": [view.view_id for view in plan.views],
            "multiViewCaptureComplete": False,
            "partialCapture": False,
            "cancelled": False,
            "returnedHome": False,
            "homeStatus": "not_requested" if not plan.return_home else "pending",
            "sampleIdGroupingRequired": True,
            "sampleIdGroupingRule": "all views of one fruit keep the same sample_id for train/validation grouping",
        })

    def _multiview_requires_sample_stage(self, plan: SampleMultiViewPlan) -> bool:
        enabled = bool(plan.rotation_plan.get("enabled"))
        return enabled and len(plan.views) > 1

    def _sample_stage(self, plan: SampleMultiViewPlan, step: str) -> Any:
        stage = self.sample_stage_controller or getattr(self.device_manager, "sample_stage_controller", None) or getattr(self.device_manager, "sample_stage", None)
        if stage is None:
            if plan.sample_stage_mode == "simulation":
                stage = SimulatedSampleStage()
                self.sample_stage_controller = stage
            else:
                stage = UnimplementedSampleStage()
        if plan.sample_stage_mode == "hardware" and not bool(getattr(stage, "implemented", False)):
            raise CaptureCoordinatorError("样品台真实硬件适配器尚未实现", step=step, code="hardware_not_implemented")
        return stage

    def _check_multiview_calibration(self, *, calibration_id: str | None, require_calibration: bool) -> dict[str, Any]:
        result: dict[str, Any] = {
            "calibrationId": calibration_id,
            "required": bool(require_calibration),
            "status": "not_provided" if not calibration_id else "pending",
        }
        if not calibration_id:
            if require_calibration:
                raise CaptureCoordinatorError("MultiView 采集需要 calibrationId", step="calibration_check", code="calibration_missing")
            result["status"] = "not_required"
            return result
        if not self._run.output_dir:
            raise CaptureCoordinatorError("CalibrationSet 检查缺少输出目录", step="calibration_check", code="calibration_output_dir_missing")
        calibration = self._read_calibration_set_file(Path(self._run.output_dir), calibration_id)
        if not calibration:
            if require_calibration:
                raise CaptureCoordinatorError("找不到指定 CalibrationSet", step="calibration_check", code="calibration_missing")
            result["status"] = "not_found"
            return result
        result["status"] = "complete" if calibration.get("calibrationComplete") else "incomplete"
        result["calibrationComplete"] = bool(calibration.get("calibrationComplete"))
        result["calibrationSet"] = calibration
        if require_calibration and not calibration.get("calibrationComplete"):
            raise CaptureCoordinatorError("CalibrationSet 尚未完整", step="calibration_check", code="calibration_incomplete")
        self._run.metadata["calibrationSet"] = calibration
        return result

    def _sample_stage_home(self, plan: SampleMultiViewPlan) -> dict[str, Any]:
        step = "sample_stage_home"
        stage = self._sample_stage(plan, step)
        try:
            result = stage.home_sample_stage()
        except SampleStageNotImplemented as exc:
            raise CaptureCoordinatorError("样品台真实硬件适配器尚未实现", step=step, code="hardware_not_implemented", cause=exc) from exc
        except Exception as exc:
            raise CaptureCoordinatorError("样品台 HOME 失败", step=step, code="sample_stage_home_failed", cause=exc) from exc
        metadata = self._run.metadata.setdefault("sampleStage", {})
        metadata.update({"homed": True, "mode": plan.sample_stage_mode, "homeResult": dict(result or {})})
        return dict(result or {})

    def _begin_sample_view(self, view: SampleViewPlan, view_index: int) -> dict[str, Any]:
        self._active_view = view
        self._active_multispectral_view_id = None
        if self._run.metadata.get("multispectralSequence"):
            self._archive_current_multispectral_sequence()
            self._run.metadata.pop("multispectralSequence", None)
            self._run.metadata["multispectralSequenceComplete"] = False
        item = self._upsert_view_metadata(view_id=view.view_id, view_index=view_index)
        item["status"] = "running"
        item["captureOrder"] = view.capture_order
        item["capture_order"] = view.capture_order
        item["logicalAngleDeg"] = view.logical_angle_deg
        item["logical_angle_deg"] = view.logical_angle_deg
        item["mechanicalAngleDeg"] = view.mechanical_angle_deg
        item["mechanical_angle_deg"] = view.mechanical_angle_deg
        item["direction"] = view.direction
        item["closureView"] = view.closure_view
        item["closure_view"] = view.closure_view
        return {"viewId": view.view_id, "captureOrder": view.capture_order}

    def _move_sample_stage_to_view(self, view: SampleViewPlan, plan: SampleMultiViewPlan) -> dict[str, Any]:
        step = f"sample_stage_move:{view.view_id}"
        item = self._upsert_view_metadata(view_id=view.view_id, view_index=view.capture_order - 1)
        target = float(view.mechanical_angle_deg)
        if not self._multiview_requires_sample_stage(plan):
            result = {
                "targetAngleDeg": target,
                "direction": view.direction,
                "status": "single_view_no_motion_required",
                "commandSent": False,
            }
            item["sampleRotation"].update(result)
            return result
        stage = self._sample_stage(plan, step)
        try:
            result = stage.move_sample_stage_to_angle(target, direction=view.direction)
        except SampleStageNotImplemented as exc:
            raise CaptureCoordinatorError("样品台真实硬件适配器尚未实现", step=step, code="hardware_not_implemented", cause=exc) from exc
        except Exception as exc:
            raise CaptureCoordinatorError("样品台移动失败", step=step, code="sample_stage_move_failed", cause=exc) from exc
        item["sampleRotation"].update({
            "targetAngleDeg": target,
            "direction": view.direction,
            "moveResult": dict(result or {}),
            "commandSent": plan.sample_stage_mode == "hardware",
        })
        return dict(result or {})

    def _verify_sample_stage_position(self, view: SampleViewPlan, plan: SampleMultiViewPlan) -> dict[str, Any]:
        step = f"sample_stage_position_verify:{view.view_id}"
        item = self._upsert_view_metadata(view_id=view.view_id, view_index=view.capture_order - 1)
        if not self._multiview_requires_sample_stage(plan):
            result = {
                "verified": True,
                "status": "single_view_no_motion_required",
                "logicalAngleDeg": view.logical_angle_deg,
                "mechanicalAngleDeg": view.mechanical_angle_deg,
            }
            item["sampleRotation"].update({**result, "positionVerification": "verified"})
            return result
        stage = self._sample_stage(plan, step)
        if hasattr(stage, "wait_sample_stage_stable"):
            try:
                stable_result = stage.wait_sample_stage_stable()
            except Exception as exc:
                raise CaptureCoordinatorError("样品台稳定确认失败", step=step, code="sample_stage_stable_failed", cause=exc) from exc
        else:
            stable_result = {"stable": False, "status": "unavailable"}
        if not hasattr(stage, "get_sample_stage_position"):
            raise CaptureCoordinatorError("样品台位置回读不可用", step=step, code="sample_stage_position_unavailable")
        raw_position = stage.get_sample_stage_position()
        if isinstance(raw_position, SampleStagePosition):
            position = raw_position.to_dict()
        elif isinstance(raw_position, dict):
            position = dict(raw_position)
        else:
            position = {"angleDeg": raw_position, "verified": raw_position is not None, "status": "verified" if raw_position is not None else "unavailable"}
        verified = bool(position.get("verified"))
        angle = _optional_number(position.get("angleDeg", position.get("angle_deg")))
        if not verified:
            raise CaptureCoordinatorError("样品台位置未确认，拒绝采集该 View", step=step, code="sample_stage_position_unverified")
        item["sampleRotation"].update({
            "verified": True,
            "actualAngleDeg": angle,
            "positionVerification": "verified",
            "position": position,
            "stableResult": dict(stable_result or {}),
        })
        return {
            "viewId": view.view_id,
            "verified": True,
            "actualAngleDeg": angle,
            "targetAngleDeg": view.mechanical_angle_deg,
            "stableResult": dict(stable_result or {}),
        }

    def _settle_sample_stage(self, view: SampleViewPlan, plan: SampleMultiViewPlan) -> dict[str, Any]:
        step = f"sample_stage_settle:{view.view_id}"
        delay_s = max(int(plan.sample_stage_settling_ms), 0) / 1000.0
        self._check_cancel(step)
        self.sleep_fn(delay_s)
        item = self._upsert_view_metadata(view_id=view.view_id, view_index=view.capture_order - 1)
        item["sampleRotation"]["settlingMs"] = int(plan.sample_stage_settling_ms)
        item["sampleRotation"]["settled"] = True
        return {"viewId": view.view_id, "settlingMs": int(plan.sample_stage_settling_ms)}

    def _verify_sample_view_complete(self, view: SampleViewPlan, plan: MultispectralCapturePlan) -> dict[str, Any]:
        item = self._upsert_view_metadata(view_id=view.view_id, view_index=view.capture_order - 1)
        sequence = self._run.metadata.get("multispectralSequence") or {}
        enabled_band_ids = [band.band_id for band in plan.enabled_bands()]
        completed_bands = list(sequence.get("completedBands") or [])
        missing = [band_id for band_id in enabled_band_ids if band_id not in completed_bands]
        rgb_complete = bool(item.get("rgbComplete"))
        multispectral_complete = not missing and bool(enabled_band_ids) and not sequence.get("failedBand")
        rotation_verified = bool((item.get("sampleRotation") or {}).get("verified"))
        item["multispectralComplete"] = multispectral_complete
        item["missingBands"] = missing
        item["viewComplete"] = bool(rotation_verified and rgb_complete and multispectral_complete)
        item["status"] = "completed" if item["viewComplete"] else "failed"
        item["multispectral"] = {
            **dict(item.get("multispectral") or {}),
            "sequence": dict(sequence),
            "enabledBandIds": enabled_band_ids,
            "completedBands": completed_bands,
            "missingBands": missing,
        }
        item["filterWheel"] = {
            "controlDomain": "filter_wheel_rotation",
            "independentFromSampleRotation": True,
            "sequence": dict(sequence.get("filterWheel") or {}),
        }
        self._archive_current_multispectral_sequence()
        self._sync_multiview_completion()
        if not item["viewComplete"]:
            raise CaptureCoordinatorError("视角采集不完整", step=f"view_complete_verify:{view.view_id}", code="view_incomplete")
        return {
            "viewId": view.view_id,
            "viewComplete": True,
            "rgbComplete": rgb_complete,
            "multispectralComplete": multispectral_complete,
            "missingBands": missing,
        }

    def _return_sample_stage_home(self, plan: SampleMultiViewPlan) -> dict[str, Any]:
        step = "return_home"
        try:
            result = self._sample_stage_home(plan)
        except CaptureCoordinatorError as exc:
            self._run.metadata["returnedHome"] = False
            self._run.metadata["homeStatus"] = exc.code
            self._run.metadata["returnHomeError"] = exc.to_dict()
            return {"returnedHome": False, "homeStatus": exc.code, "error": exc.to_dict()}
        self._run.metadata["returnedHome"] = True
        self._run.metadata["homeStatus"] = "HOME_OK"
        return {"returnedHome": True, "homeStatus": "HOME_OK", "result": result}

    def _finalize_sample_multiview_capture(self) -> dict[str, Any]:
        self._archive_current_multispectral_sequence()
        self._sync_multiview_completion()
        if self._active_multiview_plan and not self._active_multiview_plan.return_home:
            self._run.metadata["returnedHome"] = False
            self._run.metadata["homeStatus"] = "not_requested"
        return {
            "multiViewCaptureComplete": bool(self._run.metadata.get("multiViewCaptureComplete")),
            "completedViews": list(self._run.metadata.get("completedViews") or []),
            "failedView": self._run.metadata.get("failedView"),
            "pendingViews": list(self._run.metadata.get("pendingViews") or []),
        }

    def _write_sample_multiview_files(self) -> None:
        if self._run.mode != "sample_multiview" or not self._run.output_dir:
            return
        self._write_current_sample_files()

    def _write_current_sample_files(self) -> None:
        if not self._run.output_dir:
            return
        output_dir = Path(self._run.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata = dict(self._run.metadata)
        metadata["state"] = self._run.state.value
        metadata["captureId"] = self._run.capture_id
        metadata["capture_id"] = self._run.capture_id
        metadata["sampleId"] = self._run.sample_id
        metadata["sample_id"] = self._run.sample_id
        metadata["updatedAt"] = self.clock()
        metadata["updated_at"] = metadata["updatedAt"]
        temp_metadata = output_dir / f".metadata.{self._run.capture_id}.tmp"
        temp_views = output_dir / f".views.{self._run.capture_id}.tmp"
        temp_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_views.write_text(json.dumps(metadata.get("views") or [], ensure_ascii=False, indent=2), encoding="utf-8")
        temp_metadata.replace(output_dir / "metadata.json")
        temp_views.replace(output_dir / "views.json")
        self._run.metadata = metadata

    def _upsert_view_metadata(self, *, view_id: str, view_index: int) -> dict[str, Any]:
        views = self._run.metadata.setdefault("views", [])
        for item in views:
            if item.get("viewId") == view_id or item.get("view_id") == view_id:
                return item
        item = {
            "viewId": view_id,
            "view_id": view_id,
            "viewIndex": int(view_index),
            "view_index": int(view_index),
            "sampleId": self._run.sample_id,
            "sample_id": self._run.sample_id,
            "rgb_files": [],
            "multispectral_files": [],
            "rgbComplete": False,
            "multispectralComplete": False,
            "viewComplete": False,
            "missingBands": [],
            "sampleRotation": {
                "viewId": view_id,
                "verified": False,
                "controlDomain": "sample_rotation",
            },
            "filterWheel": {
                "controlDomain": "filter_wheel_rotation",
                "independentFromSampleRotation": True,
            },
        }
        views.append(item)
        return item

    def _archive_current_multispectral_sequence(self) -> None:
        sequence = self._run.metadata.get("multispectralSequence")
        if not sequence:
            return
        archived = self._run.metadata.setdefault("multispectralSequences", [])
        view_id = sequence.get("viewId")
        if view_id and any(item.get("viewId") == view_id for item in archived):
            for index, item in enumerate(archived):
                if item.get("viewId") == view_id:
                    archived[index] = dict(sequence)
                    break
        elif sequence not in archived:
            archived.append(dict(sequence))

    def _sync_multiview_completion(self) -> None:
        views = self._run.metadata.get("views") or []
        completed = [str(view.get("viewId") or view.get("view_id")) for view in views if view.get("viewComplete")]
        failed = self._run.metadata.get("failedView")
        for view in views:
            if view.get("status") == "failed":
                failed = view.get("viewId") or view.get("view_id")
                break
        pending = [
            str(view.get("viewId") or view.get("view_id"))
            for view in views
            if not view.get("viewComplete") and (view.get("viewId") or view.get("view_id")) != failed
        ]
        self._run.metadata["completedViews"] = completed
        self._run.metadata["failedView"] = failed
        self._run.metadata["pendingViews"] = pending
        complete = bool(views) and len(completed) == len(views) and not failed
        self._run.metadata["multiViewCaptureComplete"] = complete
        self._run.metadata["partialCapture"] = bool(completed) and not complete

    def _record_multiview_cancelled(self) -> None:
        if "multiViewCaptureComplete" not in self._run.metadata:
            return
        view = self._active_view
        if view is not None:
            item = self._upsert_view_metadata(view_id=view.view_id, view_index=view.capture_order - 1)
            if not item.get("viewComplete"):
                item["status"] = "cancelled"
        self._run.metadata["cancelled"] = True
        self._archive_current_multispectral_sequence()
        self._sync_multiview_completion()

    def _record_multiview_failure_from_error(self, exc: CaptureCoordinatorError) -> None:
        if "multiViewCaptureComplete" not in self._run.metadata:
            return
        view_id = self._extract_view_id_from_step(exc.step)
        if not view_id and self._active_view is not None:
            view_id = self._active_view.view_id
        if view_id:
            item = self._upsert_view_metadata(view_id=view_id, view_index=0)
            item["status"] = "failed"
            item["viewComplete"] = False
            item["error"] = exc.to_dict()
            self._run.metadata["failedView"] = view_id
        self._archive_current_multispectral_sequence()
        self._sync_multiview_completion()

    @staticmethod
    def _extract_view_id_from_step(step: str | None) -> str:
        text = str(step or "")
        for part in text.split(":"):
            if part.startswith("view_"):
                return part
        return ""

    @staticmethod
    def _validate_rgb_frame(frame: Any, *, step: str) -> dict[str, Any]:
        import numpy as np

        data = getattr(frame, "data", None)
        if data is None:
            raise CaptureCoordinatorError("RGB 相机返回空帧", step=step, code="rgb_frame_empty")
        array = np.asarray(data)
        if array.size <= 0:
            raise CaptureCoordinatorError("RGB 相机返回空帧", step=step, code="rgb_frame_empty")
        if array.ndim != 3 or array.shape[2] != 3:
            raise CaptureCoordinatorError("RGB 帧形状无效", step=step, code="rgb_frame_invalid")
        if array.shape[0] <= 0 or array.shape[1] <= 0:
            raise CaptureCoordinatorError("RGB 帧尺寸无效", step=step, code="rgb_frame_invalid")
        if array.dtype != np.uint8:
            raise CaptureCoordinatorError("RGB 正式帧必须为 uint8", step=step, code="rgb_frame_invalid")
        color_space = str(getattr(frame, "color_space", "") or "").upper()
        if color_space != "RGB":
            raise CaptureCoordinatorError("RGB 帧色彩顺序无效", step=step, code="rgb_frame_invalid")
        return {
            "height": int(array.shape[0]),
            "width": int(array.shape[1]),
            "channels": int(array.shape[2]),
            "dtype": str(array.dtype),
        }

    def _write_rgb_png(self, data: Any, target: Path) -> Path:
        from PIL import Image

        step = "rgb_capture"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise CaptureCoordinatorError("RGB 目标文件已存在，拒绝覆盖", step=step, code="rgb_file_exists")
        reserved = False
        temp_path = target.with_name(f".{target.name}.{self._run.capture_id}.tmp")
        try:
            with target.open("xb"):
                reserved = True
            Image.fromarray(data, mode="RGB").save(temp_path, format="PNG")
            if not temp_path.exists() or temp_path.stat().st_size <= 0:
                raise CaptureCoordinatorError("RGB PNG 临时文件为空", step=step, code="rgb_save_failed")
            temp_path.replace(target)
            if not target.exists() or target.stat().st_size <= 0:
                raise CaptureCoordinatorError("RGB PNG 最终文件为空", step=step, code="rgb_save_failed")
            return target
        except FileExistsError as exc:
            raise CaptureCoordinatorError("RGB 目标文件已存在，拒绝覆盖", step=step, code="rgb_file_exists", cause=exc) from exc
        except CaptureCoordinatorError:
            raise
        except Exception as exc:
            raise CaptureCoordinatorError("RGB PNG 保存失败", step=step, code="rgb_save_failed", cause=exc) from exc
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            if reserved and target.exists() and target.stat().st_size == 0:
                try:
                    target.unlink()
                except OSError:
                    pass

    def _build_rgb_frame_metadata(
        self,
        *,
        saved_path: Path,
        output_dir: Path,
        rgb_dir_name: str,
        view_index: int,
        view_id: str,
        frame: Any,
        array_info: dict[str, Any],
        capture_meta: dict[str, Any],
    ) -> dict[str, Any]:
        frame_metadata = getattr(frame, "metadata", {}) or {}
        status = capture_meta.get("status") or {}
        requested = capture_meta.get("requestedSettings") or status.get("requested") or {}
        actual = capture_meta.get("actualSettings") or status.get("actual") or {}
        relative_path = self._relative_posix(saved_path, output_dir)
        return {
            "id": f"{view_id}_rgb",
            "role": "rgb",
            "view_id": view_id,
            "view_index": view_index,
            "path": str(saved_path),
            "relativePath": relative_path,
            "directory": rgb_dir_name,
            "filename": saved_path.name,
            "width": array_info["width"],
            "height": array_info["height"],
            "channels": array_info["channels"],
            "dtype": array_info["dtype"],
            "colorSpace": getattr(frame, "color_space", "RGB"),
            "pixelOrder": "RGB",
            "sourcePixelOrder": frame_metadata.get("sourceColorSpace") or "",
            "timestamp": self.clock(),
            "fileSizeBytes": saved_path.stat().st_size,
            "device": capture_meta.get("device") or {},
            "requestedSettings": requested,
            "actualSettings": actual,
            "previewWasRunning": bool(capture_meta.get("previewWasRunning")),
            "openedForCapture": bool(capture_meta.get("openedForCapture")),
        }

    @staticmethod
    def _validate_multispectral_frame(frame: Any, *, step: str) -> dict[str, Any]:
        import numpy as np

        data = getattr(frame, "data", None)
        if data is None:
            raise CaptureCoordinatorError("多光谱相机返回空帧", step=step, code="multispectral_frame_empty")
        array = np.asarray(data)
        if array.size <= 0:
            raise CaptureCoordinatorError("多光谱相机返回空帧", step=step, code="multispectral_frame_empty")
        if array.ndim != 2:
            raise CaptureCoordinatorError("多光谱帧形状无效", step=step, code="multispectral_frame_invalid")
        if array.shape[0] <= 0 or array.shape[1] <= 0:
            raise CaptureCoordinatorError("多光谱帧尺寸无效", step=step, code="multispectral_frame_invalid")
        if array.dtype not in (np.dtype("uint8"), np.dtype("uint16")):
            raise CaptureCoordinatorError("多光谱正式帧 dtype 暂不支持", step=step, code="multispectral_frame_invalid")
        color_space = str(getattr(frame, "color_space", "") or "").upper()
        if color_space != "MONO":
            raise CaptureCoordinatorError("多光谱正式帧必须是单通道 MONO", step=step, code="multispectral_frame_invalid")
        return {
            "height": int(array.shape[0]),
            "width": int(array.shape[1]),
            "channels": 1,
            "dtype": str(array.dtype),
            "min": float(np.min(array)),
            "max": float(np.max(array)),
            "mean": float(np.mean(array)),
            "std": float(np.std(array)),
        }

    def _write_multispectral_png(self, data: Any, target: Path, *, expected: dict[str, Any]) -> Path:
        from PIL import Image
        import numpy as np

        step = "multispectral_capture"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise CaptureCoordinatorError("多光谱目标文件已存在，拒绝覆盖", step=step, code="multispectral_file_exists")

        array = np.asarray(data)
        if array.dtype == np.dtype("uint16"):
            image = Image.fromarray(array, mode="I;16")
        elif array.dtype == np.dtype("uint8"):
            image = Image.fromarray(array, mode="L")
        else:
            raise CaptureCoordinatorError("多光谱正式帧 dtype 暂不支持", step=step, code="multispectral_frame_invalid")

        temp_path = target.with_name(f".{target.name}.{self._run.capture_id}.tmp")
        reserved = False
        try:
            with target.open("xb"):
                reserved = True
            image.save(temp_path, format="PNG")
            verified = self._verify_multispectral_png(temp_path, expected=expected)
            if not verified["ok"]:
                raise CaptureCoordinatorError(
                    verified["message"],
                    step=step,
                    code="multispectral_save_verify_failed",
                )
            temp_path.replace(target)
            verified = self._verify_multispectral_png(target, expected=expected)
            if not verified["ok"]:
                raise CaptureCoordinatorError(
                    verified["message"],
                    step=step,
                    code="multispectral_save_verify_failed",
                )
            return target
        except CaptureCoordinatorError:
            raise
        except Exception as exc:
            raise CaptureCoordinatorError("多光谱 PNG 保存失败", step=step, code="multispectral_save_failed", cause=exc) from exc
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            if reserved and target.exists() and target.stat().st_size == 0:
                try:
                    target.unlink()
                except OSError:
                    pass

    @staticmethod
    def _verify_multispectral_png(path: Path, *, expected: dict[str, Any]) -> dict[str, Any]:
        from PIL import Image
        import numpy as np

        if not path.exists() or path.stat().st_size <= 0:
            return {"ok": False, "message": "多光谱 PNG 文件为空或不存在"}
        try:
            with Image.open(path) as image:
                array = np.asarray(image)
                if image.width != expected["width"] or image.height != expected["height"]:
                    return {"ok": False, "message": "多光谱 PNG 尺寸验证失败"}
                if str(array.dtype) != expected["dtype"]:
                    return {"ok": False, "message": "多光谱 PNG 位深验证失败"}
                if array.ndim != 2:
                    return {"ok": False, "message": "多光谱 PNG 不是单通道灰度图"}
        except Exception as exc:
            return {"ok": False, "message": f"多光谱 PNG 读取验证失败: {exc}"}
        return {"ok": True, "message": ""}

    def _build_multispectral_frame_metadata(
        self,
        *,
        saved_path: Path,
        output_dir: Path,
        multispectral_dir_name: str,
        frame_index: int,
        view_id: str,
        frame: Any,
        array_info: dict[str, Any],
        capture_meta: dict[str, Any],
    ) -> dict[str, Any]:
        frame_metadata = getattr(frame, "metadata", {}) or {}
        status = capture_meta.get("status") or {}
        requested = capture_meta.get("requestedSettings") or status.get("requested") or {}
        actual = capture_meta.get("actualSettings") or status.get("actual") or {}
        relative_path = self._relative_posix(saved_path, output_dir)
        exposure = frame_metadata.get("exposure")
        if exposure is None:
            exposure = capture_meta.get("exposure") if capture_meta.get("exposure") is not None else actual.get("exposure")
        gain = frame_metadata.get("gain")
        if gain is None:
            gain = capture_meta.get("gain") if capture_meta.get("gain") is not None else actual.get("gain")
        return {
            "id": f"{view_id}_multispectral_single",
            "role": "multispectral",
            "view_id": view_id,
            "view_index": frame_index,
            "path": str(saved_path),
            "relativePath": relative_path,
            "directory": multispectral_dir_name,
            "filename": saved_path.name,
            "width": array_info["width"],
            "height": array_info["height"],
            "channels": array_info["channels"],
            "dtype": array_info["dtype"],
            "colorSpace": getattr(frame, "color_space", "MONO"),
            "pixelFormat": frame_metadata.get("pixelFormat") or capture_meta.get("pixelFormat") or actual.get("pixelFormat") or "",
            "exposureUs": exposure,
            "gain": gain,
            "timestamp": self.clock(),
            "fileSizeBytes": saved_path.stat().st_size,
            "frameStats": {
                "min": array_info["min"],
                "max": array_info["max"],
                "mean": array_info["mean"],
                "std": array_info["std"],
            },
            "device": capture_meta.get("device") or {},
            "requestedSettings": requested,
            "actualSettings": actual,
            "requestedExposureUs": _optional_number(requested.get("exposure")),
            "actualExposureUs": _optional_number(exposure),
            "requestedGain": _optional_number(requested.get("gain")),
            "actualGain": _optional_number(gain),
            "previewWasRunning": bool(capture_meta.get("previewWasRunning")),
            "openedForCapture": bool(capture_meta.get("openedForCapture")),
            "streaming": bool(capture_meta.get("streaming")),
            "wavelengthNm": None,
            "bandAssignment": "unassigned",
            "filterWheelSynchronized": False,
            "captureType": "sample",
            "calibrationType": "sample",
            "captureBoundary": "dvp2_production_single_frame",
            "fileVerified": True,
        }

    def _add_reference_quality_metadata(
        self,
        *,
        frame_metadata: dict[str, Any],
        frame: Any,
        capture_meta: dict[str, Any],
        capture_type: CaptureReferenceType,
    ) -> None:
        if capture_type == CaptureReferenceType.SAMPLE:
            return
        stats = frame_metadata.get("frameStats") or {}
        if capture_type == CaptureReferenceType.DARK:
            frame_metadata["darkQuality"] = {
                "status": "unvalidated",
                "mean": stats.get("mean"),
                "max": stats.get("max"),
                "std": stats.get("std"),
            }
            return
        if capture_type == CaptureReferenceType.WHITE:
            saturation = self._saturation_diagnostics(frame, capture_meta)
            mean = float(stats.get("mean") or 0.0)
            std = float(stats.get("std") or 0.0)
            frame_metadata["saturationDiagnostics"] = saturation
            frame_metadata["whiteUniformity"] = {
                "status": "unvalidated",
                "mean": stats.get("mean"),
                "std": stats.get("std"),
                "coefficientOfVariation": None if mean == 0 else std / mean,
            }

    @staticmethod
    def _saturation_diagnostics(frame: Any, capture_meta: dict[str, Any]) -> dict[str, Any]:
        import numpy as np

        array = np.asarray(getattr(frame, "data", None))
        dtype_info = np.iinfo(array.dtype)
        frame_metadata = getattr(frame, "metadata", {}) or {}
        actual = capture_meta.get("actualSettings") or {}
        raw_bits = (
            frame_metadata.get("bits")
            or frame_metadata.get("bitDepth")
            or actual.get("bits")
            or actual.get("bitDepth")
            or capture_meta.get("bits")
            or capture_meta.get("bitDepth")
        )
        bit_depth = None
        if raw_bits not in (None, ""):
            try:
                bit_depth = int(raw_bits)
            except (TypeError, ValueError):
                bit_depth = None
        dtype_bits = int(array.dtype.itemsize * 8)
        if bit_depth is not None and 0 < bit_depth <= dtype_bits:
            saturation_value = int((1 << bit_depth) - 1)
            max_source = "frameMetadataBits"
            bit_depth_status = "known"
        else:
            saturation_value = int(dtype_info.max)
            max_source = "dtypeMax"
            bit_depth_status = "unknown"
            bit_depth = None
        count = int(np.count_nonzero(array >= saturation_value))
        total = int(array.size)
        return {
            "status": "unvalidated",
            "bitDepth": bit_depth,
            "bitDepthStatus": bit_depth_status,
            "saturationValue": saturation_value,
            "saturationMaxSource": max_source,
            "saturatedPixelCount": count,
            "saturatedPixelRatio": 0.0 if total <= 0 else count / total,
        }

    def _finalize_calibration_capture(
        self,
        *,
        plan: MultispectralCapturePlan,
        capture_type: CaptureReferenceType,
        calibration_id: str,
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._run.output_dir:
            return previous
        output_dir = Path(self._run.output_dir)
        calibration_set = self._build_calibration_set(plan=plan, calibration_id=calibration_id, output_dir=output_dir)
        self._run.metadata["captureType"] = capture_type.value
        self._run.metadata["calibrationId"] = calibration_id
        self._run.metadata["calibrationSet"] = calibration_set.to_dict()
        self._run.metadata["calibrationComplete"] = calibration_set.calibration_complete
        self.write_metadata_skeleton()
        self._write_calibration_set_file(output_dir, calibration_set)
        return self.snapshot()

    def _build_calibration_set(
        self,
        *,
        plan: MultispectralCapturePlan,
        calibration_id: str,
        output_dir: Path,
    ) -> CalibrationSet:
        existing = self._read_calibration_set_file(output_dir, calibration_id)
        dark_frames = self._merge_reference_frames(
            (existing.get("dark") or {}).get("frames") or [],
            self._run.metadata.get("frames") or [],
            CaptureReferenceType.DARK,
        )
        white_frames = self._merge_reference_frames(
            (existing.get("white") or {}).get("frames") or [],
            self._run.metadata.get("frames") or [],
            CaptureReferenceType.WHITE,
        )
        enabled_bands = plan.enabled_bands()
        enabled_band_ids = [band.band_id for band in enabled_bands]
        completed_dark = self._completed_reference_bands(dark_frames)
        completed_white = self._completed_reference_bands(white_frames)
        missing_dark = [band_id for band_id in enabled_band_ids if band_id not in completed_dark]
        missing_white = [band_id for band_id in enabled_band_ids if band_id not in completed_white]
        complete = not missing_dark and not missing_white and bool(enabled_band_ids)
        current_state = self._run.state.value
        if complete:
            status = "completed"
        elif current_state in {"failed", "cancelled"}:
            status = current_state
        else:
            status = "incomplete"
        first_frame = next(iter(dark_frames or white_frames), {})
        camera_identity = self._camera_identity_from_frame(first_frame)
        created_at = existing.get("createdAt") or self._run.started_at or self.clock()
        return CalibrationSet(
            calibration_id=calibration_id,
            created_at=float(created_at),
            camera_stable_id=self._camera_stable_id(camera_identity),
            camera_identity=camera_identity,
            filter_config_source=plan.filter_config_source,
            filter_config_version=plan.filter_config_version,
            development_config=plan.development_config,
            bands=[band.to_dict() for band in plan.bands],
            dark_frames=dark_frames,
            white_frames=white_frames,
            completed_dark_bands=completed_dark,
            completed_white_bands=completed_white,
            missing_dark_bands=missing_dark,
            missing_white_bands=missing_white,
            status=status,
            calibration_complete=complete,
            partial_capture=bool(completed_dark or completed_white) and not complete,
            same_band_settings_matched=self._reference_settings_match(dark_frames, white_frames, enabled_band_ids),
        )

    @staticmethod
    def _merge_reference_frames(
        existing_frames: list[dict[str, Any]],
        current_frames: list[dict[str, Any]],
        capture_type: CaptureReferenceType,
    ) -> list[dict[str, Any]]:
        by_band: dict[str, dict[str, Any]] = {}
        for frame in list(existing_frames) + list(current_frames):
            if frame.get("captureType") != capture_type.value:
                continue
            band_id = str(frame.get("bandId") or "")
            if not band_id:
                continue
            by_band[band_id] = dict(frame)
        return list(by_band.values())

    @staticmethod
    def _completed_reference_bands(frames: list[dict[str, Any]]) -> list[str]:
        return [
            str(frame.get("bandId"))
            for frame in frames
            if frame.get("bandId") and frame.get("fileVerified", True)
        ]

    @staticmethod
    def _camera_identity_from_frame(frame: dict[str, Any]) -> dict[str, Any]:
        device = dict(frame.get("device") or {})
        return {
            "stableId": device.get("stableId") or device.get("stable_id") or "",
            "serial": device.get("serial") or device.get("cameraSerial") or "",
            "userId": device.get("userId") or "",
            "model": device.get("model") or "",
            "ip": device.get("ip") or device.get("cameraIp") or "",
            "mac": device.get("mac") or device.get("cameraMac") or "",
            "transport": device.get("transport") or "",
            "backend": device.get("backend") or "",
        }

    @staticmethod
    def _camera_stable_id(identity: dict[str, Any]) -> str:
        return str(identity.get("stableId") or identity.get("serial") or identity.get("userId") or "")

    @staticmethod
    def _reference_settings_match(
        dark_frames: list[dict[str, Any]],
        white_frames: list[dict[str, Any]],
        enabled_band_ids: list[str],
    ) -> bool:
        dark_by_band = {str(frame.get("bandId")): frame for frame in dark_frames}
        white_by_band = {str(frame.get("bandId")): frame for frame in white_frames}
        if not enabled_band_ids:
            return False
        for band_id in enabled_band_ids:
            dark = dark_by_band.get(band_id)
            white = white_by_band.get(band_id)
            if not dark or not white:
                return False
            if not _numbers_equal(dark.get("requestedExposureUs"), white.get("requestedExposureUs")):
                return False
            if not _numbers_equal(dark.get("actualExposureUs"), white.get("actualExposureUs")):
                return False
            if not _numbers_equal(dark.get("requestedGain"), white.get("requestedGain")):
                return False
            if not _numbers_equal(dark.get("actualGain"), white.get("actualGain")):
                return False
        return True

    def _read_calibration_set_file(self, output_dir: Path, calibration_id: str) -> dict[str, Any]:
        path = self._calibration_set_path(output_dir, calibration_id)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_calibration_set_file(self, output_dir: Path, calibration_set: CalibrationSet) -> Path:
        path = self._calibration_set_path(output_dir, calibration_set.calibration_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{self._run.capture_id}.tmp")
        temp.write_text(json.dumps(calibration_set.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return path

    def _calibration_set_path(self, output_dir: Path, calibration_id: str) -> Path:
        safe_id = self._safe_path_token(calibration_id)
        return output_dir / "calibration" / f"calibration_set_{safe_id}.json"

    def _normalize_calibration_id(self, calibration_id: str | None) -> str:
        value = str(calibration_id or "").strip()
        if value:
            return value
        return "calibration_" + time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]

    def _evaluate_focus_metadata(
        self,
        frame: Any,
        *,
        band_id: str | None = None,
        wavelength_nm: int | None = None,
    ) -> dict[str, Any]:
        evaluator = getattr(self.camera_manager, "focus_evaluator", None)
        if evaluator is None:
            try:
                from camera_service.focus_quality import FocusEvaluator

                evaluator = FocusEvaluator()
            except Exception as exc:
                return {
                    "status": "evaluation_failed",
                    "classification": "unknown",
                    "score": None,
                    "error": str(exc),
                }
        try:
            result = evaluator.evaluate(
                frame,
                roi="center",
                band_id=band_id,
                wavelength_nm=wavelength_nm,
            ).to_dict()
        except Exception as exc:
            return {
                "status": "evaluation_failed",
                "classification": "unknown",
                "score": None,
                "error": str(exc),
            }
        metrics = result.get("metrics") or {}
        return {
            "status": result.get("status", "ok"),
            "classification": result.get("classification", "unknown"),
            "score": result.get("focusScore"),
            "tenengrad": metrics.get("tenengrad"),
            "laplacianVariance": metrics.get("laplacianVariance"),
            "edgeDensity": metrics.get("edgeDensity"),
            "roiMode": (result.get("roi") or {}).get("mode"),
            "roi": result.get("roi") or {},
            "thresholds": result.get("thresholds") or {},
            "bandId": result.get("bandId"),
            "wavelengthNm": result.get("wavelengthNm"),
        }

    def _build_multispectral_capture_plan(
        self,
        *,
        band_plan: MultispectralCapturePlan | list[MultispectralBandPlan] | list[dict[str, Any]] | None,
        filter_config_path: str | Path | None,
        settling_ms: int | None,
    ) -> MultispectralCapturePlan:
        if isinstance(band_plan, MultispectralCapturePlan):
            return MultispectralCapturePlan(
                bands=list(band_plan.bands),
                filter_config_source=band_plan.filter_config_source,
                filter_config_version=band_plan.filter_config_version,
                development_config=band_plan.development_config,
                settling_ms=int(settling_ms if settling_ms is not None else band_plan.settling_ms),
            )
        if band_plan is not None:
            bands = [self._coerce_band_plan(item) for item in band_plan]
            return MultispectralCapturePlan(
                bands=bands,
                filter_config_source="explicit",
                filter_config_version="",
                development_config=False,
                settling_ms=int(250 if settling_ms is None else settling_ms),
            )

        from quality_algorithm import filters as filter_module

        config_path = Path(filter_config_path).expanduser() if filter_config_path else filter_module.CONFIG_PATH
        raw_payload: dict[str, Any] = {}
        try:
            raw_payload = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            raw_payload = {}
        configured = filter_module.load_filter_config(config_path)
        bands = [self._band_plan_from_filter_band(band) for band in configured]
        profile = str(raw_payload.get("profile") or config_path.stem)
        development = bool("development" in profile.lower() or config_path.name.endswith(".development.json"))
        return MultispectralCapturePlan(
            bands=bands,
            filter_config_source=str(config_path),
            filter_config_version=profile,
            development_config=development,
            settling_ms=int(250 if settling_ms is None else settling_ms),
        )

    @staticmethod
    def _coerce_band_plan(item: MultispectralBandPlan | dict[str, Any]) -> MultispectralBandPlan:
        if isinstance(item, MultispectralBandPlan):
            return item
        data = dict(item)
        wheel_position = data.get("wheelPosition", data.get("filter_position", data.get("position")))
        if wheel_position is None:
            raise ValueError("band wheelPosition is required")
        wavelength = data.get("wavelengthNm", data.get("wavelength_nm"))
        band_id = data.get("bandId") or data.get("band_id")
        if not band_id:
            band_id = f"R{int(wavelength)}" if wavelength is not None else f"band_{int(wheel_position):02d}"
        exposure = data.get("exposureUs", data.get("exposure_us"))
        if exposure is None and data.get("exposure_ms") is not None:
            exposure = float(data.get("exposure_ms")) * 1000.0
        return MultispectralBandPlan(
            band_id=str(band_id),
            wheel_position=int(wheel_position),
            wavelength_nm=None if wavelength is None else int(wavelength),
            enabled=bool(data.get("enabled", True)),
            bandwidth_nm=_optional_number(data.get("bandwidthNm", data.get("bandwidth_nm"))),
            exposure_us=_optional_number(exposure),
            gain=_optional_number(data.get("gain")),
            source=data,
        )

    @staticmethod
    def _band_plan_from_filter_band(band: Any) -> MultispectralBandPlan:
        wavelength = getattr(band, "wavelength_nm", None)
        exposure_ms = getattr(band, "exposure_ms", None)
        return MultispectralBandPlan(
            band_id=f"R{int(wavelength)}" if wavelength is not None else f"band_{int(getattr(band, 'filter_position')):02d}",
            wheel_position=int(getattr(band, "filter_position")),
            wavelength_nm=None if wavelength is None else int(wavelength),
            enabled=bool(getattr(band, "enabled", True)),
            bandwidth_nm=_optional_number(getattr(band, "bandwidth_nm", None)),
            exposure_us=None if exposure_ms is None else float(exposure_ms) * 1000.0,
            gain=_optional_number(getattr(band, "gain", None)),
            source=band.to_dict() if hasattr(band, "to_dict") else {},
        )

    def _ensure_sequence_metadata(self, plan: MultispectralCapturePlan) -> dict[str, Any]:
        sequence = self._run.metadata.get("multispectralSequence")
        active_view_id = self._active_multispectral_view_id
        if sequence is not None and (not active_view_id or sequence.get("viewId") == active_view_id):
            return sequence
        if sequence is not None:
            self._archive_current_multispectral_sequence()
        enabled = plan.enabled_bands()
        disabled = [band for band in plan.bands if not band.enabled]
        sequence = {
            "status": "running",
            "captureType": "sample",
            "viewId": active_view_id,
            "filterConfigSource": plan.filter_config_source,
            "filterConfigVersion": plan.filter_config_version,
            "developmentConfig": bool(plan.development_config),
            "settlingMs": int(plan.settling_ms),
            "enabledBandIds": [band.band_id for band in enabled],
            "disabledBandIds": [band.band_id for band in disabled],
            "completedBands": [],
            "failedBand": None,
            "pendingBands": [band.band_id for band in enabled],
            "partialCapture": False,
            "cancelled": False,
            "failureReason": None,
            "filterWheel": {"homed": False, "position": None},
            "bands": [
                {
                    **band.to_dict(),
                    "status": "pending" if band.enabled else "skipped",
                    "captured": False,
                    "saved": False,
                    "verified": False,
                    "filterWheelSynchronized": False,
                }
                for band in plan.bands
            ],
        }
        self._run.metadata["multispectralSequence"] = sequence
        self._run.metadata["multispectralSequenceComplete"] = False
        self._run.metadata["bands"] = [band.to_dict() for band in plan.bands]
        return sequence

    def _sequence_state(self) -> dict[str, Any]:
        sequence = self._run.metadata.get("multispectralSequence")
        if sequence is None:
            raise CaptureCoordinatorError("多波段采集计划尚未初始化", step=self._run.current_step or "", code="multispectral_sequence_missing")
        return sequence

    def _sequence_band_state(self, band: MultispectralBandPlan) -> dict[str, Any]:
        sequence = self._sequence_state()
        for item in sequence.get("bands") or []:
            if item.get("bandId") == band.band_id:
                return item
        raise CaptureCoordinatorError("多波段状态缺少目标 band", step=self._run.current_step or "", code="multispectral_band_missing")

    def _mark_sequence_band_completed(self, band: MultispectralBandPlan, frame_metadata: dict[str, Any]) -> None:
        sequence = self._sequence_state()
        band_state = self._sequence_band_state(band)
        band_state.update({
            "status": "completed",
            "captured": True,
            "saved": True,
            "verified": True,
            "filterWheelSynchronized": True,
            "relativePath": frame_metadata["relativePath"],
            "path": frame_metadata["path"],
            "dtype": frame_metadata["dtype"],
            "width": frame_metadata["width"],
            "height": frame_metadata["height"],
        })
        if band.band_id not in sequence["completedBands"]:
            sequence["completedBands"].append(band.band_id)
        sequence["pendingBands"] = [band_id for band_id in sequence["pendingBands"] if band_id != band.band_id]
        sequence["partialCapture"] = bool(sequence["completedBands"])

    def _mark_sequence_band_failed(self, band: MultispectralBandPlan, reason: str) -> None:
        sequence = self._run.metadata.get("multispectralSequence")
        if not sequence:
            return
        band_state = self._sequence_band_state(band)
        band_state["status"] = "failed"
        sequence["failedBand"] = band.band_id
        sequence["failureReason"] = reason
        sequence["status"] = "failed"
        sequence["partialCapture"] = bool(sequence.get("completedBands"))
        sequence["pendingBands"] = [
            band_id
            for band_id in sequence.get("pendingBands") or []
            if band_id != band.band_id and band_id not in sequence.get("completedBands", [])
        ]

    def _sync_sequence_completion(self) -> None:
        sequence = self._run.metadata.get("multispectralSequence")
        if not sequence:
            return
        complete = bool(sequence.get("enabledBandIds")) and not sequence.get("pendingBands") and not sequence.get("failedBand")
        sequence["status"] = "completed" if complete else sequence.get("status", "running")
        sequence["partialCapture"] = bool(sequence.get("completedBands")) and not complete
        self._run.metadata["multispectralSequenceComplete"] = complete

    def _record_sequence_cancelled(self) -> None:
        sequence = self._run.metadata.get("multispectralSequence")
        if not sequence:
            return
        sequence["cancelled"] = True
        sequence["status"] = "cancelled"
        sequence["partialCapture"] = bool(sequence.get("completedBands"))
        sequence["failureReason"] = "capture_cancelled"
        self._run.metadata["multispectralSequenceComplete"] = False

    def _record_sequence_failure_from_error(self, exc: CaptureCoordinatorError) -> None:
        sequence = self._run.metadata.get("multispectralSequence")
        if not sequence:
            return
        sequence["status"] = "failed"
        sequence["failureReason"] = exc.code
        sequence["partialCapture"] = bool(sequence.get("completedBands"))
        self._run.metadata["multispectralSequenceComplete"] = False
        step = exc.step or ""
        if ":" in step and not sequence.get("failedBand"):
            sequence["failedBand"] = step.rsplit(":", 1)[1]

    def _read_wheel_position(self, step: str) -> int | None:
        value = self._controller(step).get_wheel_status()
        if value == 0x7F:
            return None
        return int(value)

    @staticmethod
    def _wheel_position_delta(current: int, target: int) -> int:
        delta = int(target) - int(current)
        if delta < -128 or delta > 127:
            raise ValueError("filter wheel relative move is outside supported range")
        return delta

    @staticmethod
    def _band_filename(band: MultispectralBandPlan, band_index: int) -> str:
        safe_id = CaptureCoordinator._safe_path_token(band.band_id)
        return f"band_{band_index + 1:02d}_{safe_id or f'band_{band_index + 1:02d}'}.png"

    @staticmethod
    def _safe_path_token(value: Any) -> str:
        return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value or "")).strip("_")

    @staticmethod
    def _relative_posix(path: Path, base: Path) -> str:
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _validate_direct_dir_name(value: str, *, field: str) -> str:
        name = str(value or "").strip()
        if not name or name in {".", ".."}:
            raise ValueError(f"{field} 不能为空")
        if Path(name).is_absolute() or any(separator and separator in name for separator in (os.sep, os.altsep)):
            raise ValueError(f"{field} 必须是一级目录名")
        return name

    @staticmethod
    def _validate_filename(value: str, *, role: str = "RGB") -> None:
        name = str(value or "").strip()
        if not name or name in {".", ".."}:
            raise ValueError(f"{role} 文件名不能为空")
        if Path(name).is_absolute() or any(separator and separator in name for separator in (os.sep, os.altsep)):
            raise ValueError(f"{role} 文件名不能包含路径")
        if Path(name).suffix.lower() != ".png":
            raise ValueError(f"{role} 文件名必须是 .png")

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized in {"rgb", "color"}:
            return "rgb"
        if normalized in {"multispectral", "multi_spectral", "ms"}:
            return "multispectral"
        raise ValueError("mode 必须是 rgb 或 multispectral")

    @staticmethod
    def _state_name(value: Any) -> str:
        if hasattr(value, "name"):
            return str(value.name).lower()
        return str(value).strip().lower()

    def _check_cancel(self, step_id: str = "") -> None:
        if self._run.cancel_requested:
            raise CaptureCancelled(step=step_id)

    def _fail_current_step(self, exc: CaptureCoordinatorError) -> None:
        for step in self._run.steps:
            if step.id == exc.step:
                step.status = CaptureStepStatus.FAILED
                if step.finished_at is None:
                    step.finished_at = self.clock()
                if step.started_at is not None:
                    step.duration_ms = int(round((step.finished_at - step.started_at) * 1000))
                step.error = exc.to_dict()
                return

    def _cancel_pending_steps(self, active_step: str = "") -> None:
        for step in self._run.steps:
            if step.status == CaptureStepStatus.RUNNING or (active_step and step.id == active_step):
                step.status = CaptureStepStatus.CANCELLED
                step.finished_at = step.finished_at or self.clock()
                if step.started_at is not None:
                    step.duration_ms = int(round((step.finished_at - step.started_at) * 1000))
            elif step.status == CaptureStepStatus.PENDING:
                step.status = CaptureStepStatus.SKIPPED


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numbers_equal(left: Any, right: Any, *, tolerance: float = 1e-6) -> bool:
    left_number = _optional_number(left)
    right_number = _optional_number(right)
    if left_number is None or right_number is None:
        return left_number is None and right_number is None
    return abs(left_number - right_number) <= tolerance


def validate_calibration_compatibility(
    calibration: dict[str, Any],
    sample_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Validate whether a calibration set can be paired with a sample sequence."""

    calibration_set = calibration.get("calibrationSet") if isinstance(calibration.get("calibrationSet"), dict) else calibration
    sample_sequence = sample_metadata.get("multispectralSequence") or {}
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    calibration_bands = _enabled_band_map(calibration_set.get("bands") or [])
    sample_bands = _enabled_band_map(sample_sequence.get("bands") or sample_metadata.get("bands") or [])
    calibration_band_ids = list(calibration_bands)
    sample_band_ids = list(sample_bands)
    if calibration_band_ids != sample_band_ids:
        issues.append({
            "field": "enabledBandIds",
            "message": "Calibration enabled bands do not match sample band plan",
            "calibration": calibration_band_ids,
            "sample": sample_band_ids,
        })

    for band_id in sorted(set(calibration_bands) & set(sample_bands)):
        cal_band = calibration_bands[band_id]
        sample_band = sample_bands[band_id]
        if cal_band.get("wavelengthNm") != sample_band.get("wavelengthNm"):
            issues.append({
                "field": f"bands.{band_id}.wavelengthNm",
                "message": "Calibration wavelength mapping does not match sample",
                "calibration": cal_band.get("wavelengthNm"),
                "sample": sample_band.get("wavelengthNm"),
            })

    dark_frames = _frames_by_band((calibration_set.get("dark") or {}).get("frames") or [], "dark")
    white_frames = _frames_by_band((calibration_set.get("white") or {}).get("frames") or [], "white")
    sample_frames = _frames_by_band(sample_metadata.get("frames") or [], "sample")
    completed_dark = set((calibration_set.get("dark") or {}).get("completedBands") or dark_frames.keys())
    completed_white = set((calibration_set.get("white") or {}).get("completedBands") or white_frames.keys())
    for band_id in calibration_band_ids:
        if band_id not in completed_dark:
            issues.append({"field": f"dark.{band_id}", "message": "Dark reference frame is missing"})
        if band_id not in completed_white:
            issues.append({"field": f"white.{band_id}", "message": "White reference frame is missing"})

    calibration_camera = str(calibration_set.get("cameraStableId") or "")
    sample_camera = _sample_camera_stable_id(sample_frames)
    if calibration_camera and sample_camera and calibration_camera != sample_camera:
        issues.append({
            "field": "cameraStableId",
            "message": "Calibration camera identity does not match sample",
            "calibration": calibration_camera,
            "sample": sample_camera,
        })
    elif not calibration_camera or not sample_camera:
        warnings.append({
            "field": "cameraStableId",
            "message": "Camera stable identity is incomplete",
            "calibration": calibration_camera,
            "sample": sample_camera,
        })

    if (calibration_set.get("filterConfigVersion") or "") != (sample_sequence.get("filterConfigVersion") or ""):
        issues.append({
            "field": "filterConfigVersion",
            "message": "Filter config version does not match",
            "calibration": calibration_set.get("filterConfigVersion") or "",
            "sample": sample_sequence.get("filterConfigVersion") or "",
        })
    if (calibration_set.get("filterConfigSource") or "") != (sample_sequence.get("filterConfigSource") or ""):
        warnings.append({
            "field": "filterConfigSource",
            "message": "Filter config source path differs",
            "calibration": calibration_set.get("filterConfigSource") or "",
            "sample": sample_sequence.get("filterConfigSource") or "",
        })

    settings_match = True
    for band_id in sorted(set(calibration_band_ids) & set(sample_frames)):
        sample = sample_frames.get(band_id) or {}
        for reference_name, reference_frames in (("dark", dark_frames), ("white", white_frames)):
            reference = reference_frames.get(band_id) or {}
            for field in ("width", "height", "dtype", "pixelFormat"):
                if reference and sample and reference.get(field) != sample.get(field):
                    issues.append({
                        "field": f"{reference_name}.{band_id}.{field}",
                        "message": f"{reference_name} frame {field} does not match sample",
                        "calibration": reference.get(field),
                        "sample": sample.get(field),
                    })
            for field in ("requestedExposureUs", "actualExposureUs", "requestedGain", "actualGain"):
                if reference and sample and not _numbers_equal(reference.get(field), sample.get(field)):
                    settings_match = False
                    warnings.append({
                        "field": f"{reference_name}.{band_id}.{field}",
                        "message": f"{reference_name} setting does not match sample",
                        "calibration": reference.get(field),
                        "sample": sample.get(field),
                    })

    status = "incompatible" if issues else "warning" if warnings else "compatible"
    return {
        "compatible": status != "incompatible",
        "status": status,
        "issues": issues,
        "warnings": warnings,
        "sameBandSettingsMatched": settings_match and not issues,
        "calibrationId": calibration_set.get("calibrationId"),
    }


def _enabled_band_map(bands: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for band in bands:
        if not isinstance(band, dict) or not band.get("enabled", True):
            continue
        band_id = str(band.get("bandId") or "")
        if band_id:
            result[band_id] = band
    return result


def _frames_by_band(frames: list[dict[str, Any]], capture_type: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        if str(frame.get("captureType") or "sample") != capture_type:
            continue
        band_id = str(frame.get("bandId") or "")
        if band_id:
            result[band_id] = frame
    return result


def _sample_camera_stable_id(sample_frames: dict[str, dict[str, Any]]) -> str:
    first = next(iter(sample_frames.values()), {})
    device = first.get("device") or {}
    return str(device.get("stableId") or device.get("stable_id") or device.get("serial") or device.get("cameraSerial") or device.get("userId") or "")


def _current_view_from_step(step_id: str) -> str | None:
    if not step_id or ":" not in step_id:
        return None
    parts = step_id.split(":")
    for part in parts[1:]:
        if part.startswith("view_"):
            return part
    return None


def _current_band_from_step(step_id: str) -> str | None:
    if not step_id or ":" not in step_id:
        return None
    parts = step_id.split(":")
    if parts[0] in {
        "filter_wheel_move",
        "filter_wheel_position_verify",
        "filter_wheel_settle",
        "band_camera_settings",
        "multispectral_capture",
    }:
        return parts[-1]
    return None
