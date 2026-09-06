from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Stm32ProtocolError(RuntimeError):
    """Base error for the STM32 current-firmware protocol layer."""


class Stm32CrcError(Stm32ProtocolError):
    """A complete frame was received with a bad CRC."""


class Stm32FrameKind(str, Enum):
    ACK = "ack"
    STATUS = "status"
    INFO = "info"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Stm32Frame:
    cmd: int
    payload: bytes
    raw: bytes
    kind: Stm32FrameKind = Stm32FrameKind.UNKNOWN
    timestamp_monotonic: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cmd": self.cmd,
            "cmdHex": f"0x{self.cmd:02X}",
            "kind": self.kind.value,
            "payloadHex": self.payload.hex(" ").upper(),
            "rawHex": self.raw.hex(" ").upper(),
            "timestampMonotonic": self.timestamp_monotonic,
        }


@dataclass(frozen=True)
class Stm32Status:
    motor_state: str = "unknown"
    position_deg: float | None = None
    velocity_rpm: float | None = None
    target_deg: float | None = None
    fan_on: bool | None = None
    led_mask: int | None = None
    error_code: int | None = None
    raw_payload: bytes = b""
    raw_frame: bytes = b""
    updated_monotonic: float = field(default_factory=time.monotonic)

    def logical_slot(self, *, slots_per_rev: int = 16) -> int | None:
        if self.position_deg is None:
            return None
        slot_angle = 360.0 / float(slots_per_rev)
        return int(round((self.position_deg % 360.0) / slot_angle)) % slots_per_rev

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["rawPayloadHex"] = self.raw_payload.hex(" ").upper()
        data["rawFrameHex"] = self.raw_frame.hex(" ").upper()
        data.pop("raw_payload", None)
        data.pop("raw_frame", None)
        return data


@dataclass(frozen=True)
class Stm32ProtocolProfile:
    """
    Numeric command ids and payload layout for one STM32 firmware revision.

    The current worktree does not contain the STM32 source files requested by
    P1B-7.5A, so production code must inject a profile derived from that source
    instead of relying on guessed command numbers.
    """

    ack_cmd: int
    status_cmd: int
    info_cmd: int
    query_status_cmd: int
    move_abs_cmd: int
    move_rel_cmd: int
    stop_cmd: int
    set_profile_cmd: int
    set_origin_cmd: int
    fan_set_cmd: int | None = None
    led_set_cmd: int | None = None
    door_cmd: int | None = None
    status_layout: str = "opaque"
    crc_includes_header: bool = True

    def frame_kind(self, cmd: int) -> Stm32FrameKind:
        if cmd == self.ack_cmd:
            return Stm32FrameKind.ACK
        if cmd == self.status_cmd:
            return Stm32FrameKind.STATUS
        if cmd == self.info_cmd:
            return Stm32FrameKind.INFO
        return Stm32FrameKind.UNKNOWN


@dataclass(frozen=True)
class FilterWheelMapping:
    slots_per_rev: int = 16
    pulses_per_rev: int = 1600
    move_payload_units: str = "pulses_i32_le"

    @property
    def degrees_per_slot(self) -> float:
        return 360.0 / float(self.slots_per_rev)

    @property
    def pulses_per_slot(self) -> int:
        return int(round(self.pulses_per_rev / self.slots_per_rev))

    def relative_payload(self, slot_delta: int) -> bytes:
        if self.move_payload_units == "slots_i16_le":
            return int(slot_delta).to_bytes(2, "little", signed=True)
        if self.move_payload_units == "degrees_centi_i32_le":
            value = int(round(slot_delta * self.degrees_per_slot * 100.0))
            return value.to_bytes(4, "little", signed=True)
        if self.move_payload_units == "pulses_i32_le":
            return int(slot_delta * self.pulses_per_slot).to_bytes(4, "little", signed=True)
        raise ValueError(f"unsupported filter-wheel move units: {self.move_payload_units}")

    def absolute_payload(self, slot: int) -> bytes:
        if not 0 <= int(slot) < self.slots_per_rev:
            raise ValueError("filter-wheel slot out of range")
        if self.move_payload_units == "slots_i16_le":
            return int(slot).to_bytes(2, "little", signed=True)
        if self.move_payload_units == "degrees_centi_i32_le":
            value = int(round(slot * self.degrees_per_slot * 100.0))
            return value.to_bytes(4, "little", signed=True)
        if self.move_payload_units == "pulses_i32_le":
            return int(slot * self.pulses_per_slot).to_bytes(4, "little", signed=True)
        raise ValueError(f"unsupported filter-wheel move units: {self.move_payload_units}")


def crc16_ccitt_false(data: bytes | bytearray | memoryview) -> int:
    crc = 0xFFFF
    for byte in bytes(data):
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


