from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from camera_service import CameraManager
from capture_coordinator import CaptureCoordinator, TrueCapturePlan
from device_discovery import DeviceDiscovery, DeviceRegistry
from hardware_controller import DoorState, HardwareController
from sample_stage import SAMPLE_STAGE_PROTOCOL_UNKNOWN, SampleStageNotImplemented, UnimplementedSampleStage
from serial_service import SerialDependencyError, SerialService
from stm32_controller import Stm32ControllerAdapter
from stm32_protocol import (
    CURRENT_FILTER_WHEEL_MAPPING,
    CURRENT_STM32_FIRMWARE_PROFILE,
    FilterWheelMapping,
    Stm32ProtocolProfile,
)


LOGGER = logging.getLogger(__name__)


class DeviceManagerError(RuntimeError):
    """设备管理层基础异常。"""


class DeviceNotConnectedError(DeviceManagerError):
    """STM32F407 尚未连接。"""


class CameraIntegrationRequired(DeviceManagerError):
    """完整真实采集协调器尚未开放。"""


class DeviceBusyError(DeviceManagerError):
    """设备正在执行互斥动作。"""


class UnsupportedCapabilityError(DeviceManagerError):
    """当前固件/硬件不支持该能力。"""


class DeviceManager:
    """
    上位机设备管理层。

    负责把网页后端与串口服务、硬件控制器连接起来；不直接实现
    两字节协议，也不生成模拟相机图像。
    """

    _DOOR_NAMES = {
        DoorState.UNKNOWN: "unknown",
        DoorState.OPEN: "open",
        DoorState.CLOSED: "closed",
        DoorState.MOVING: "moving",
        DoorState.ERROR: "error",
    }

    def __init__(
        self,
        serial_service: Any | None = None,
        controller_factory: Callable[[Any], HardwareController] = HardwareController,
        camera_manager: CameraManager | None = None,
        capture_coordinator: CaptureCoordinator | None = None,
        sample_stage_controller: Any | None = None,
        discovery: DeviceDiscovery | None = None,
        registry: DeviceRegistry | None = None,
        stm32_protocol_profile: Stm32ProtocolProfile | None = CURRENT_STM32_FIRMWARE_PROFILE,
        filter_wheel_mapping: FilterWheelMapping | None = CURRENT_FILTER_WHEEL_MAPPING,
    ) -> None:
        self.serial = serial_service or SerialService()
        self.controller_factory = controller_factory
        self.stm32_protocol_profile = stm32_protocol_profile
        self.filter_wheel_mapping = filter_wheel_mapping or FilterWheelMapping()
        self.stm32_adapter: Stm32ControllerAdapter | None = None
        self.camera_manager = camera_manager or CameraManager()
        self.registry = registry
        self.discovery = discovery or DeviceDiscovery(
            serial_service=self.serial,
            serial_service_factory=self._new_serial_probe_service,
            camera_manager=self.camera_manager,
        )
        self.controller: HardwareController | None = None
        self.sample_stage_controller = sample_stage_controller or UnimplementedSampleStage()
        self.capture_coordinator = capture_coordinator or CaptureCoordinator(
            camera_manager=self.camera_manager,
            device_manager=self,
            sample_stage_controller=self.sample_stage_controller,
        )
        if self.registry is not None:
            for binding in self.registry.bindings().values():
                self._apply_binding_to_runtime_config(binding)

        self._lock = threading.RLock()
        self._emergency_stopped = False
        self._actuator_lock = threading.RLock()
        self._actuator_timer: threading.Timer | None = None
        self._actuator_token = 0
        self._actuator_busy = False
        self._last_actuator_action = ""

    def list_ports(self) -> list[dict[str, str]]:
        """返回适合直接转换成 JSON 的串口列表。"""

        return [port.to_dict() for port in self.serial.list_ports()]

    def discover_devices(self) -> dict[str, Any]:
        """Scan candidates without treating COM/index as permanent identity."""

        return self.discovery.discover_all()

    def device_bindings(self, discovery: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.registry is None:
            return {"profilePath": "", "bindings": {}, "matches": {}}
        discovery = discovery or self.discovery.discover_all()
        candidates = self._candidates_from_discovery(discovery)
        return self.registry.snapshot(candidates)

    def _candidates_from_discovery(self, discovery: dict[str, Any]) -> list[Any]:
        candidates = [
            self._candidate_from_payload(item)
            for item in discovery.get("candidates", [])
            if isinstance(item, dict)
        ]
        return candidates

    def bind_device(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.registry is None:
            raise DeviceManagerError("设备绑定 registry 尚未初始化")
        discovery = self.discovery.discover_all()
        candidates = [
            self._candidate_from_payload(item)
            for item in discovery.get("candidates", [])
            if isinstance(item, dict)
        ]
        binding = self.registry.bind_from_payload(payload, candidates)
        self._apply_binding_to_runtime_config(binding)
        return {
            "binding": binding.to_dict(),
            "bindings": self.registry.snapshot(candidates),
        }

    def connect(self, port: str) -> dict[str, Any]:
        """连接串口、创建硬件控制器并执行 PING。"""

        with self._lock:
            if self.serial.is_connected or self.controller is not None:
                self.disconnect()

            self.serial.connect(port)
            controller_transport = self._controller_transport()
            controller = self.controller_factory(controller_transport)

            try:
                controller.ping()
            except Exception:
                self.serial.disconnect()
                self.controller = None
                raise

            self.controller = controller
            self._emergency_stopped = False

            LOGGER.info("STM32F407 已连接并通过 PING：%s", self.serial.port_name)
            return self.status()

    def disconnect(self) -> None:
        """尽力执行安全停止，然后断开串口。"""

        with self._lock:
            controller = self.controller

            if controller is not None and self.serial.is_connected:
                try:
                    controller.safe_stop()
                except Exception as exc:
                    LOGGER.warning("断开前执行安全停止失败：%s", exc)

            self.serial.disconnect()
            self.controller = None
            self.stm32_adapter = None
            self._emergency_stopped = False

    def _controller_transport(self) -> Any:
        if self.stm32_protocol_profile is None:
            self.stm32_adapter = None
            return self.serial
        self.stm32_adapter = Stm32ControllerAdapter(
            self.serial,
            profile=self.stm32_protocol_profile,
            filter_wheel=self.filter_wheel_mapping,
        )
        return self.stm32_adapter

    def status(self) -> dict[str, Any]:
        """读取并返回网页需要的完整设备状态。"""

        with self._lock:
            if self.controller is None or not self.serial.is_connected:
                return self._empty_status()

            outputs = self.controller.get_output_status()
            door = self.controller.get_door_status()
            wheel = self.controller.get_wheel_status()
            error_code = self.controller.get_error_status()
            firmware_profile = self._stm32_profile_status()
            adapter_snapshot = self.stm32_adapter.status_snapshot if self.stm32_adapter is not None else None
            adapter_status = adapter_snapshot.status if adapter_snapshot is not None else None

            return {
                "connected": True,
                "port": self.serial.port_name,
                "fanOn": outputs.fan_on,
                "fanDuty": adapter_status.fan_duty if adapter_status is not None else (100 if outputs.fan_on else 0),
                "door": self._DOOR_NAMES[door],
                "wheelPosition": None if wheel == 0x7F else wheel,
                "wheelHomed": wheel != 0x7F,
                "wheelPositionDeg": adapter_status.position_deg if adapter_status is not None else None,
                "wheelTargetDeg": adapter_status.target_deg if adapter_status is not None else None,
                "wheelMotorState": adapter_status.motor_state if adapter_status is not None else "unknown",
                "rgbLed1On": outputs.rgb_led_1_on,
                "rgbLed2On": outputs.rgb_led_2_on,
                "rgbLed3On": outputs.rgb_led_3_on,
                "ledMask": adapter_status.led_mask if adapter_status is not None else outputs.raw,
                "led3Duty": adapter_status.led3_duty if adapter_status is not None else (100 if outputs.rgb_led_3_on else 0),
                "tungsten1On": outputs.tungsten_1_on,
                "tungsten2On": outputs.tungsten_2_on,
                "errorCode": error_code,
                "statusRevision": adapter_snapshot.revision if adapter_snapshot is not None else None,
                "statusReceivedMonotonic": adapter_snapshot.received_monotonic if adapter_snapshot is not None else None,
                "actuatorBusy": self._actuator_busy,
                "lastActuatorAction": self._last_actuator_action,
                "sampleStage": self.sample_stage_status(),
                "stm32FirmwareProfile": firmware_profile,
                "emergencyStopped": (
                    self._emergency_stopped or error_code == 0x08
                ),
                "cameras": self.camera_manager.status(),
            }

    def self_test(self, include_motion: bool = False) -> dict[str, Any]:
        """
        执行通信与基础硬件自检。

        当前固件 Web 自检只做非破坏通信检查和 fresh STATUS，不执行输出或运动。
        """

        with self._lock:
            controller = self._require_controller()
            controller.ping()
            if self.stm32_adapter is not None and hasattr(self.serial, "write_bytes"):
                self.stm32_adapter.query_status(require_fresh=True)

            return {
                "passed": True,
                "includeMotion": False,
                "motionRequestedIgnored": bool(include_motion),
                "status": self.status(),
                "checks": self._self_test_checks(include_motion=False),
            }

    def independent_device_check(self) -> dict[str, Any]:
        """Check controller, RGB and DVP2 domains independently."""

        with self._lock:
            controller_check = self._controller_check()
            status = self.status()

        rgb_check = self._probe_rgb_check()
        multispectral_check = self._probe_multispectral_check()

        with self._lock:
            status = self.status()
            checks = {
                **self._hardware_checks_from_status(status, controller_check),
                "rgbCamera": rgb_check,
                "multispectralCamera": multispectral_check,
                "calibration": {
                    "status": "manual_required",
                    "label": "标定状态",
                    "message": "当前需要操作员人工确认",
                },
            }
            return {
                "passed": all(
                    checks[key]["status"] == "passed"
                    for key in ("controller", "rgbCamera", "multispectralCamera")
                    if key in checks
                ),
                "includeMotion": False,
                "independentDomains": True,
                "status": status,
                "checks": checks,
            }

    def emergency_stop(self) -> dict[str, Any]:
        """执行安全停止并返回停止后的设备状态。"""

        with self._lock:
            controller = self._require_controller()
            controller.safe_stop()
            sample_stage_stop = self.sample_stage_safe_stop()
            self._emergency_stopped = True

            result = self.status()
            result["emergencyStopped"] = True
            result["sampleStageSafeStop"] = sample_stage_stop
            return result

    def fault_clear(self) -> dict[str, Any]:
        """清除 STM32 故障及本地急停状态。"""

        with self._lock:
            self._require_controller()
            raise UnsupportedCapabilityError("当前 STM32 firmware 不支持远程 Fault Clear，请现场断电/复位后重新连接")

    def read_hardware_status(self) -> dict[str, Any]:
        return self.status()

    def set_fan(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            adapter = self._require_adapter()
            before = adapter.status_snapshot.revision
            result = adapter.set_fan(bool(enabled))
            status = adapter.query_status(require_fresh=True, after_revision=before)
            expected_duty = 100 if enabled else 0
            if int(status.fan_duty or 0) != expected_duty:
                raise DeviceManagerError("state_verification_failed: 风扇状态回读与命令不一致")
            return {
                "commandAccepted": result.ok(),
                "fanOn": bool(status.fan_on),
                "fanDuty": status.fan_duty,
                "status": self.status(),
            }

    def set_led3(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            adapter = self._require_adapter()
            before = adapter.status_snapshot.revision
            mask = 0x04 if enabled else 0x00
            result = adapter.set_led_mask(mask)
            status = adapter.query_status(require_fresh=True, after_revision=before)
            if int(status.led_mask or 0) != mask:
                raise DeviceManagerError("state_verification_failed: LED3 状态回读与命令不一致")
            return {
                "commandAccepted": result.ok(),
                "ledMask": status.led_mask,
                "led3Duty": status.led3_duty,
                "status": self.status(),
            }

    def actuator_extend(self, duration_ms: int | None = None) -> dict[str, Any]:
        return self._start_actuator_action("extend", duration_ms)

    def actuator_retract(self, duration_ms: int | None = None) -> dict[str, Any]:
        return self._start_actuator_action("retract", duration_ms)

    def actuator_stop(self) -> dict[str, Any]:
        with self._actuator_lock:
            self._actuator_token += 1
            timer = self._actuator_timer
            self._actuator_timer = None
            self._actuator_busy = False
            self._last_actuator_action = "stop"
            if timer is not None:
                timer.cancel()
        with self._lock:
            adapter = self._require_adapter()
            result = adapter.door_command(0x02)
            return {
                "commandAccepted": result.ok(),
                "action": "stop",
                "motionCompleted": False,
                "endpointFeedbackVerified": False,
                "status": self.status(),
            }

    def move_filter_wheel(self, direction: str, slots: int) -> dict[str, Any]:
        direction = str(direction or "").strip()
        if direction not in {"clockwise", "counterclockwise"}:
            raise ValueError("direction must be clockwise or counterclockwise")
        if isinstance(slots, bool) or not isinstance(slots, int) or not 1 <= slots <= 15:
            raise ValueError("slots must be an integer from 1 to 15")
        slot_delta = slots if direction == "counterclockwise" else -slots
        with self._lock:
            adapter = self._require_adapter()
        result = adapter.move_filter_wheel_relative_slots(slot_delta, max_retries=0)
        return {
            "direction": direction,
            "slots": slots,
            "slotDelta": slot_delta,
            "degrees": slot_delta * adapter.filter_wheel.degrees_per_slot,
            "command": result.to_dict(),
            "failureReason": result.failure_reason,
            "motionCompleted": result.completed_from_status and not result.failure_reason,
            "status": self.status(),
        }

    def stop_filter_wheel(self) -> dict[str, Any]:
        with self._lock:
            adapter = self._require_adapter()
            result = adapter.stop_filter_wheel()
            return {
                "commandAccepted": result.ok(),
                "motionCompleted": False,
                "failureReason": result.failure_reason,
                "status": self.status(),
            }

    def set_filter_wheel_origin(self, operator_confirmed_aligned: bool) -> dict[str, Any]:
        if operator_confirmed_aligned is not True:
            raise ValueError("operatorConfirmedAligned must be true")
        with self._lock:
            adapter = self._require_adapter()
            before = adapter.status_snapshot.revision
            result = adapter.set_origin()
            status = None
            try:
                status = adapter.query_status(require_fresh=True, after_revision=before)
            except Exception:
                LOGGER.warning("SET_ORIGIN 后未收到 fresh STATUS", exc_info=True)
            return {
                "commandAccepted": result.ok(),
                "originEstablished": True,
                "automaticHoming": False,
                "message": "逻辑零点已建立",
                "statusAfter": status.to_dict() if status is not None else None,
                "status": self.status(),
            }

    def capture_status(self) -> dict[str, Any]:
        """返回当前采集状态的副本。"""

        with self._lock:
            snapshot = self.capture_coordinator.snapshot()
            if snapshot.get("state") == "idle":
                snapshot["message"] = "True Capture 编排入口已接入；启动前按 capture plan 动态检查 readiness"
            return snapshot

    def capture_readiness(self, payload: dict[str, Any] | TrueCapturePlan | None = None) -> dict[str, Any]:
        """Evaluate whether the current true-capture plan can run."""

        with self._lock:
            plan = payload if isinstance(payload, TrueCapturePlan) else self._build_true_capture_plan(payload or {})
            status = self.status()
            cameras = status.get("cameras") or {}
            rgb_status = cameras.get("rgb") or {}
            multispectral_status = cameras.get("multispectral") or {}
            sample_stage = status.get("sampleStage") or self.sample_stage_status()
            blocking: list[dict[str, str]] = []
            warnings: list[dict[str, str]] = []

            if not str(plan.sample_id or "").strip():
                blocking.append({"code": "SAMPLE_REQUIRED", "message": "请先创建当前样品"})
            if not str(plan.output_dir or "").strip():
                blocking.append({"code": "OUTPUT_DIR_REQUIRED", "message": "请先创建或指定样品保存目录"})

            controller_ready = bool(status.get("connected")) and status.get("errorCode") in (None, 0)
            if not controller_ready:
                blocking.append({"code": "STM32_NOT_READY", "message": "STM32 控制器未连接或存在故障"})

            rgb_ready = (not plan.rgb_enabled) or bool(rgb_status.get("available"))
            if plan.rgb_enabled and not rgb_ready:
                blocking.append({
                    "code": "CAMERA_NOT_READY",
                    "detailCode": "RGB_NOT_AVAILABLE",
                    "message": rgb_status.get("error") or "RGB 相机不可用",
                })
            rgb_scientific = {}
            if plan.rgb_enabled:
                if hasattr(self.camera_manager, "rgb_scientific_status"):
                    try:
                        rgb_scientific = self.camera_manager.rgb_scientific_status()
                    except Exception as exc:
                        rgb_scientific = {
                            "scientificStrictLossless": False,
                            "scientificCaptureApproved": False,
                            "reason": str(exc),
                            "sourceCompression": "unknown",
                        }
                else:
                    rgb_scientific = rgb_status.get("scientificTransport") or {}
                if rgb_scientific and not rgb_scientific.get(
                    "scientificCaptureApproved",
                    rgb_scientific.get("scientificStrictLossless"),
                ):
                    code = (
                        "RGB_SCIENTIFIC_TRANSPORT_LOSSY"
                        if rgb_scientific.get("sourceCompression") == "lossy"
                        else "RGB_SCIENTIFIC_PROFILE_NOT_CONFIGURED"
                        if rgb_scientific.get("detailCode") == "RGB_SCIENTIFIC_PROFILE_NOT_CONFIGURED"
                        else "RGB_SCIENTIFIC_LOSSLESS_UNAVAILABLE"
                    )
                    blocking.append({
                        "code": "CAMERA_NOT_READY",
                        "detailCode": code,
                        "message": "RGB 正式科学采集链路未通过科研采集准入",
                    })
            rgb_restore = rgb_status.get("settingsRestoreState") or {}
            if plan.rgb_enabled and rgb_restore.get("state") == "device_mismatch":
                blocking.append({"code": "CAMERA_SETTINGS_DEVICE_MISMATCH", "message": "RGB 已保存相机参数与当前设备身份不一致"})
            elif plan.rgb_enabled and rgb_restore.get("state") == "failed":
                warnings.append({"code": "CAMERA_SETTINGS_RESTORE_FAILED", "message": rgb_restore.get("restoreError") or "RGB 已保存参数恢复失败"})

            multispectral_ready = (not plan.multispectral_enabled) or bool(multispectral_status.get("available"))
            if plan.multispectral_enabled and not multispectral_ready:
                blocking.append({
                    "code": "CAMERA_NOT_READY",
                    "detailCode": "DVP2_NOT_AVAILABLE",
                    "message": multispectral_status.get("error") or "DVP2 多光谱相机不可用",
                })
            multispectral_restore = multispectral_status.get("settingsRestoreState") or {}
            if plan.multispectral_enabled and multispectral_restore.get("state") == "device_mismatch":
                blocking.append({"code": "CAMERA_SETTINGS_DEVICE_MISMATCH", "message": "DVP2 已保存相机参数与当前设备身份不一致"})
            elif plan.multispectral_enabled and multispectral_restore.get("state") == "failed":
                warnings.append({"code": "CAMERA_SETTINGS_RESTORE_FAILED", "message": multispectral_restore.get("restoreError") or "DVP2 已保存参数恢复失败"})

            filter_wheel_ready = (not plan.multispectral_enabled) or (
                bool(status.get("connected")) and bool(status.get("wheelHomed"))
            )
            if plan.multispectral_enabled and not filter_wheel_ready:
                blocking.append({"code": "FILTER_WHEEL_NOT_READY", "message": "滤光轮未连接或尚未建立逻辑零点"})

            sample_stage_required = plan.capture_mode == "multi_view" and bool((plan.rotation_plan or {}).get("enabled"))
            sample_stage_ready = (not sample_stage_required) or (
                bool(sample_stage.get("available")) and bool(sample_stage.get("protocolKnown"))
            )
            if sample_stage_required and not sample_stage_ready:
                blocking.append({
                    "code": "SAMPLE_STAGE_NOT_READY",
                    "detailCode": sample_stage.get("lastError") or SAMPLE_STAGE_PROTOCOL_UNKNOWN,
                    "message": "真实多视角采集需要独立样品旋转台，当前协议未知",
                })

            calibration_ready = True
            if plan.multispectral_enabled:
                if plan.calibration_mode == "existing":
                    calibration_ready = bool(plan.calibration_id)
                    if not calibration_ready:
                        blocking.append({
                            "code": "CALIBRATION_REQUIRED",
                            "detailCode": "CALIBRATION_ID_REQUIRED",
                            "message": "使用已有 CalibrationSet 时必须填写有效 calibrationId",
                        })
                    elif not self._calibration_set_exists(plan.output_dir, str(plan.calibration_id)):
                        calibration_ready = False
                        blocking.append({
                            "code": "CALIBRATION_REQUIRED",
                            "detailCode": "CALIBRATION_ID_NOT_FOUND",
                            "message": "未在当前样品目录找到指定 CalibrationSet",
                        })
                elif plan.calibration_mode == "capture_new":
                    calibration_ready = bool(plan.capture_dark and plan.capture_white)
                    if not calibration_ready:
                        blocking.append({"code": "CALIBRATION_REQUIRED", "detailCode": "CALIBRATION_CAPTURE_REQUIRED", "message": "重新校正必须采集 Dark 和 White"})
                    if not plan.operator_confirmed_dark:
                        blocking.append({"code": "CALIBRATION_REQUIRED", "detailCode": "DARK_OPERATOR_CONFIRMATION_REQUIRED", "message": "请确认暗场遮光状态"})
                    if not plan.operator_confirmed_white:
                        blocking.append({"code": "CALIBRATION_REQUIRED", "detailCode": "WHITE_OPERATOR_CONFIRMATION_REQUIRED", "message": "请放置白板并确认"})

            warnings.append({
                "code": "HARDWARE_ACCEPTANCE_NOT_PASSED",
                "message": "P1B hardware acceptance checklist 仍未 PASS；软件可执行不等于生产放行",
            })

            sample_stage_block_codes = {
                SAMPLE_STAGE_PROTOCOL_UNKNOWN,
                str(sample_stage.get("lastError") or ""),
            }
            single_view_blocking = [
                reason for reason in blocking if str(reason.get("code") or "") not in sample_stage_block_codes
            ]
            ready = not blocking
            single_view_ready = not single_view_blocking
            multi_view_ready = sample_stage_ready and ready

            capabilities = {
                "singleViewReady": single_view_ready,
                "multiViewReady": multi_view_ready,
                "rgbReady": rgb_ready,
                "rgbScientificStrictLossless": bool(rgb_scientific.get("scientificStrictLossless")) if rgb_scientific else None,
                "rgbScientificCaptureApproved": bool(rgb_scientific.get("scientificCaptureApproved")) if rgb_scientific else None,
                "rgbScientificTransport": rgb_scientific,
                "multispectralReady": multispectral_ready,
                "calibrationReady": calibration_ready,
                "filterWheelReady": filter_wheel_ready,
                "sampleStageReady": sample_stage_ready,
                "controllerReady": controller_ready,
                "productionAccepted": False,
            }
            return {
                "ready": ready,
                "trueCapturePrepared": ready,
                "captureMode": plan.capture_mode,
                "plan": plan.to_dict(),
                "blockingReasons": blocking,
                "warnings": warnings,
                "capabilities": capabilities,
                "singleView": {
                    "ready": single_view_ready,
                    "blockingReasons": single_view_blocking,
                },
                "multiView": {
                    "ready": multi_view_ready,
                    "blockingReasons": blocking if plan.capture_mode == "multi_view" else [
                        {"code": "SAMPLE_STAGE_NOT_READY", "detailCode": sample_stage.get("lastError") or SAMPLE_STAGE_PROTOCOL_UNKNOWN, "message": "样品台协议未知"}
                    ] if not sample_stage_ready else [],
                },
            }

    @staticmethod
    def _calibration_set_exists(output_dir: Any, calibration_id: str) -> bool:
        if not output_dir or not calibration_id:
            return False
        from pathlib import Path

        root = Path(str(output_dir))
        candidates = [
            root / "calibration" / f"calibration_set_{calibration_id}.json",
            root / "calibration" / f"{calibration_id}.json",
        ]
        return any(path.exists() and path.is_file() for path in candidates)

    def start_capture(self, sample_id: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Start formal true capture through CaptureCoordinator protected paths."""

        with self._lock:
            plan_payload = dict(payload or {})
            if sample_id and not plan_payload.get("sampleId"):
                plan_payload["sampleId"] = sample_id
            plan = self._build_true_capture_plan(plan_payload)
            readiness = self.capture_readiness(plan)
            if not readiness.get("ready"):
                codes = ", ".join(str(item.get("code")) for item in readiness.get("blockingReasons") or [])
                raise CameraIntegrationRequired(f"True Capture readiness 未通过: {codes}")

        capture = self.capture_coordinator.run_true_capture(plan)
        capture["readiness"] = readiness
        return capture

    def cancel_capture(self) -> dict[str, Any]:
        """取消当前采集；设备已连接时同时执行安全停止。"""

        with self._lock:
            if self.controller is not None and self.serial.is_connected:
                self.controller.safe_stop()
                self._emergency_stopped = True
            try:
                self.sample_stage_safe_stop()
            except Exception as exc:
                LOGGER.warning("取消采集时样品台 safe_stop 失败：%s", exc)

            return self.capture_coordinator.request_cancel()

    def _build_true_capture_plan(self, payload: dict[str, Any]) -> TrueCapturePlan:
        payload = dict(payload or {})
        capture_mode = str(payload.get("captureMode") or payload.get("mode") or "single_view").strip().lower()
        if capture_mode in {"single", "single-view", "singleview"}:
            capture_mode = "single_view"
        if capture_mode in {"multi", "multi-view", "multiview", "sample_multiview"}:
            capture_mode = "multi_view"
        if capture_mode not in {"single_view", "multi_view"}:
            raise ValueError("captureMode must be single_view or multi_view")

        calibration_mode = str(payload.get("calibrationMode") or "existing").strip().lower()
        if calibration_mode in {"new", "capture-new", "capture"}:
            calibration_mode = "capture_new"
        if calibration_mode not in {"existing", "capture_new", "none"}:
            raise ValueError("calibrationMode must be existing, capture_new, or none")
        capture_new = calibration_mode == "capture_new"
        require_calibration = self._bool_payload(payload.get("requireCalibration"), default=calibration_mode != "none")
        rotation_plan = payload.get("rotationPlan") if isinstance(payload.get("rotationPlan"), dict) else {}
        sample_rotation = payload.get("sampleRotation") if isinstance(payload.get("sampleRotation"), dict) else None
        if not rotation_plan and sample_rotation is not None:
            try:
                from rotation_plan import build_capture_rotation_plan

                rotation_plan = build_capture_rotation_plan(sample_rotation)
            except Exception:
                rotation_plan = dict(sample_rotation)
        if capture_mode == "single_view":
            rotation_plan = {"enabled": False, "views": []}

        return TrueCapturePlan(
            sample_id=str(payload.get("sampleId") or payload.get("sample_id") or "").strip(),
            capture_mode=capture_mode,
            rgb_enabled=self._bool_payload(payload.get("rgbEnabled"), default=True),
            multispectral_enabled=self._bool_payload(payload.get("multispectralEnabled"), default=True),
            calibration_mode=calibration_mode,
            calibration_id=str(payload.get("calibrationId") or payload.get("calibration_id") or "").strip() or None,
            capture_dark=self._bool_payload(payload.get("captureDark"), default=capture_new),
            capture_white=self._bool_payload(payload.get("captureWhite"), default=capture_new),
            rotation_plan=dict(rotation_plan or {}),
            band_plan=payload.get("bandPlan"),
            filter_config_path=payload.get("filterConfigPath"),
            settling_ms=self._optional_int(payload.get("settlingMs")),
            sample_stage_settling_ms=self._optional_int(payload.get("sampleStageSettlingMs"), default=300) or 300,
            rgb_dir_name=str(payload.get("rgbDirName") or "rgb"),
            multispectral_dir_name=str(payload.get("multispectralDirName") or "multispectral"),
            output_dir=payload.get("outputDir") or payload.get("captureDir"),
            return_home=self._bool_payload(payload.get("returnHome"), default=True),
            require_calibration=require_calibration,
            sample_stage_mode=str(payload.get("sampleStageMode") or "hardware").strip().lower(),
            operator_confirmed_dark=self._bool_payload(payload.get("operatorConfirmedDark", payload.get("operatorConfirmed")), default=False),
            operator_confirmed_white=self._bool_payload(payload.get("operatorConfirmedWhite", payload.get("operatorConfirmed")), default=False),
            rgb_led_mask=self._optional_int(payload.get("rgbLedMask"), default=0x03) or 0x03,
            tungsten_mask=self._optional_int(payload.get("tungstenMask"), default=0x03) or 0x03,
        )

    @staticmethod
    def _bool_payload(value: Any, *, default: bool = False) -> bool:
        if value is None:
            return bool(default)
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no", "off"}
        return bool(value)

    @staticmethod
    def _optional_int(value: Any, *, default: int | None = None) -> int | None:
        if value in (None, ""):
            return default
        return int(value)

    def _require_controller(self) -> HardwareController:
        if self.controller is None or not self.serial.is_connected:
            raise DeviceNotConnectedError("STM32F407 尚未连接")

        return self.controller

    def _require_adapter(self) -> Stm32ControllerAdapter:
        self._require_controller()
        if self.stm32_adapter is None:
            raise UnsupportedCapabilityError("当前连接未启用 STM32 AA55 current firmware adapter")
        return self.stm32_adapter

    def _start_actuator_action(self, action: str, duration_ms: int | None) -> dict[str, Any]:
        duration = 1000 if duration_ms is None else int(duration_ms)
        if not 100 <= duration <= 5000:
            raise ValueError("durationMs must be 100..5000")
        command = {"extend": 0x01, "retract": 0x00}[action]
        with self._actuator_lock:
            if self._actuator_busy:
                raise DeviceBusyError("device_busy: 推杆动作尚未完成，请先停止")
            self._actuator_busy = True
            self._last_actuator_action = action
            self._actuator_token += 1
            token = self._actuator_token
        try:
            with self._lock:
                adapter = self._require_adapter()
                result = adapter.door_command(command)
                status = self.status()
        except Exception:
            with self._actuator_lock:
                self._actuator_busy = False
            raise

        timer = threading.Timer(duration / 1000.0, self._auto_stop_actuator, args=(token,))
        timer.daemon = True
        with self._actuator_lock:
            if token == self._actuator_token:
                self._actuator_timer = timer
                timer.start()
        return {
            "commandAccepted": result.ok(),
            "action": action,
            "durationMs": duration,
            "autoStopScheduled": True,
            "motionCompleted": False,
            "endpointFeedbackVerified": False,
            "status": status,
        }

    def _auto_stop_actuator(self, token: int) -> None:
        with self._actuator_lock:
            if token != self._actuator_token or not self._actuator_busy:
                return
        try:
            with self._lock:
                if self.stm32_adapter is not None and self.serial.is_connected:
                    self.stm32_adapter.door_command(0x02)
        except Exception as exc:
            LOGGER.warning("推杆定时 STOP 失败：%s", exc)
        finally:
            with self._actuator_lock:
                if token == self._actuator_token:
                    self._actuator_busy = False
                    self._actuator_timer = None

    def _self_test_checks(self, include_motion: bool = False) -> dict[str, dict[str, Any]]:
        status = self.status()
        connected = bool(status.get("connected"))
        wheel_homed = bool(status.get("wheelHomed"))
        door = status.get("door") or "unknown"
        error_code = status.get("errorCode")
        has_fault = bool(status.get("emergencyStopped") or (error_code not in (None, 0)))

        door_state = "passed" if door in {"open", "closed"} else "warning"
        if door == "error":
            door_state = "failed"

        wheel_state = "passed" if include_motion and wheel_homed else "manual_required"
        if include_motion and not wheel_homed:
            wheel_state = "warning"
        camera_checks = self.camera_manager.checks(probe_rgb=include_motion)

        return {
            "controller": {
                "status": "failed" if has_fault else "passed" if connected else "not_connected",
                "label": "STM32 控制器",
                "message": "PING 通过" if connected and not has_fault else "控制器未连接或存在故障",
            },
            "door": {
                "status": door_state if connected else "not_connected",
                "label": "升降门",
                "message": f"门状态: {door}",
            },
            "fan": {
                "status": "manual_required" if connected else "not_connected",
                "label": "风扇",
                "message": f"风扇 duty: {status.get('fanDuty')}" if connected else "风扇未开启",
            },
            "filterWheel": {
                "status": wheel_state if connected else "not_connected",
                "label": "滤光轮",
                "message": f"位置: {status.get('wheelPosition')}" if wheel_homed else "需要人工设定逻辑零点或移动验证",
            },
            "rgbCamera": camera_checks["rgbCamera"],
            "multispectralCamera": camera_checks["multispectralCamera"],
            "light": {
                "status": "manual_required" if connected else "not_connected",
                "label": "光源控制",
                "message": "控制层已接入，需在光源检查页人工确认输出",
            },
            "calibration": {
                "status": "manual_required",
                "label": "标定状态",
                "message": "当前需要操作员人工确认",
            },
        }

    def _controller_check(self) -> dict[str, Any]:
        if self.controller is not None and self.serial.is_connected:
            try:
                self.controller.ping()
                return {
                    "status": "passed",
                    "label": "STM32 控制器",
                    "message": "PING 通过",
                    "verified": True,
                }
            except Exception as exc:
                return {
                    "status": "failed",
                    "label": "STM32 控制器",
                    "message": str(exc),
                    "verified": False,
                }
        try:
            ports = self.serial.list_ports()
        except SerialDependencyError as exc:
            return {
                "status": "dependency_missing",
                "label": "STM32 控制器",
                "message": str(exc),
                "verified": False,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "label": "STM32 控制器",
                "message": f"读取串口列表失败：{exc}",
                "verified": False,
            }
        return {
            "status": "not_connected" if ports else "no_serial_port",
            "label": "STM32 控制器",
            "message": "请选择串口并连接 STM32" if ports else "未发现可用串口",
            "verified": False,
            "candidateCount": len(ports),
        }

    def _hardware_checks_from_status(
        self,
        status: dict[str, Any],
        controller_check: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        connected = bool(status.get("connected"))
        wheel_homed = bool(status.get("wheelHomed"))
        door = status.get("door") or "unknown"
        door_state = "passed" if door in {"open", "closed"} else "warning"
        if door == "error":
            door_state = "failed"
        return {
            "controller": controller_check,
            "door": {
                "status": door_state if connected else "not_connected",
                "label": "升降门",
                "message": f"门状态: {door}" if connected else "STM32 未连接，未读取门状态",
            },
            "fan": {
                "status": "manual_required" if connected else "not_connected",
                "label": "风扇",
                "message": f"风扇 duty: {status.get('fanDuty')}" if connected else "风扇未开启",
            },
            "filterWheel": {
                "status": "passed" if connected and wheel_homed else "manual_required" if connected else "not_connected",
                "label": "滤光轮",
                "message": f"位置: {status.get('wheelPosition')}" if wheel_homed else "需要人工设定逻辑零点或移动验证",
            },
            "light": {
                "status": "manual_required" if connected else "not_connected",
                "label": "光源控制",
                "message": "控制层已接入，需在光源检查页人工确认输出" if connected else "STM32 未连接，未读取光源输出",
            },
        }

    def _probe_rgb_check(self) -> dict[str, Any]:
        try:
            if hasattr(self.camera_manager, "probe_rgb"):
                result = self.camera_manager.probe_rgb()
                return self.camera_manager._rgb_check(result.get("status") or {})
            return self.camera_manager.checks(probe_rgb=True)["rgbCamera"]
        except Exception as exc:
            return {
                "status": "failed",
                "label": "RGB 相机",
                "message": str(exc),
                "cameraStatus": {"available": False, "error": str(exc)},
            }

    def _probe_multispectral_check(self) -> dict[str, Any]:
        try:
            if hasattr(self.camera_manager, "probe_multispectral"):
                result = self.camera_manager.probe_multispectral()
                return self.camera_manager._multispectral_check(result.get("status") or {})
            return self.camera_manager.checks(probe_rgb=False)["multispectralCamera"]
        except Exception as exc:
            status = {}
            try:
                status = self.camera_manager.status().get("multispectral") or {}
            except Exception:
                status = {"available": False}
            status = {**status, "error": status.get("error") or str(exc)}
            if hasattr(self.camera_manager, "_multispectral_check"):
                return self.camera_manager._multispectral_check(status)
            return {
                "status": "failed",
                "label": "多光谱相机",
                "message": str(exc),
                "cameraStatus": status,
            }

    def _empty_status(self) -> dict[str, Any]:
        return {
            "connected": False,
            "port": "",
            "fanOn": False,
            "door": "unknown",
            "wheelPosition": None,
            "wheelHomed": False,
            "rgbLed1On": False,
            "rgbLed2On": False,
            "rgbLed3On": False,
            "tungsten1On": False,
            "tungsten2On": False,
            "errorCode": None,
            "sampleStage": self.sample_stage_status(),
            "stm32FirmwareProfile": self._stm32_profile_status(),
            "emergencyStopped": False,
            "cameras": self.camera_manager.status(),
        }

    def sample_stage_status(self) -> dict[str, Any]:
        """Return the independent fruit rotation stage status.

        This is deliberately separate from the filter wheel STM32 status.
        Until a real sample-stage protocol is documented, the default adapter
        reports SAMPLE_STAGE_PROTOCOL_UNKNOWN and no position feedback.
        """

        stage = self.sample_stage_controller
        if stage is None:
            stage = UnimplementedSampleStage()
            self.sample_stage_controller = stage
        if hasattr(stage, "get_status"):
            status = stage.get_status()
            return status.to_dict() if hasattr(status, "to_dict") else dict(status or {})
        return {
            "connected": bool(getattr(stage, "is_connected", False)),
            "available": bool(getattr(stage, "implemented", False)),
            "homed": None,
            "currentAngleDeg": None,
            "targetAngleDeg": None,
            "moving": bool(getattr(stage, "is_moving", False)),
            "lastCommand": "",
            "lastError": "SAMPLE_STAGE_STATUS_UNAVAILABLE",
            "fault": "SAMPLE_STAGE_STATUS_UNAVAILABLE",
            "hardwareMode": str(getattr(stage, "mode", "hardware")),
            "implemented": bool(getattr(stage, "implemented", False)),
            "protocolKnown": False,
            "positionFeedbackSupported": False,
        }

    def sample_stage_home(self) -> dict[str, Any]:
        return self._run_sample_stage_command("home")

    def sample_stage_move_absolute(self, angle_deg: float, *, direction: str = "CW") -> dict[str, Any]:
        angle = self._normalize_sample_angle(angle_deg)
        return self._run_sample_stage_command("move_to", angle, direction=direction)

    def sample_stage_move_relative(self, delta_deg: float, *, direction: str = "CW") -> dict[str, Any]:
        if isinstance(delta_deg, bool):
            raise ValueError("deltaDeg must be a number")
        delta = float(delta_deg)
        if abs(delta) > 360.0:
            raise ValueError("deltaDeg must be within -360..360")
        return self._run_sample_stage_command("move_relative", delta, direction=direction)

    def sample_stage_stop(self) -> dict[str, Any]:
        stage = self._sample_stage()
        try:
            if hasattr(stage, "stop"):
                result = stage.stop()
            elif hasattr(stage, "safe_stop"):
                result = stage.safe_stop()
            else:
                raise SampleStageNotImplemented("SAMPLE_STAGE_STOP_UNAVAILABLE")
            return {
                "command": "stop",
                "result": dict(result or {}),
                "status": self.sample_stage_status(),
            }
        except SampleStageNotImplemented as exc:
            raise UnsupportedCapabilityError(str(exc)) from exc

    def sample_stage_safe_stop(self) -> dict[str, Any]:
        stage = self._sample_stage()
        if hasattr(stage, "safe_stop"):
            result = stage.safe_stop()
            return {"command": "safe_stop", "result": dict(result or {}), "status": self.sample_stage_status()}
        return {"command": "safe_stop", "result": {"commandSent": False}, "status": self.sample_stage_status()}

    def _sample_stage(self) -> Any:
        if self.sample_stage_controller is None:
            self.sample_stage_controller = UnimplementedSampleStage()
        return self.sample_stage_controller

    def _run_sample_stage_command(self, command: str, *args: Any, direction: str = "CW") -> dict[str, Any]:
        stage = self._sample_stage()
        try:
            if command == "home":
                if hasattr(stage, "home"):
                    result = stage.home()
                else:
                    result = stage.home_sample_stage()
            elif command == "move_to":
                if hasattr(stage, "move_to"):
                    result = stage.move_to(args[0], direction=direction)
                else:
                    result = stage.move_sample_stage_to_angle(args[0], direction=direction)
            elif command == "move_relative":
                if hasattr(stage, "move_relative"):
                    result = stage.move_relative(args[0], direction=direction)
                else:
                    position = stage.get_sample_stage_position()
                    angle = position.angle_deg if hasattr(position, "angle_deg") else None
                    if angle is None:
                        raise SampleStageNotImplemented("SAMPLE_STAGE_RELATIVE_MOVE_REQUIRES_POSITION_FEEDBACK")
                    result = stage.move_sample_stage_to_angle(self._normalize_sample_angle(float(angle) + float(args[0])), direction=direction)
            else:
                raise ValueError(f"unknown sample stage command: {command}")
            return {
                "command": command,
                "result": dict(result or {}),
                "status": self.sample_stage_status(),
            }
        except SampleStageNotImplemented as exc:
            raise UnsupportedCapabilityError(str(exc)) from exc

    @staticmethod
    def _normalize_sample_angle(value: float) -> float:
        if isinstance(value, bool):
            raise ValueError("angleDeg must be a number")
        angle = float(value)
        if not -360_000.0 < angle < 360_000.0:
            raise ValueError("angleDeg is outside supported bounds")
        normalized = angle % 360.0
        return 0.0 if abs(normalized) < 1e-9 else normalized

    def _stm32_profile_status(self) -> dict[str, Any]:
        adapter = self.stm32_adapter
        if adapter is None:
            return {
                "enabled": False,
                "currentFirmwareProfileValidated": False,
                "diagnostics": {},
                "info": None,
            }
        info = adapter.info_cache
        return {
            "enabled": True,
            "currentFirmwareProfileValidated": adapter.current_firmware_profile_validated,
            "diagnostics": adapter.validate_profile_consistency(),
            "info": info.to_dict() if info is not None else None,
            "tungstenSupported": adapter.tungsten_supported,
            "automaticHoming": adapter.automatic_homing_supported,
            "physicalEncoderVerified": adapter.physical_encoder_verified,
            "doorEndpointFeedback": adapter.door_endpoint_feedback_supported,
            "faultClearSupported": False,
            "statusRevision": adapter.status_snapshot.revision,
            "statusReceivedMonotonic": adapter.status_snapshot.received_monotonic,
        }

    def _new_serial_probe_service(self) -> SerialService:
        return SerialService(
            serial_factory=getattr(self.serial, "_serial_factory", None),
            ports_provider=getattr(self.serial, "_ports_provider", None),
            default_timeout_s=getattr(self.serial, "_default_timeout_s", 0.5),
        )

    @staticmethod
    def _candidate_from_payload(payload: dict[str, Any]):
        from device_discovery import DeviceCandidate

        return DeviceCandidate(
            kind=str(payload.get("kind") or ""),
            role=payload.get("role"),
            stable_id=payload.get("stableId"),
            display_name=str(payload.get("displayName") or ""),
            connection=payload.get("connection"),
            status=str(payload.get("status") or ""),
            metadata=dict(payload.get("metadata") or {}),
        )

    def _apply_binding_to_runtime_config(self, binding: Any) -> None:
        if getattr(binding, "role", "") == "RGB_CAMERA" and binding.last_device_index is not None:
            rgb = getattr(self.camera_manager, "rgb", None)
            if rgb is not None and hasattr(rgb, "configure"):
                config = getattr(rgb, "config", None)
                if config is not None and hasattr(config, "to_dict"):
                    rgb.configure({**config.to_dict(), "deviceIndex": binding.last_device_index})
        if getattr(binding, "role", "") == "MULTISPECTRAL_CAMERA" and binding.stable_id:
            multi = getattr(self.camera_manager, "multispectral", None)
            if multi is not None:
                if hasattr(multi, "serial_number"):
                    multi.serial_number = binding.stable_id
                if hasattr(multi, "stable_id"):
                    multi.stable_id = binding.stable_id
                if getattr(binding, "display_name", "") and hasattr(multi, "friendly_name"):
                    multi.friendly_name = binding.display_name
