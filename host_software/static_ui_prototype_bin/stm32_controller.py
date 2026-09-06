from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from stm32_protocol import (
    Aa55Codec,
    Aa55StreamParser,
    FilterWheelMapping,
    Stm32Frame,
    Stm32FrameKind,
    Stm32ProtocolError,
    Stm32ProtocolProfile,
    Stm32Status,
    ack_echo_cmd,
    ack_result_code,
    decode_status_payload,
)


class RawSerialTransport(Protocol):
    @property
    def is_connected(self) -> bool: ...
    def write_bytes(self, data: bytes | bytearray | memoryview) -> int: ...
    def read_bytes(self, size: int, timeout_s: float | None = None) -> bytes: ...
    def clear_buffers(self) -> None: ...


class Stm32AdapterError(RuntimeError):
    """Base error raised by the STM32 current-firmware adapter."""


class Stm32CapabilityUnavailable(Stm32AdapterError):
    """The current firmware/hardware does not provide this capability."""


class Stm32AckTimeout(Stm32AdapterError):
    """No matching ACK was received before timeout."""


class Stm32CommandRejected(Stm32AdapterError):
    def __init__(self, cmd: int, result_code: int) -> None:
        self.cmd = cmd
        self.result_code = result_code
        super().__init__(f"STM32 command 0x{cmd:02X} rejected with 0x{result_code:02X}")


@dataclass(frozen=True)
class Stm32CommandResult:
    cmd: int
    ack_received: bool
    result_code: int = 0
    status_after: Stm32Status | None = None
    completed_from_status: bool = False
    attempts: int = 1

    def ok(self) -> bool:
        return self.result_code == 0 and (self.ack_received or self.completed_from_status)


@dataclass
class Stm32SafetyReport:
    actions: list[dict[str, Any]] = field(default_factory=list)

    def add(self, action: str, *, ok: bool, error: str = "") -> None:
        self.actions.append({"action": action, "ok": bool(ok), "error": error})

    def to_dict(self) -> dict[str, Any]:
        return {"actions": list(self.actions), "ok": all(item["ok"] for item in self.actions)}


