from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RgbCameraConfig:
    """Requested RGB UVC camera settings.

    device_index=1 is the current development PC's verified DirectShow index,
    not a stable cross-machine camera identity.
    """

    device_index: int = 1
    width: int = 3840
    height: int = 2160
    fps: float = 25.0
    fourcc: str = "MJPG"
    exposure: float | None = None
    gain: float | None = None
    white_balance: float | None = None
    auto_exposure: float | None = None
    auto_white_balance: float | None = None
    max_probe_index: int = 4

    def to_dict(self) -> dict[str, Any]:
        return {
            "deviceIndex": self.device_index,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "fourcc": self.fourcc,
            "exposure": self.exposure,
            "gain": self.gain,
            "whiteBalance": self.white_balance,
            "autoExposure": self.auto_exposure,
            "autoWhiteBalance": self.auto_white_balance,
            "maxProbeIndex": self.max_probe_index,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "RgbCameraConfig":
        payload = payload or {}
        return cls(
            device_index=int(payload.get("deviceIndex", payload.get("device_index", cls.device_index))),
            width=int(payload.get("width", cls.width)),
            height=int(payload.get("height", cls.height)),
            fps=float(payload.get("fps", cls.fps)),
            fourcc=str(payload.get("fourcc", cls.fourcc) or cls.fourcc).upper()[:4],
            exposure=_optional_float(payload.get("exposure")),
            gain=_optional_float(payload.get("gain")),
            white_balance=_optional_float(payload.get("whiteBalance", payload.get("white_balance"))),
            auto_exposure=_optional_float(payload.get("autoExposure", payload.get("auto_exposure"))),
            auto_white_balance=_optional_float(payload.get("autoWhiteBalance", payload.get("auto_white_balance"))),
            max_probe_index=int(payload.get("maxProbeIndex", payload.get("max_probe_index", cls.max_probe_index))),
        )

    @classmethod
    def from_env(cls) -> "RgbCameraConfig":
        payload = {
            "deviceIndex": os.environ.get("FRUIT_RGB_CAMERA_INDEX"),
            "width": os.environ.get("FRUIT_RGB_CAMERA_WIDTH"),
            "height": os.environ.get("FRUIT_RGB_CAMERA_HEIGHT"),
            "fps": os.environ.get("FRUIT_RGB_CAMERA_FPS"),
            "fourcc": os.environ.get("FRUIT_RGB_CAMERA_FOURCC"),
            "exposure": os.environ.get("FRUIT_RGB_CAMERA_EXPOSURE"),
            "gain": os.environ.get("FRUIT_RGB_CAMERA_GAIN"),
            "whiteBalance": os.environ.get("FRUIT_RGB_CAMERA_WHITE_BALANCE"),
            "autoExposure": os.environ.get("FRUIT_RGB_CAMERA_AUTO_EXPOSURE"),
            "autoWhiteBalance": os.environ.get("FRUIT_RGB_CAMERA_AUTO_WHITE_BALANCE"),
            "maxProbeIndex": os.environ.get("FRUIT_RGB_CAMERA_MAX_PROBE_INDEX"),
        }
        return cls.from_dict({key: value for key, value in payload.items() if value not in (None, "")})


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, str) and value.strip().lower() in {"auto", "default", "自动"}:
        return None
    return float(value)
