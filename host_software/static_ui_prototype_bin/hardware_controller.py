from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol


TUNGSTEN_1_BIT = 0x01
TUNGSTEN_2_BIT = 0x02
LED3_BIT = 0x04
TUNGSTEN_MASK = TUNGSTEN_1_BIT | TUNGSTEN_2_BIT
ALL_LIGHT_MASK = TUNGSTEN_MASK | LED3_BIT
RGB_CAPTURE_LIGHT_MASK: int | None = None
ALLOW_DUAL_TUNGSTEN = False
TUNGSTEN_CLOSE_FAILURE_MESSAGE = "无法确认钨灯已关闭，请立即切断12V光源电源。"


class SerialTransport(Protocol):
    def send_command(self, cmd: int, param: int, timeout_s: float | None = None) -> int: ...
    def ping(self, timeout_s: float = 0.5) -> bool: ...


class HardwareControllerError(RuntimeError):
    pass


class InterlockError(HardwareControllerError):
    pass


class CommandFailedError(HardwareControllerError):
    def __init__(self, cmd: int, result_code: int) -> None:
        self.cmd = cmd
        self.result_code = result_code
        super().__init__(f"命令 0x{cmd:02X} 执行失败，错误码 0x{result_code:02X}")


class CapabilityUnavailableError(HardwareControllerError):
    pass


class DoorState(IntEnum):
    UNKNOWN = 0x00
    OPEN = 0x01
    CLOSED = 0x02
    MOVING = 0x03
    ERROR = 0x04


@dataclass(frozen=True)
class OutputStatus:
    raw: int
    fan_on: bool
    rgb_led_1_on: bool
    rgb_led_2_on: bool
    tungsten_1_on: bool
    tungsten_2_on: bool
    rgb_led_3_on: bool = False

    @property
    def any_rgb_led_on(self) -> bool:
        return self.rgb_led_3_on

    @property
    def any_tungsten_on(self) -> bool:
        return self.tungsten_1_on or self.tungsten_2_on