class Stm32ControllerAdapter:
    """
    Adapter from existing host semantic commands to the STM32 current firmware.

    It owns AA55 framing, ACK correlation, STATUS caching, command serialization,
    and motion retry policy. The existing CaptureCoordinator and
    HardwareController continue to use semantic wheel/fan/light/door operations.
    """

    LEGACY_PING = 0x01
    LEGACY_FAN_SET = 0x10
    LEGACY_DOOR_SET = 0x11
    LEGACY_RGB_LED_SET = 0x12
    LEGACY_TUNGSTEN_SET = 0x13
    LEGACY_WHEEL_MOVE_RELATIVE = 0x20
    LEGACY_WHEEL_HOME = 0x21
    LEGACY_OUTPUT_STATUS_GET = 0x30
    LEGACY_DOOR_STATUS_GET = 0x31
    LEGACY_WHEEL_POSITION_GET = 0x32
    LEGACY_LAST_ERROR_GET = 0x33
    LEGACY_SAFE_STOP = 0x3E
    LEGACY_FAULT_CLEAR = 0x3F

    ERROR_CAPABILITY_UNAVAILABLE = 0x81
    ERROR_UNVERIFIED = 0x82

    def __init__(
        self,
        transport: RawSerialTransport,
        *,
        profile: Stm32ProtocolProfile,
        filter_wheel: FilterWheelMapping | None = None,
        ack_timeout_s: float = 0.5,
        motion_timeout_s: float = 8.0,
        read_chunk_size: int = 64,
        allow_short_frame_fan: bool = True,
        allow_short_frame_led: bool = True,
    ) -> None:
        if ack_timeout_s <= 0 or motion_timeout_s <= 0:
            raise ValueError("timeouts must be positive")
        self.transport = transport
        self.profile = profile
        self.filter_wheel = filter_wheel or FilterWheelMapping()
        self.ack_timeout_s = float(ack_timeout_s)
        self.motion_timeout_s = float(motion_timeout_s)
        self.read_chunk_size = int(read_chunk_size)
        self.allow_short_frame_fan = bool(allow_short_frame_fan)
        self.allow_short_frame_led = bool(allow_short_frame_led)

        self.codec = Aa55Codec(profile)
        self.parser = Aa55StreamParser(self.codec)
        self._command_lock = threading.RLock()
        self._status_lock = threading.RLock()
        self._status: Stm32Status | None = None
        self._info_frames: list[Stm32Frame] = []
        self._last_safety_report = Stm32SafetyReport()
        self.tungsten_supported = False
        self.door_endpoint_feedback_supported = False
        self.physical_encoder_verified = False
        self.automatic_homing_supported = False

    @property
    def is_connected(self) -> bool:
        return bool(getattr(self.transport, "is_connected", False))

    @property
    def status_cache(self) -> Stm32Status | None:
        with self._status_lock:
            return self._status

    @property
    def last_safety_report(self) -> dict[str, Any]:
        return self._last_safety_report.to_dict()

    def ping(self, timeout_s: float = 0.5) -> bool:
        result = self.query_status(timeout_s=timeout_s)
        return result is not None

    def send_command(self, cmd: int, param: int, timeout_s: float | None = None) -> int:
        """Compatibility shim for the existing HardwareController command ids."""

        if cmd == self.LEGACY_PING:
            return 0x5A if self.ping(timeout_s=timeout_s or self.ack_timeout_s) else 0x00
        if cmd == self.LEGACY_FAN_SET:
            self.set_fan(bool(param), timeout_s=timeout_s)
            return 0x00
        if cmd == self.LEGACY_RGB_LED_SET:
            self.set_led_mask(param, timeout_s=timeout_s)
            return 0x00
        if cmd == self.LEGACY_TUNGSTEN_SET:
            if int(param) == 0:
                return 0x00
            return self.ERROR_CAPABILITY_UNAVAILABLE
        if cmd == self.LEGACY_DOOR_SET:
            self.door_command(param, timeout_s=timeout_s)
            return 0x00
        if cmd == self.LEGACY_WHEEL_MOVE_RELATIVE:
            delta = int(param)
            if delta >= 0x80:
                delta -= 0x100
            self.move_filter_wheel_relative_slots(delta, timeout_s=timeout_s)
            return 0x00
        if cmd == self.LEGACY_WHEEL_HOME:
            return self.ERROR_CAPABILITY_UNAVAILABLE
        if cmd == self.LEGACY_OUTPUT_STATUS_GET:
            status = self.query_status(timeout_s=timeout_s or self.ack_timeout_s)
            return self._legacy_output_status(status)
        if cmd == self.LEGACY_DOOR_STATUS_GET:
            return 0x00
        if cmd == self.LEGACY_WHEEL_POSITION_GET:
            status = self.query_status(timeout_s=timeout_s or self.ack_timeout_s)
            slot = status.logical_slot(slots_per_rev=self.filter_wheel.slots_per_rev)
            return 0x7F if slot is None else slot
        if cmd == self.LEGACY_LAST_ERROR_GET:
            status = self.query_status(timeout_s=timeout_s or self.ack_timeout_s)
            return int(status.error_code or 0)
        if cmd == self.LEGACY_SAFE_STOP:
            self.safe_stop(timeout_s=timeout_s or self.ack_timeout_s)
            return 0x00
        if cmd == self.LEGACY_FAULT_CLEAR:
            return 0x00
        raise Stm32CapabilityUnavailable(f"legacy command 0x{cmd:02X} is not mapped")

    def query_status(self, *, timeout_s: float | None = None) -> Stm32Status:
        with self._command_lock:
            self._send_frame(self.profile.query_status_cmd, b"")
            deadline = time.monotonic() + float(timeout_s or self.ack_timeout_s)
            while time.monotonic() < deadline:
                for frame in self._read_frames_once(deadline):
                    self._handle_async_frame(frame)
                    if frame.kind == Stm32FrameKind.STATUS:
                        status = self._decode_status_frame(frame)
                        self._update_status(status)
                        return status
        cached = self.status_cache
        if cached is not None:
            return cached
        raise Stm32AckTimeout("no STATUS received")

    def set_fan(self, enabled: bool, *, timeout_s: float | None = None) -> Stm32CommandResult:
        if self.profile.fan_set_cmd is None:
            if not self.allow_short_frame_fan:
                raise Stm32CapabilityUnavailable("fan command is unavailable in this profile")
            self._send_short_frame(0x10, 0x01 if enabled else 0x00)
            return Stm32CommandResult(cmd=0x10, ack_received=True)
        return self._send_and_wait_ack(
            self.profile.fan_set_cmd,
            bytes((0x01 if enabled else 0x00,)),
            timeout_s=timeout_s,
        )

    def set_led_mask(self, mask: int, *, timeout_s: float | None = None) -> Stm32CommandResult:
        if not 0 <= int(mask) <= 0x07:
            raise ValueError("LED mask must be 0x00..0x07")
        if self.profile.led_set_cmd is None:
            if not self.allow_short_frame_led:
                raise Stm32CapabilityUnavailable("LED command is unavailable in this profile")
            self._send_short_frame(0x12, int(mask))
            return Stm32CommandResult(cmd=0x12, ack_received=True)
        return self._send_and_wait_ack(
            self.profile.led_set_cmd,
            bytes((int(mask),)),
            timeout_s=timeout_s,
        )

    def door_command(self, action: int, *, timeout_s: float | None = None) -> Stm32CommandResult:
        if self.profile.door_cmd is None:
            raise Stm32CapabilityUnavailable("door command is unavailable in this profile")
        if int(action) not in (0x00, 0x01, 0x02):
            raise ValueError("door action must be open/close/stop")
        return self._send_and_wait_ack(
            self.profile.door_cmd,
            bytes((int(action),)),
            timeout_s=timeout_s,
        )

    def set_origin(self, *, timeout_s: float | None = None) -> Stm32CommandResult:
        return self._send_and_wait_ack(
            self.profile.set_origin_cmd,
            b"",
            timeout_s=timeout_s,
            mechanical=True,
        )

    def move_filter_wheel_relative_slots(
        self,
        slot_delta: int,
        *,
        timeout_s: float | None = None,
        max_retries: int = 1,
    ) -> Stm32CommandResult:
        if slot_delta == 0:
            raise ValueError("slot_delta must not be zero")
        payload = self.filter_wheel.relative_payload(slot_delta)
        return self._send_motion_with_status_aware_retry(
            self.profile.move_rel_cmd,
            payload,
            timeout_s=timeout_s or self.motion_timeout_s,
            max_retries=max_retries,
        )

    def move_filter_wheel_absolute_slot(
        self,
        slot: int,
        *,
        timeout_s: float | None = None,
    ) -> Stm32CommandResult:
        payload = self.filter_wheel.absolute_payload(slot)
        return self._send_and_wait_ack(
            self.profile.move_abs_cmd,
            payload,
            timeout_s=timeout_s or self.motion_timeout_s,
            mechanical=True,
        )

    def stop_filter_wheel(self, *, timeout_s: float | None = None) -> Stm32CommandResult:
        return self._send_and_wait_ack(self.profile.stop_cmd, b"", timeout_s=timeout_s)

    def safe_stop(self, *, timeout_s: float | None = None) -> dict[str, Any]:
        report = Stm32SafetyReport()
        actions = (
            ("filter_wheel_stop", lambda: self.stop_filter_wheel(timeout_s=timeout_s)),
            ("door_stop", lambda: self.door_command(0x02, timeout_s=timeout_s)),
            ("led_off", lambda: self.set_led_mask(0x00, timeout_s=timeout_s)),
            ("fan_on", lambda: self.set_fan(True, timeout_s=timeout_s)),
        )
        for action, callback in actions:
            try:
                callback()
                report.add(action, ok=True)
            except Exception as exc:
                report.add(action, ok=False, error=str(exc))
        self._last_safety_report = report
        return report.to_dict()

    def listen_once(self, *, timeout_s: float = 0.1) -> list[Stm32Frame]:
        deadline = time.monotonic() + timeout_s
        frames = self._read_frames_once(deadline)
        for frame in frames:
            self._handle_async_frame(frame)
        return frames

    def _send_motion_with_status_aware_retry(
        self,
        cmd: int,
        payload: bytes,
        *,
        timeout_s: float,
        max_retries: int,
    ) -> Stm32CommandResult:
        attempts = 0
        start_status = self.status_cache
        last_position = start_status.position_deg if start_status else None

        with self._command_lock:
            while True:
                attempts += 1
                try:
                    result = self._send_and_wait_ack(
                        cmd,
                        payload,
                        timeout_s=timeout_s,
                        mechanical=True,
                        attempts=attempts,
                    )
                    self._wait_motion_complete(timeout_s=timeout_s)
                    return result
                except Stm32AckTimeout:
                    status = self.status_cache
                    if status is not None and status.motor_state == "moving":
                        final_status = self._wait_motion_complete(timeout_s=timeout_s)
                        return Stm32CommandResult(
                            cmd=cmd,
                            ack_received=False,
                            completed_from_status=True,
                            status_after=final_status,
                            attempts=attempts,
                        )
                    if (
                        status is not None
                        and status.motor_state in {"idle", "done"}
                        and status.position_deg is not None
                        and (
                            status.motor_state == "done"
                            or (last_position is not None and status.position_deg != last_position)
                        )
                    ):
                        return Stm32CommandResult(
                            cmd=cmd,
                            ack_received=False,
                            completed_from_status=True,
                            status_after=status,
                            attempts=attempts,
                        )
                    unchanged = (
                        status is not None
                        and status.motor_state in {"idle", "done"}
                        and (last_position is None or status.position_deg == last_position)
                    )
                    if unchanged and attempts <= max_retries:
                        continue
                    raise

    def _send_and_wait_ack(
        self,
        cmd: int,
        payload: bytes,
        *,
        timeout_s: float | None = None,
        mechanical: bool = False,
        attempts: int = 1,
    ) -> Stm32CommandResult:
        del mechanical
        with self._command_lock:
            self._send_frame(cmd, payload)
            deadline = time.monotonic() + float(timeout_s or self.ack_timeout_s)
            while time.monotonic() < deadline:
                frames = self._read_frames_once(deadline)
                for frame in frames:
                    self._handle_async_frame(frame)
                    if frame.kind != Stm32FrameKind.ACK:
                        continue
                    if ack_echo_cmd(frame) != cmd:
                        continue
                    result_code = ack_result_code(frame)
                    if result_code != 0:
                        raise Stm32CommandRejected(cmd, result_code)
                    return Stm32CommandResult(
                        cmd=cmd,
                        ack_received=True,
                        result_code=result_code,
                        status_after=self.status_cache,
                        attempts=attempts,
                    )
            raise Stm32AckTimeout(f"no ACK for command 0x{cmd:02X}")

    def _wait_motion_complete(self, *, timeout_s: float) -> Stm32Status | None:
        deadline = time.monotonic() + timeout_s
        last = self.status_cache
        while time.monotonic() < deadline:
            frames = self._read_frames_once(deadline)
            for frame in frames:
                self._handle_async_frame(frame)
                if frame.kind == Stm32FrameKind.STATUS:
                    last = self.status_cache
                    if last and last.motor_state in {"idle", "done", "error"}:
                        return last
        return last

    def _send_frame(self, cmd: int, payload: bytes) -> None:
        frame = self.codec.encode(cmd, payload)
        written = self.transport.write_bytes(frame)
        if written != len(frame):
            raise Stm32AdapterError("serial write was incomplete")

    def _send_short_frame(self, cmd: int, param: int) -> None:
        packet = bytes((cmd, param))
        written = self.transport.write_bytes(packet)
        if written != 2:
            raise Stm32AdapterError("short-frame serial write was incomplete")

    def _read_frames_once(self, deadline: float) -> list[Stm32Frame]:
        remaining = max(0.001, min(0.05, deadline - time.monotonic()))
        chunk = self.transport.read_bytes(self.read_chunk_size, timeout_s=remaining)
        if not chunk:
            return []
        return self.parser.feed(chunk)

    def _handle_async_frame(self, frame: Stm32Frame) -> None:
        if frame.kind == Stm32FrameKind.STATUS:
            self._update_status(self._decode_status_frame(frame))
        elif frame.kind == Stm32FrameKind.INFO:
            self._info_frames.append(frame)

    def _decode_status_frame(self, frame: Stm32Frame) -> Stm32Status:
        return decode_status_payload(
            frame.payload,
            layout=self.profile.status_layout,
            raw_frame=frame.raw,
        )

    def _update_status(self, status: Stm32Status) -> None:
        with self._status_lock:
            self._status = status

    @staticmethod
    def _legacy_output_status(status: Stm32Status) -> int:
        raw = 0
        if status.fan_on:
            raw |= 1 << 0
        led_mask = int(status.led_mask or 0)
        if led_mask & 0x01:
            raw |= 1 << 1
        if led_mask & 0x02:
            raw |= 1 << 2
        return raw
