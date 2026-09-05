from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class CaptureState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
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
        return {
            "captureId": self.capture_id,
            "sampleId": self.sample_id,
            "state": self.state.value,
            "status": self.state.value,
            "currentStep": self.current_step,
            "progress": self.progress,
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
        "rgb_capture": 60_000,
    }

    def __init__(
        self,
        *,
        camera_manager: Any | None = None,
        device_manager: Any | None = None,
        hardware_controller: Any | None = None,
        safe_stop_callback: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.time,
        capture_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.camera_manager = camera_manager
        self.device_manager = device_manager
        self.hardware_controller = hardware_controller
        self.safe_stop_callback = safe_stop_callback
        self.clock = clock
        self.capture_id_factory = capture_id_factory or (lambda: time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
        self._run = CaptureRun(capture_id="")

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
        try:
            if self.safe_stop_callback is not None:
                self.safe_stop_callback()
                return
            if self.hardware_controller is not None and hasattr(self.hardware_controller, "safe_stop"):
                self.hardware_controller.safe_stop()
                return
            controller = getattr(self.device_manager, "controller", None)
            if controller is not None and hasattr(controller, "safe_stop"):
                controller.safe_stop()
        except Exception as exc:
            if self._run.error is None:
                self._run.error = CaptureSafetyError("安全停止失败", cause=exc).to_dict()
            else:
                self._run.error["safeStopError"] = str(exc)

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
            self.safe_stop()
            self._cancel_pending_steps(exc.step)
            self._run.state = CaptureState.CANCELLED
            self._run.current_step = None
            self._run.finished_at = self.clock()
            self._run.metadata = self.build_metadata()
        except CaptureCoordinatorError as exc:
            self._fail_current_step(exc)
            self._run.error = exc.to_dict()
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
        self._run.metadata.setdefault("views", []).append({
            "view_id": view_id,
            "view_index": view_index,
            "rgb_files": [frame_metadata["relativePath"]],
        })
        return {
            "saved": True,
            "path": frame_metadata["relativePath"],
            "width": frame_metadata["width"],
            "height": frame_metadata["height"],
            "dtype": frame_metadata["dtype"],
            "channels": frame_metadata["channels"],
            "previewWasRunning": bool(capture_meta.get("previewWasRunning")),
        }

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
    def _validate_filename(value: str) -> None:
        name = str(value or "").strip()
        if not name or name in {".", ".."}:
            raise ValueError("RGB 文件名不能为空")
        if Path(name).is_absolute() or any(separator and separator in name for separator in (os.sep, os.altsep)):
            raise ValueError("RGB 文件名不能包含路径")
        if Path(name).suffix.lower() != ".png":
            raise ValueError("RGB 文件名必须是 .png")

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