class HardwareController:
    FAN_SET = 0x10
    DOOR_SET = 0x11
    RGB_LED_SET = 0x12
    TUNGSTEN_SET = RGB_LED_SET
    WHEEL_MOVE_RELATIVE = 0x20
    WHEEL_HOME = 0x21
    OUTPUT_STATUS_GET = 0x30
    DOOR_STATUS_GET = 0x31
    WHEEL_POSITION_GET = 0x32
    LAST_ERROR_GET = 0x33
    SAFE_STOP = 0x3E
    FAULT_CLEAR = 0x3F

    def __init__(self, serial_service: SerialTransport, *, door_timeout_s: float = 10.0) -> None:
        if door_timeout_s <= 0:
            raise ValueError("door_timeout_s 必须大于 0")
        self.serial = serial_service
        self.door_timeout_s = float(door_timeout_s)
        self._safe_stopped = False
        self._wheel_moving = False
        self._lock = threading.RLock()

    def ping(self) -> bool:
        return self.serial.ping(timeout_s=0.5)

    def fan_on(self) -> None:
        self._send_control(self.FAN_SET, 0x01, 0.5)

    def fan_off(self) -> None:
        if self.get_output_status().any_tungsten_on:
            raise InterlockError("钨灯开启时禁止关闭风扇")
        self._send_control(self.FAN_SET, 0x00, 0.5)

    def door_raise(self) -> None:
        self._ensure_operational()
        outputs = self.get_output_status()
        if outputs.any_rgb_led_on or outputs.any_tungsten_on:
            raise InterlockError("所有光源关闭后才能升起门")
        self._send_control(self.DOOR_SET, 0x00, self.door_timeout_s)

    def door_close(self) -> None:
        self._ensure_operational()
        self._send_control(self.DOOR_SET, 0x01, self.door_timeout_s)

    def door_stop(self) -> None:
        self._send_control(self.DOOR_SET, 0x02, 0.5)

    def rgb_led_set(self, mask: int) -> None:
        self._validate_mask(mask, max_mask=ALL_LIGHT_MASK, label="RGB LED mask")
        if mask & TUNGSTEN_MASK:
            raise CapabilityUnavailableError("PB7/PB8 已确认为钨灯 SSR，不能作为 RGB LED 使用")
        if mask:
            self._ensure_operational()
            self._require_closed_door()
            if self.get_output_status().any_tungsten_on:
                raise InterlockError("钨灯开启时禁止开启 RGB LED")
        self._send_control(self.RGB_LED_SET, mask, 0.5)

    def tungsten_set(self, mask: int) -> None:
        self._validate_mask(mask, max_mask=TUNGSTEN_MASK, label="tungsten mask")
        if mask == TUNGSTEN_MASK and not ALLOW_DUAL_TUNGSTEN:
            raise InterlockError("DUAL_TUNGSTEN_NOT_ACCEPTED: 两路钨灯同时开启尚未通过电源/散热/防护验收")
        if mask:
            self._ensure_operational()
            self._require_closed_door()
            outputs = self.get_output_status()
            if not outputs.fan_on:
                raise InterlockError("风扇未开启，禁止开启钨灯")
            if outputs.any_rgb_led_on:
                raise InterlockError("RGB LED 开启时禁止开启钨灯")
        self._send_control(self.TUNGSTEN_SET, mask, 0.5)

    def all_lights_off(self) -> None:
        self._send_control(self.RGB_LED_SET, 0x00, 0.5, stop_on_error=False)

    def wheel_home(self) -> None:
        self._ensure_operational()
        with self._lock:
            self._wheel_moving = True
            try:
                self._send_control(self.WHEEL_HOME, 0x00, 10.0)
            finally:
                self._wheel_moving = False

    def wheel_move_relative(self, steps: int) -> None:
        if isinstance(steps, bool) or not isinstance(steps, int):
            raise TypeError("steps 必须是整数")
        if not -128 <= steps <= 127 or steps == 0:
            raise ValueError("steps 必须在 -128～127 范围内且不能为 0")
        self._ensure_operational()
        timeout_s = 2.0 if abs(steps) == 1 else 5.0
        with self._lock:
            self._wheel_moving = True
            try:
                self._send_control(self.WHEEL_MOVE_RELATIVE, steps & 0xFF, timeout_s)
            finally:
                self._wheel_moving = False

    def get_output_status(self) -> OutputStatus:
        if hasattr(self.serial, "query_status"):
            status = self.serial.query_status(require_fresh=True, timeout_s=0.5)
            led_mask = int(status.led_mask or 0) & ALL_LIGHT_MASK
            return OutputStatus(
                raw=led_mask,
                fan_on=bool(status.fan_on),
                rgb_led_1_on=False,
                rgb_led_2_on=False,
                rgb_led_3_on=bool(status.led3_duty or (led_mask & LED3_BIT)),
                tungsten_1_on=bool(status.led1_duty or (led_mask & TUNGSTEN_1_BIT)),
                tungsten_2_on=bool(status.led2_duty or (led_mask & TUNGSTEN_2_BIT)),
            )
        raw = self._query(self.OUTPUT_STATUS_GET)
        return OutputStatus(
            raw=raw,
            fan_on=bool(raw & (1 << 0)),
            rgb_led_1_on=False,
            rgb_led_2_on=False,
            rgb_led_3_on=bool(raw & LED3_BIT),
            tungsten_1_on=bool(raw & TUNGSTEN_1_BIT),
            tungsten_2_on=bool(raw & TUNGSTEN_2_BIT),
        )

    def get_door_status(self) -> DoorState:
        value = self._query(self.DOOR_STATUS_GET)
        try:
            return DoorState(value)
        except ValueError as exc:
            raise HardwareControllerError(f"未知门状态 0x{value:02X}") from exc

    def get_wheel_status(self) -> int:
        position = self._query(self.WHEEL_POSITION_GET)
        if position == 0x7F or 0x00 <= position <= 0x0F:
            return position
        raise HardwareControllerError(f"未知滤光轮位置 0x{position:02X}")

    def get_error_status(self) -> int:
        return self._query(self.LAST_ERROR_GET)

    def ensure_rgb_capture_ready(self) -> None:
        self._ensure_operational()
        self._require_closed_door()
        outputs = self.get_output_status()
        if not outputs.fan_on:
            raise InterlockError("风扇未开启")
        if not outputs.any_rgb_led_on:
            raise InterlockError("RGB LED 尚未开启")
        if outputs.any_tungsten_on:
            raise InterlockError("钨灯未关闭")
        if self._wheel_moving:
            raise InterlockError("滤光轮仍在运动")

    def ensure_multispectral_capture_ready(self) -> None:
        self._ensure_operational()
        self._require_closed_door()
        outputs = self.get_output_status()
        if not outputs.fan_on:
            raise InterlockError("风扇未开启")
        if outputs.any_rgb_led_on:
            raise InterlockError("RGB LED 未关闭")
        if not outputs.any_tungsten_on:
            raise InterlockError("钨灯尚未开启")
        if self._wheel_moving:
            raise InterlockError("滤光轮仍在运动")

    def safe_stop(self) -> None:
        with self._lock:
            self._safe_stopped = True
            try:
                self.all_lights_off()
            except Exception:
                pass
            self._send_control(self.SAFE_STOP, 0x00, 0.5, stop_on_error=False)

    def fault_clear(self) -> None:
        with self._lock:
            self._send_control(self.FAULT_CLEAR, 0x00, 0.5, stop_on_error=False)
            self._safe_stopped = False

    def _ensure_operational(self) -> None:
        if self._safe_stopped:
            raise InterlockError("系统已安全停止，请排除故障后执行 fault_clear()")
        error_code = self.get_error_status()
        if error_code != 0x00:
            raise InterlockError(f"STM32 当前故障码为 0x{error_code:02X}")

    def _require_closed_door(self) -> None:
        if self.get_door_status() != DoorState.CLOSED:
            raise InterlockError("升降门未关闭到位，禁止开启光源或采集")

    def _query(self, cmd: int) -> int:
        value = self.serial.send_command(cmd, 0x00, timeout_s=0.5)
        if value >= 0x80:
            raise CommandFailedError(cmd, value & 0x7F)
        return value

    def _send_control(
        self,
        cmd: int,
        param: int,
        timeout_s: float,
        *,
        stop_on_error: bool = True,
    ) -> None:
        try:
            result = self.serial.send_command(cmd, param, timeout_s=timeout_s)
            if result != 0x00:
                raise CommandFailedError(cmd, result)
        except Exception:
            if stop_on_error and cmd != self.SAFE_STOP:
                self._best_effort_safe_stop()
            raise

    def _best_effort_safe_stop(self) -> None:
        self._safe_stopped = True
        try:
            self.serial.send_command(self.SAFE_STOP, 0x00, timeout_s=0.5)
        except Exception:
            pass

    @staticmethod
    def _validate_mask(mask: int, *, max_mask: int, label: str) -> None:
        if isinstance(mask, bool) or not isinstance(mask, int):
            raise TypeError(f"{label} 必须是整数")
        if not 0x00 <= mask <= max_mask:
            raise ValueError(f"{label} 必须在 0x00..0x{max_mask:02X} 范围内")
