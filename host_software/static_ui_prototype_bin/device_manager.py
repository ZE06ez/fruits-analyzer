from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from camera_service import CameraManager
from capture_coordinator import CaptureCoordinator
from device_discovery import DeviceDiscovery, DeviceRegistry
from hardware_controller import DoorState, HardwareController
from serial_service import SerialService


LOGGER = logging.getLogger(__name__)


class DeviceManagerError(RuntimeError):
    """设备管理层基础异常。"""


class DeviceNotConnectedError(DeviceManagerError):
    """STM32F407 尚未连接。"""


class CameraIntegrationRequired(DeviceManagerError):
    """完整真实采集协调器尚未开放。"""


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
        discovery: DeviceDiscovery | None = None,
        registry: DeviceRegistry | None = None,
    ) -> None:
        self.serial = serial_service or SerialService()
        self.controller_factory = controller_factory
        self.camera_manager = camera_manager or CameraManager()
        self.registry = registry
        self.discovery = discovery or DeviceDiscovery(
            serial_service=self.serial,
            serial_service_factory=self._new_serial_probe_service,
            camera_manager=self.camera_manager,
        )
        self.controller: HardwareController | None = None
        self.capture_coordinator = capture_coordinator or CaptureCoordinator(
            camera_manager=self.camera_manager,
            device_manager=self,
        )
        if self.registry is not None:
            for binding in self.registry.bindings().values():
                self._apply_binding_to_runtime_config(binding)

        self._lock = threading.RLock()
        self._emergency_stopped = False

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
            controller = self.controller_factory(self.serial)

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
            self._emergency_stopped = False

    def status(self) -> dict[str, Any]:
        """读取并返回网页需要的完整设备状态。"""

        with self._lock:
            if self.controller is None or not self.serial.is_connected:
                return self._empty_status()

            outputs = self.controller.get_output_status()
            door = self.controller.get_door_status()
            wheel = self.controller.get_wheel_status()
            error_code = self.controller.get_error_status()

            return {
                "connected": True,
                "port": self.serial.port_name,
                "fanOn": outputs.fan_on,
                "door": self._DOOR_NAMES[door],
                "wheelPosition": None if wheel == 0x7F else wheel,
                "wheelHomed": wheel != 0x7F,
                "rgbLed1On": outputs.rgb_led_1_on,
                "rgbLed2On": outputs.rgb_led_2_on,
                "tungsten1On": outputs.tungsten_1_on,
                "tungsten2On": outputs.tungsten_2_on,
                "errorCode": error_code,
                "emergencyStopped": (
                    self._emergency_stopped or error_code == 0x08
                ),
                "cameras": self.camera_manager.status(),
            }

    def self_test(self, include_motion: bool = False) -> dict[str, Any]:
        """
        执行通信与基础硬件自检。

        默认不让滤光轮运动；只有 include_motion=True 时才执行寻零。
        """

        with self._lock:
            controller = self._require_controller()
            controller.ping()
            controller.fan_on()

            if include_motion:
                controller.wheel_home()

            return {
                "passed": True,
                "includeMotion": bool(include_motion),
                "status": self.status(),
                "checks": self._self_test_checks(include_motion=include_motion),
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
            controller = self._require_controller()
            controller.fault_clear()
            self._emergency_stopped = False
            return self.status()

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
                "status": "passed" if status.get("fanOn") else "warning" if connected else "not_connected",
                "label": "风扇",
                "message": "风扇已开启" if status.get("fanOn") else "风扇未开启",
            },
            "filterWheel": {
                "status": wheel_state if connected else "not_connected",
                "label": "滤光轮",
                "message": f"位置: {status.get('wheelPosition')}" if wheel_homed else "尚未确认 HOME",
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
            "tungsten1On": False,
            "tungsten2On": False,
            "errorCode": None,
            "emergencyStopped": False,
            "cameras": self.camera_manager.status(),
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