class Aa55Codec:
    HEADER = b"\xAA\x55"
    MAX_PAYLOAD = 255
    MIN_FRAME_SIZE = 6

    def __init__(self, profile: Stm32ProtocolProfile) -> None:
        self.profile = profile

    def encode(self, cmd: int, payload: bytes | bytearray | memoryview = b"") -> bytes:
        _validate_byte("cmd", cmd)
        body_payload = bytes(payload)
        if len(body_payload) > self.MAX_PAYLOAD:
            raise ValueError("payload must fit in one-byte PLEN")
        body = bytes((cmd, len(body_payload))) + body_payload
        crc_input = self.HEADER + body if self.profile.crc_includes_header else body
        crc = crc16_ccitt_false(crc_input)
        return self.HEADER + body + crc.to_bytes(2, "big")

    def decode(self, frame: bytes | bytearray | memoryview) -> Stm32Frame:
        raw = bytes(frame)
        if len(raw) < self.MIN_FRAME_SIZE or not raw.startswith(self.HEADER):
            raise Stm32ProtocolError("invalid AA55 frame")
        payload_len = raw[3]
        expected_len = 2 + 1 + 1 + payload_len + 2
        if len(raw) != expected_len:
            raise Stm32ProtocolError("AA55 frame length mismatch")
        received_crc = int.from_bytes(raw[-2:], "big")
        crc_input = raw[:-2] if self.profile.crc_includes_header else raw[2:-2]
        actual_crc = crc16_ccitt_false(crc_input)
        if received_crc != actual_crc:
            raise Stm32CrcError(
                f"CRC mismatch: expected 0x{received_crc:04X}, calculated 0x{actual_crc:04X}"
            )
        cmd = raw[2]
        return Stm32Frame(
            cmd=cmd,
            payload=raw[4:-2],
            raw=raw,
            kind=self.profile.frame_kind(cmd),
        )


class Aa55StreamParser:
    def __init__(self, codec: Aa55Codec) -> None:
        self.codec = codec
        self._buffer = bytearray()
        self.noise_bytes_dropped = 0
        self.crc_errors = 0

    def feed(self, data: bytes | bytearray | memoryview) -> list[Stm32Frame]:
        self._buffer.extend(bytes(data))
        frames: list[Stm32Frame] = []

        while True:
            header_index = self._buffer.find(Aa55Codec.HEADER)
            if header_index < 0:
                self.noise_bytes_dropped += len(self._buffer)
                self._buffer.clear()
                break
            if header_index > 0:
                self.noise_bytes_dropped += header_index
                del self._buffer[:header_index]
            if len(self._buffer) < Aa55Codec.MIN_FRAME_SIZE:
                break

            payload_len = self._buffer[3]
            frame_len = 2 + 1 + 1 + payload_len + 2
            if len(self._buffer) < frame_len:
                break

            raw = bytes(self._buffer[:frame_len])
            del self._buffer[:frame_len]
            try:
                frames.append(self.codec.decode(raw))
            except Stm32CrcError:
                self.crc_errors += 1
                continue

        return frames

    def buffered_size(self) -> int:
        return len(self._buffer)


def decode_status_payload(payload: bytes, *, layout: str, raw_frame: bytes = b"") -> Stm32Status:
    """
    Decode STATUS payloads when a firmware-specific layout is known.

    ``opaque`` deliberately avoids guessing current STM32 payload fields.
    ``basic_v1`` is a compact test/adapter layout:
    state,u32/i32 centideg position,i16 centirpm velocity,i32 centideg target,
    fan byte, led mask byte, error byte.
    """

    raw = bytes(payload)
    if layout == "opaque":
        return Stm32Status(raw_payload=raw, raw_frame=bytes(raw_frame))
    if layout != "basic_v1":
        raise Stm32ProtocolError(f"unsupported STATUS layout: {layout}")
    if len(raw) < 14:
        raise Stm32ProtocolError("basic_v1 STATUS payload too short")
    state_map = {
        0: "idle",
        1: "moving",
        2: "done",
        3: "busy",
        4: "error",
    }
    motor_state = state_map.get(raw[0], f"unknown_{raw[0]:02X}")
    position_cdeg = int.from_bytes(raw[1:5], "little", signed=True)
    velocity_crpm = int.from_bytes(raw[5:7], "little", signed=True)
    target_cdeg = int.from_bytes(raw[7:11], "little", signed=True)
    fan_on = bool(raw[11])
    led_mask = raw[12]
    error_code = raw[13]
    return Stm32Status(
        motor_state=motor_state,
        position_deg=position_cdeg / 100.0,
        velocity_rpm=velocity_crpm / 100.0,
        target_deg=target_cdeg / 100.0,
        fan_on=fan_on,
        led_mask=led_mask,
        error_code=error_code,
        raw_payload=raw,
        raw_frame=bytes(raw_frame),
    )


def ack_echo_cmd(frame: Stm32Frame) -> int | None:
    if frame.kind != Stm32FrameKind.ACK or not frame.payload:
        return None
    return frame.payload[0]


def ack_result_code(frame: Stm32Frame) -> int:
    if frame.kind != Stm32FrameKind.ACK or len(frame.payload) < 2:
        return 0x00
    return frame.payload[1]


def _validate_byte(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be in byte range")
