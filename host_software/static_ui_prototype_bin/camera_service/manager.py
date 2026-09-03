from __future__ import annotations

import io
import threading
from typing import Any

from .base import CameraStatus
from .config import RgbCameraConfig
from .dvp2_mono import Dvp2MonoCamera
from .errors import CameraError
from .rgb_uvc import RgbUvcCamera


class CameraManager:
    """Owns camera adapters and reports their status without coordinating capture."""

    def __init__(
        self,
        rgb_camera: Any | None = None,
        multispectral_camera: Any | None = None,
        rgb_config: RgbCameraConfig | dict[str, Any] | None = None,
    ) -> None:
        self.rgb = rgb_camera or RgbUvcCamera(config=rgb_config)
        self.multispectral = multispectral_camera or Dvp2MonoCamera()
        self._lock = threading.RLock()
        self._rgb_preview = {
            "running": False,
            "width": 960,
            "height": 540,
            "fps": 12,
            "format": "image/jpeg",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "rgb": self._status_dict(self.rgb),
                "multispectral": self._status_dict(self.multispectral),
                "preview": {"rgb": dict(self._rgb_preview)},
            }

    def checks(self, *, probe_rgb: bool = False) -> dict[str, dict[str, Any]]:
        with self._lock:
            rgb_status = self._status_dict(self.rgb)
            if probe_rgb and hasattr(self.rgb, "probe_available"):
                rgb_status = self._probe_rgb_locked()["status"]
            multispectral_status = self._status_dict(self.multispectral)
            return {
                "rgbCamera": self._rgb_check(rgb_status),
                "multispectralCamera": self._multispectral_check(multispectral_status),
            }

    def probe_rgb(self) -> dict[str, Any]:
        with self._lock:
            result = self._probe_rgb_locked()
            return {
                "passed": bool(result["status"].get("available")),
                "status": result["status"],
                "preview": {"rgb": dict(self._rgb_preview)},
            }

    def apply_rgb_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = RgbCameraConfig.from_dict(payload)
        with self._lock:
            current = getattr(self.rgb, "config", RgbCameraConfig.from_env())
            restart_required = self._rgb_restart_required(current, config)
            preview_was_running = bool(self._rgb_preview.get("running"))
            result = self.rgb.apply_config(config, restart=restart_required)
            if preview_was_running:
                self.rgb.start_stream()
                self._rgb_preview["running"] = True
            status = result["status"]
            return {
                "restartRequired": restart_required,
                "previewRestarted": preview_was_running and restart_required,
                "settingResults": result.get("settingResults") or {},
                "status": status,
                "summary": self._requested_actual_summary(status),
                "preview": {"rgb": dict(self._rgb_preview)},
            }

    def start_rgb_preview(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        with self._lock:
            width = int(payload.get("width") or self._rgb_preview["width"])
            height = int(payload.get("height") or self._rgb_preview["height"])
            fps = float(payload.get("fps") or self._rgb_preview["fps"])
            print(
                f"[camera.rgb] preview start requested: width={width}; height={height}; fps={fps}",
                flush=True,
            )
            self.rgb.start_stream()
            self._rgb_preview.update({
                "running": True,
                "width": max(160, min(width, 1920)),
                "height": max(90, min(height, 1080)),
                "fps": max(1, min(fps, 15)),
                "format": "image/jpeg",
            })
            status = self._status_dict(self.rgb)
            return {
                "status": status,
                "preview": {"rgb": dict(self._rgb_preview)},
            }

    def stop_rgb_preview(self) -> dict[str, Any]:
        with self._lock:
            self._rgb_preview["running"] = False
            self.rgb.stop_stream()
            self.rgb.close()
            print("[camera.rgb] preview stopped", flush=True)
            return {
                "status": self._status_dict(self.rgb),
                "preview": {"rgb": dict(self._rgb_preview)},
            }

    def rgb_preview_jpeg(self) -> tuple[bytes, dict[str, Any]]:
        with self._lock:
            if not self._rgb_preview.get("running"):
                raise CameraError("RGB 预览未启动", "Call /api/camera/rgb/preview/start first")
            try:
                frame = self.rgb.capture_frame()
                from PIL import Image

                image = Image.fromarray(frame.data)
                target = (int(self._rgb_preview["width"]), int(self._rgb_preview["height"]))
                if image.size != target:
                    resampling = getattr(getattr(Image, "Resampling", Image), "BILINEAR")
                    image = image.resize(target, resampling)
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=82, optimize=True)
                return buffer.getvalue(), {
                    "sourceShape": frame.shape,
                    "previewWidth": target[0],
                    "previewHeight": target[1],
                    "fps": self._rgb_preview["fps"],
                    "contentType": "image/jpeg",
                }
            except CameraError:
                self._rgb_preview["running"] = False
                self.rgb.stop_stream()
                self.rgb.close()
                raise

    def _probe_rgb_locked(self) -> dict[str, Any]:
        if self._rgb_preview.get("running") and getattr(self.rgb, "is_open", False):
            try:
                self.rgb.capture_frame()
            except CameraError as exc:
                rgb_status = self._status_dict(self.rgb)
                rgb_status.update({
                    "detected": False,
                    "available": False,
                    "connected": False,
                    "opened": False,
                    "streaming": False,
                    "error": exc.user_message,
                    "technicalError": exc.technical_message,
                })
                self._rgb_preview["running"] = False
                self.rgb.stop_stream()
                self.rgb.close()
                return {"status": rgb_status}
            rgb_status = self._status_dict(self.rgb)
            rgb_status.update({
                "detected": True,
                "available": True,
                "connected": True,
                "opened": True,
                "streaming": True,
            })
            return {"status": rgb_status}
        rgb_ok = bool(self.rgb.probe_available())
        rgb_status = self._status_dict(self.rgb)
        rgb_status.update({
            "detected": rgb_ok,
            "available": rgb_ok,
            "connected": bool(rgb_ok or rgb_status.get("connected")),
            "opened": bool(rgb_status.get("opened")),
        })
        return {"status": rgb_status}

    @staticmethod
    def _rgb_restart_required(current: RgbCameraConfig, requested: RgbCameraConfig) -> bool:
        return any((
            current.device_index != requested.device_index,
            current.width != requested.width,
            current.height != requested.height,
            abs(float(current.fps) - float(requested.fps)) >= 0.01,
            current.fourcc.upper() != requested.fourcc.upper(),
        ))

    def _status_dict(self, adapter: Any) -> dict[str, Any]:
        try:
            status = adapter.get_status()
            if isinstance(status, CameraStatus):
                return status.to_dict()
            return dict(status)
        except CameraError as exc:
            return {
                "role": getattr(adapter, "role", ""),
                "available": False,
                "connected": False,
                "streaming": False,
                "transport": getattr(adapter, "transport", ""),
                "error": exc.user_message,
                "technicalError": exc.technical_message,
            }
        except Exception as exc:
            return {
                "role": getattr(adapter, "role", ""),
                "available": False,
                "connected": False,
                "streaming": False,
                "transport": getattr(adapter, "transport", ""),
                "error": "相机状态读取失败",
                "technicalError": str(exc),
            }

    def _rgb_check(self, status: dict[str, Any]) -> dict[str, Any]:
        connected = bool(status.get("connected") or status.get("available"))
        return {
            "status": "passed" if connected else "not_connected",
            "label": "RGB 相机",
            "message": (
                self._resolution_message(status)
                if connected
                else status.get("error") or "RGB 相机未连接"
            ),
            "cameraStatus": status,
        }

    def _multispectral_check(self, status: dict[str, Any]) -> dict[str, Any]:
        if not status.get("sdkAvailable"):
            check_status = "sdk_missing"
            message = status.get("error") or "多光谱 GigE 相机 DVP2 SDK 尚未安装"
        elif status.get("connected") or status.get("available"):
            check_status = "passed"
            message = self._resolution_message(status)
        else:
            check_status = "warning"
            message = status.get("error") or "DVP2 SDK 已发现，设备/API 待实机确认"
        return {
            "status": check_status,
            "label": "多光谱相机",
            "message": message,
            "cameraStatus": status,
        }

    @staticmethod
    def _resolution_message(status: dict[str, Any]) -> str:
        resolution = status.get("resolution") or {}
        actual = status.get("actual") or {}
        if actual.get("width") and actual.get("height"):
            resolution = {"width": actual.get("width"), "height": actual.get("height")}
        width = resolution.get("width")
        height = resolution.get("height")
        fps = actual.get("fps")
        fourcc = actual.get("fourcc")
        if width and height:
            suffix = " ".join(str(value) for value in (f"{fps:g}fps" if isinstance(fps, (int, float)) else "", fourcc or "") if value)
            return f"已连接 {width}x{height}" + (f" @ {suffix}" if suffix else "")
        return "已连接"

    @staticmethod
    def _requested_actual_summary(status: dict[str, Any]) -> dict[str, Any]:
        requested = status.get("requested") or {}
        actual = status.get("actual") or {}
        return {
            "requestedResolution": (
                f"{requested.get('width')}x{requested.get('height')}"
                if requested.get("width") and requested.get("height")
                else ""
            ),
            "actualResolution": (
                f"{actual.get('width')}x{actual.get('height')}"
                if actual.get("width") and actual.get("height")
                else ""
            ),
            "requestedFps": requested.get("fps"),
            "actualFps": actual.get("fps"),
            "requestedFourcc": requested.get("fourcc"),
            "actualFourcc": actual.get("fourcc"),
            "requestedExposure": requested.get("exposure"),
            "actualExposure": actual.get("exposure"),
            "requestedGain": requested.get("gain"),
            "actualGain": actual.get("gain"),
            "requestedWhiteBalance": requested.get("whiteBalance"),
            "actualWhiteBalance": actual.get("whiteBalance"),
            "matchesRequested": bool(actual.get("matchesRequested")),
        }
