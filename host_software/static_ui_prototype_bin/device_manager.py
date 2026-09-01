from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from hardware_controller import DoorState, HardwareController
from serial_service import SerialService


LOGGER = logging.getLogger(__name__)


class DeviceManagerError(RuntimeError):
    """设备管理层基础异常。"""


class DeviceNotConnectedError(DeviceManagerError):
    """STM32F407 尚未连接。"""


class CameraIntegrationRequired(DeviceManagerError):
    """真实相机服务尚未接入。"""


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
    ) -> None:
        self.serial = serial_service or SerialService()
        self.controller_factory = controller_factory
        self.controller: HardwareController | None = None

        self._lock = threading.RLock()
        self._emergency_stopped = False
        self._capture = self._not_ready_capture_status()

    def list_ports(self) -> list[dict[str, str]]:
        """返回适合直接转换成 JSON 的串口列表。"""

        return [port.to_dict() for port in self.serial.list_ports()]

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
            return dict(self._capture)

    def start_capture(self, sample_id: str = "") -> dict[str, Any]:
        """相机服务接入前，明确拒绝启动真实采集。"""

        with self._lock:
            self._require_controller()
            self._capture = {
                "status": "not_ready",
                "progress": 0,
                "sampleId": str(sample_id).strip(),
                "message": "相机服务尚未接入，不能开始真实采集",
            }
            raise CameraIntegrationRequired(self._capture["message"])

    def cancel_capture(self) -> dict[str, Any]:
        """取消当前采集；设备已连接时同时执行安全停止。"""

        with self._lock:
            if self.controller is not None and self.serial.is_connected:
                self.controller.safe_stop()
                self._emergency_stopped = True

            self._capture = {
                "status": "cancelled",
                "progress": 0,
                "message": "采集已取消并执行安全停止",
            }
            return dict(self._capture)

    def _require_controller(self) -> HardwareController:
        if self.controller is None or not self.serial.is_connected:
            raise DeviceNotConnectedError("STM32F407 尚未连接")

        return self.controller

    @staticmethod
    def _not_ready_capture_status() -> dict[str, Any]:
        return {
            "status": "not_ready",
            "progress": 0,
            "message": "相机服务尚未接入",
        }

    @staticmethod
    def _empty_status() -> dict[str, Any]:
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
        }
