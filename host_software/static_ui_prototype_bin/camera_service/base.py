from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class CameraDeviceInfo:
    role: str
    device_index: int | None = None
    device_name: str = ""
    stable_id: str = ""
    backend: str = ""
    transport: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "deviceIndex": self.device_index,
            "deviceName": self.device_name,
            "stableId": self.stable_id,
            "backend": self.backend,
            "transport": self.transport,
        }


@dataclass(frozen=True)
class CameraFrame:
    data: Any
    color_space: str
    dtype: str
    shape: tuple[int, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CameraStatus:
    role: str
    available: bool = False
    connected: bool = False
    streaming: bool = False
    sdk_available: bool | None = None
    backend: str = ""
    transport: str = ""
    device_index: int | None = None
    device_name: str = ""
    stable_id: str = ""
    resolution: tuple[int, int] | None = None
    exposure: float | None = None
    gain: float | None = None
    requested: dict[str, Any] = field(default_factory=dict)
    actual: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    pixel_format: str = ""
    color_space: str = ""
    frame_dtype: str = ""
    error: str = ""
    technical_error: str = ""
    sdk_path: str = ""
    dll_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "available": self.available,
            "connected": self.connected,
            "streaming": self.streaming,
            "sdkAvailable": self.sdk_available,
            "backend": self.backend,
            "transport": self.transport,
            "deviceIndex": self.device_index,
            "deviceName": self.device_name,
            "stableId": self.stable_id,
            "resolution": (
                {"width": self.resolution[0], "height": self.resolution[1]}
                if self.resolution
                else None
            ),
            "exposure": self.exposure,
            "gain": self.gain,
            "requested": self.requested,
            "actual": self.actual,
            "capabilities": self.capabilities,
            "pixelFormat": self.pixel_format,
            "colorSpace": self.color_space,
            "frameDtype": self.frame_dtype,
            "error": self.error,
            "technicalError": self.technical_error,
            "sdkPath": self.sdk_path,
            "dllPath": self.dll_path,
        }


class CameraAdapter(Protocol):
    role: str

    def list_devices(self) -> list[CameraDeviceInfo]:
        ...

    def open(self) -> None:
        ...

    def close(self) -> None:
        ...

    @property
    def is_open(self) -> bool:
        ...

    def get_status(self) -> CameraStatus:
        ...

    def start_stream(self) -> None:
        ...

    def stop_stream(self) -> None:
        ...

    def capture_frame(self) -> CameraFrame:
        ...

    def set_exposure(self, value: float) -> None:
        ...

    def get_exposure(self) -> float | None:
        ...

    def set_gain(self, value: float) -> None:
        ...

    def get_gain(self) -> float | None:
        ...

    def get_resolution(self) -> tuple[int, int] | None:
        ...
