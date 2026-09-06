from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from camera_service import CameraManager
from capture_coordinator import CaptureCoordinator
from device_discovery import DeviceDiscovery, DeviceRegistry
from hardware_controller import DoorState, HardwareController
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
        self.sample_stage_controller = sample_stage_controller
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
            self._emergency_stopped = True

            result = self.status()
            result["emergencyStopped"] = True
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
                snapshot["message"] = "完整真实采集协调器骨架已接入，真实采集启动仍未开放"
            return snapshot

    def start_capture(self, sample_id: str = "") -> dict[str, Any]:
        """CaptureCoordinator 接入前，明确拒绝启动真实采集。"""

        with self._lock:
            self._require_controller()
            raise CameraIntegrationRequired("完整真实采集协调器尚未开放，不能开始真实采集")

    def cancel_capture(self) -> dict[str, Any]:
        """取消当前采集；设备已连接时同时执行安全停止。"""

        with self._lock:
            if self.controller is not None and self.serial.is_connected:
                self.controller.safe_stop()
                self._emergency_stopped = True

            return self.capture_coordinator.request_cancel()

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
            "stm32FirmwareProfile": self._stm32_profile_status(),
            "emergencyStopped": False,
            "cameras": self.camera_manager.status(),
        }

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
