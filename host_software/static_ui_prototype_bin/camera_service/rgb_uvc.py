from __future__ import annotations

import math
from typing import Any

from .base import CameraDeviceInfo, CameraFrame, CameraStatus
from .config import RgbCameraConfig
from .errors import (
    CameraCaptureError,
    CameraOpenError,
    CameraSettingUnsupported,
    CameraUnavailableError,
)


class RgbUvcCamera:
    """RGB USB camera adapter using OpenCV DirectShow on Windows.

    Frames returned by capture_frame() are RGB uint8 arrays with shape H x W x 3.
    OpenCV reads BGR internally; the adapter converts BGR -> RGB before returning.
    """

    role = "rgb"
    transport = "UVC/DirectShow"

    def __init__(
        self,
        device_index: int | None = None,
        *,
        config: RgbCameraConfig | dict[str, Any] | None = None,
        cv2_module: Any | None = None,
        capture_factory: Any | None = None,
        max_probe_index: int = 4,
    ) -> None:
        if isinstance(config, dict):
            rgb_config = RgbCameraConfig.from_dict(config)
        else:
            rgb_config = config or RgbCameraConfig.from_env()
        if device_index is not None:
            rgb_config = RgbCameraConfig.from_dict({**rgb_config.to_dict(), "deviceIndex": int(device_index)})
        self.config = rgb_config
        self.device_index = int(rgb_config.device_index)
        self.max_probe_index = int(max_probe_index if max_probe_index != 4 else rgb_config.max_probe_index)
        self._cv2 = cv2_module
        self._capture_factory = capture_factory
        self._capture = None
        self._streaming = False
        self._last_error = ""
        self._last_technical_error = ""
        self._actual: dict[str, Any] = {}
        self._capabilities: dict[str, Any] = {}
        self._setting_results: dict[str, Any] = {}

    def list_devices(self) -> list[CameraDeviceInfo]:
        devices: list[CameraDeviceInfo] = []
        for index in range(max(0, self.max_probe_index)):
            capture = self._make_capture(index)
            try:
                if capture is not None and capture.isOpened():
                    devices.append(CameraDeviceInfo(
                        role=self.role,
                        device_index=index,
                        device_name=f"OpenCV DirectShow camera {index}",
                        stable_id=f"opencv-dshow:{index}",
                        backend="opencv-dshow",
                        transport="UVC/DirectShow",
                    ))
            finally:
                if capture is not None:
                    capture.release()
        return devices

    def open(self) -> None:
        if self.is_open:
            return
        capture = self._make_capture(self.device_index)
        if capture is None or not capture.isOpened():
            self._last_error = "RGB 相机未连接或当前被其他程序占用。请关闭 AMCAP、Windows 相机等程序后重试。"
            self._last_technical_error = f"OpenCV CAP_DSHOW open failed; device_index={self.device_index}"
            if capture is not None:
                capture.release()
            raise CameraOpenError(self._last_error, self._last_technical_error)
        self._capture = capture
        self._setting_results = self._apply_requested_stream_config()
        self._read_actual_stream_config()
        self._probe_capabilities()
        self._last_error = ""
        self._last_technical_error = ""

    def close(self) -> None:
        self._streaming = False
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    @property
    def is_open(self) -> bool:
        return bool(self._capture is not None and self._capture.isOpened())

    def get_status(self) -> CameraStatus:
        status = CameraStatus(
            role=self.role,
            available=False,
            connected=self.is_open,
            streaming=self._streaming and self.is_open,
            sdk_available=self._cv2_available(),
            backend="opencv-dshow",
            transport="UVC/DirectShow",
            device_index=self.device_index,
            device_name=f"OpenCV DirectShow camera {self.device_index}",
            stable_id=f"opencv-dshow:{self.device_index}",
            color_space="RGB",
            frame_dtype="uint8",
            requested=self.config.to_dict(),
            actual=dict(self._actual),
            capabilities={
                **dict(self._capabilities),
                "lastApply": dict(self._setting_results),
            },
            error=self._last_error,
            technical_error=self._last_technical_error,
        )
        if self.is_open:
            status.available = True
            status.resolution = self.get_resolution()
            status.exposure = self._read_property("CAP_PROP_EXPOSURE")
            status.gain = self._read_property("CAP_PROP_GAIN")
            status.actual = self._read_actual_stream_config()
            status.capabilities = {
                **self._probe_capabilities(),
                "lastApply": dict(self._setting_results),
            }
            return status
        if self._cv2_available():
            status.error = "RGB 相机未连接"
            status.technical_error = "Camera is not open; run device check to probe OpenCV DirectShow"
        else:
            status.error = "OpenCV 未安装，RGB 相机不可用"
            status.technical_error = "cv2 module not found"
        return status

    def start_stream(self) -> None:
        self.open()
        self._streaming = True

    def stop_stream(self) -> None:
        self._streaming = False

    def capture_frame(self) -> CameraFrame:
        self.open()
        ok, frame = self._capture.read()
        if not ok or frame is None:
            self._last_error = "RGB 相机取帧失败"
            self._last_technical_error = "VideoCapture.read returned false"
            raise CameraCaptureError(self._last_error, self._last_technical_error)
        cv2 = self._require_cv2()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return CameraFrame(
            data=rgb,
            color_space="RGB",
            dtype=str(rgb.dtype),
            shape=tuple(int(value) for value in rgb.shape),
            metadata={"sourceColorSpace": "BGR", "deviceIndex": self.device_index},
        )

    def set_resolution(self, width: int, height: int) -> None:
        self.open()
        self._set_property("CAP_PROP_FRAME_WIDTH", float(width), "分辨率宽度")
        self._set_property("CAP_PROP_FRAME_HEIGHT", float(height), "分辨率高度")

    def get_resolution(self) -> tuple[int, int] | None:
        if not self.is_open:
            return None
        width = self._read_property("CAP_PROP_FRAME_WIDTH")
        height = self._read_property("CAP_PROP_FRAME_HEIGHT")
        if width is None or height is None:
            return None
        return int(round(width)), int(round(height))

    def set_exposure(self, value: float) -> None:
        self.open()
        self._set_property("CAP_PROP_EXPOSURE", float(value), "曝光")

    def get_exposure(self) -> float | None:
        if not self.is_open:
            return None
        return self._read_property("CAP_PROP_EXPOSURE")

    def set_gain(self, value: float) -> None:
        self.open()
        self._set_property("CAP_PROP_GAIN", float(value), "增益")

    def get_gain(self) -> float | None:
        if not self.is_open:
            return None
        return self._read_property("CAP_PROP_GAIN")

    def configure(self, config: RgbCameraConfig | dict[str, Any]) -> None:
        rgb_config = RgbCameraConfig.from_dict(config) if isinstance(config, dict) else config
        self.config = rgb_config
        self.device_index = int(rgb_config.device_index)
        self.max_probe_index = int(rgb_config.max_probe_index)

    def apply_config(self, config: RgbCameraConfig | dict[str, Any], *, restart: bool = False) -> dict[str, Any]:
        if restart and self.is_open:
            self.close()
        self.configure(config)
        if not self.is_open:
            self.open()
        else:
            self._setting_results = self._apply_requested_stream_config()
            self._read_actual_stream_config()
            self._probe_capabilities()
        return {
            "status": self.get_status().to_dict(),
            "settingResults": dict(self._setting_results),
        }

    def probe_available(self) -> bool:
        try:
            self.open()
            frame = self.capture_frame()
            return len(frame.shape) == 3 and frame.shape[2] == 3
        except Exception as exc:
            self._last_error = "RGB 相机未连接"
            self._last_technical_error = str(exc)
            return False
        finally:
            self.close()

    def _apply_requested_stream_config(self) -> dict[str, Any]:
        results = {
            "fourcc": self._setting_result(
                "CAP_PROP_FOURCC",
                self._fourcc_to_float(self.config.fourcc),
                self.config.fourcc,
                "视频格式",
            ),
            "width": self._setting_result("CAP_PROP_FRAME_WIDTH", float(self.config.width), self.config.width, "分辨率宽度"),
            "height": self._setting_result("CAP_PROP_FRAME_HEIGHT", float(self.config.height), self.config.height, "分辨率高度"),
            "fps": self._setting_result("CAP_PROP_FPS", float(self.config.fps), self.config.fps, "帧率"),
        }
        optional_settings = [
            ("autoExposure", "CAP_PROP_AUTO_EXPOSURE", self.config.auto_exposure, "自动曝光"),
            ("exposure", "CAP_PROP_EXPOSURE", self.config.exposure, "曝光"),
            ("gain", "CAP_PROP_GAIN", self.config.gain, "增益"),
            ("autoWhiteBalance", "CAP_PROP_AUTO_WB", self.config.auto_white_balance, "自动白平衡"),
            ("whiteBalance", "CAP_PROP_WB_TEMPERATURE", self.config.white_balance, "白平衡"),
        ]
        for key, prop_name, value, label in optional_settings:
            if value is not None:
                results[key] = self._setting_result(prop_name, float(value), value, label)
            else:
                results[key] = {"requested": None, "accepted": None, "label": label, "skipped": True}
        return results

    def _setting_result(self, prop_name: str, cv_value: float, requested: Any, label: str) -> dict[str, Any]:
        prop = self._property_id_or_none(prop_name)
        if prop is None or not self.is_open:
            return {"requested": requested, "accepted": False, "label": label, "supported": False}
        accepted = bool(self._capture.set(prop, cv_value))
        return {"requested": requested, "accepted": accepted, "label": label, "supported": True}

    def _read_actual_stream_config(self) -> dict[str, Any]:
        actual = {
            "width": _round_int(self._read_property("CAP_PROP_FRAME_WIDTH")),
            "height": _round_int(self._read_property("CAP_PROP_FRAME_HEIGHT")),
            "fps": self._read_property("CAP_PROP_FPS"),
            "fourcc": self._read_fourcc(),
            "exposure": self._read_property("CAP_PROP_EXPOSURE"),
            "gain": self._read_property("CAP_PROP_GAIN"),
            "whiteBalance": self._read_property("CAP_PROP_WB_TEMPERATURE"),
            "autoExposure": self._read_property("CAP_PROP_AUTO_EXPOSURE"),
            "autoWhiteBalance": self._read_property("CAP_PROP_AUTO_WB"),
        }
        actual["matchesRequested"] = self._matches_requested(actual)
        self._actual = actual
        return dict(actual)

    def _matches_requested(self, actual: dict[str, Any]) -> bool:
        fps = actual.get("fps")
        return (
            actual.get("width") == self.config.width
            and actual.get("height") == self.config.height
            and isinstance(fps, (int, float))
            and abs(float(fps) - float(self.config.fps)) < 0.5
            and str(actual.get("fourcc") or "").upper() == self.config.fourcc.upper()
        )

    def _probe_capabilities(self) -> dict[str, Any]:
        capabilities = {
            "exposure": self._probe_property_capability("CAP_PROP_EXPOSURE", "曝光"),
            "gain": self._probe_property_capability("CAP_PROP_GAIN", "增益"),
            "whiteBalance": self._probe_property_capability("CAP_PROP_WB_TEMPERATURE", "白平衡"),
            "autoExposure": self._probe_property_capability("CAP_PROP_AUTO_EXPOSURE", "自动曝光"),
            "autoWhiteBalance": self._probe_property_capability("CAP_PROP_AUTO_WB", "自动白平衡"),
        }
        self._capabilities = capabilities
        return dict(capabilities)

    def _probe_property_capability(self, prop_name: str, label: str) -> dict[str, Any]:
        prop = self._property_id_or_none(prop_name)
        if prop is None or not self.is_open:
            return {"supported": False, "settable": False, "current": None, "label": label}
        current = self._capture.get(prop)
        current = _float_or_none(current)
        if current is None:
            return {"supported": False, "settable": False, "current": None, "label": label}
        settable = bool(self._capture.set(prop, current))
        return {"supported": True, "settable": settable, "current": current, "label": label}

    def _make_capture(self, index: int):
        cv2 = self._require_cv2()
        if self._capture_factory is not None:
            return self._capture_factory(index)
        return cv2.VideoCapture(index, cv2.CAP_DSHOW)

    def _cv2_available(self) -> bool:
        try:
            self._require_cv2()
            return True
        except CameraUnavailableError:
            return False

    def _require_cv2(self):
        if self._cv2 is None:
            try:
                import cv2  # type: ignore
            except Exception as exc:
                raise CameraUnavailableError("OpenCV 未安装，RGB 相机不可用", str(exc)) from exc
            self._cv2 = cv2
        return self._cv2

    def _property_id(self, name: str) -> int:
        cv2 = self._require_cv2()
        if not hasattr(cv2, name):
            raise CameraSettingUnsupported(f"RGB 相机不支持{name}", f"cv2.{name} not found")
        return int(getattr(cv2, name))

    def _property_id_or_none(self, name: str) -> int | None:
        try:
            return self._property_id(name)
        except CameraSettingUnsupported:
            return None

    def _set_property(self, name: str, value: float, label: str) -> None:
        prop = self._property_id(name)
        ok = bool(self._capture.set(prop, value))
        if not ok:
            raise CameraSettingUnsupported(f"RGB 相机不支持设置{label}", f"VideoCapture.set({name}) returned false")

    def _try_set_property(self, name: str, value: float, label: str) -> bool:
        prop = self._property_id_or_none(name)
        if prop is None or not self.is_open:
            return False
        return bool(self._capture.set(prop, value))

    def _read_property(self, name: str) -> float | None:
        if not self.is_open:
            return None
        prop = self._property_id_or_none(name)
        if prop is None:
            return None
        return _float_or_none(self._capture.get(prop))

    def _read_fourcc(self) -> str:
        value = self._read_property("CAP_PROP_FOURCC")
        if value is None:
            return ""
        raw = int(value)
        chars = [chr((raw >> (8 * index)) & 0xFF) for index in range(4)]
        return "".join(chars).strip("\x00")

    def _fourcc_to_float(self, value: str) -> float:
        cv2 = self._require_cv2()
        fourcc = (value or "MJPG").upper()[:4]
        if hasattr(cv2, "VideoWriter_fourcc"):
            return float(cv2.VideoWriter_fourcc(*fourcc))
        return float(sum(ord(fourcc[index]) << (8 * index) for index in range(len(fourcc))))


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _round_int(value: float | None) -> int | None:
    if value is None:
        return None
    return int(round(value))
