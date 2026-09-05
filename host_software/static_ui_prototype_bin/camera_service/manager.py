from __future__ import annotations

import io
import threading
import time
from typing import Any

from .base import CameraFrame, CameraStatus
from .config import RgbCameraConfig
from .dvp2_mono import Dvp2MonoCamera
from .errors import CameraError
from .focus_quality import FocusEvaluator
from .rgb_uvc import RgbUvcCamera


class CameraManager:
    """Owns camera adapters and reports their status without coordinating capture."""

    def __init__(
        self,
        rgb_camera: Any | None = None,
        multispectral_camera: Any | None = None,
        rgb_config: RgbCameraConfig | dict[str, Any] | None = None,
        focus_evaluator: FocusEvaluator | None = None,
    ) -> None:
        self.rgb = rgb_camera or RgbUvcCamera(config=rgb_config)
        self.multispectral = multispectral_camera or Dvp2MonoCamera()
        self.focus_evaluator = focus_evaluator or FocusEvaluator()
        self._lock = threading.RLock()
        self._rgb_preview = {
            "running": False,
            "width": 960,
            "height": 540,
            "fps": 12,
            "format": "image/jpeg",
        }
        self._multispectral_preview = {
            "running": False,
            "width": 960,
            "height": 540,
            "fps": 12,
            "format": "image/jpeg",
            "lowLatency": True,
            "diagnostics": {},
        }
        self._multispectral_capture_lock = threading.RLock()
        self._multispectral_latest_lock = threading.Lock()
        self._multispectral_latest_frame: CameraFrame | None = None
        self._multispectral_latest_diagnostics: dict[str, Any] = {}
        self._multispectral_preview_error: CameraError | None = None
        self._multispectral_preview_stop_event = threading.Event()
        self._multispectral_preview_ready_event = threading.Event()
        self._multispectral_preview_thread: threading.Thread | None = None
        self._multispectral_preview_served_count = 0
        self._multispectral_preview_served_started_at: float | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "rgb": self._status_dict(self.rgb),
                "multispectral": self._status_dict(self.multispectral),
                "preview": {
                    "rgb": dict(self._rgb_preview),
                    "multispectral": dict(self._multispectral_preview),
                },
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
                "preview": self._preview_status(),
            }

    def probe_multispectral(self) -> dict[str, Any]:
        with self._lock:
            if self._multispectral_preview.get("running") and getattr(self.multispectral, "is_open", False):
                try:
                    with self._multispectral_capture_lock:
                        self.multispectral.capture_frame()
                except CameraError as exc:
                    status = self._status_dict(self.multispectral)
                    status.update({
                        "detected": False,
                        "available": False,
                        "connected": False,
                        "opened": False,
                        "streaming": False,
                        "error": exc.user_message,
                        "technicalError": exc.technical_message,
                    })
                    self._multispectral_preview["running"] = False
                    self._multispectral_preview_stop_event.set()
                    with self._multispectral_capture_lock:
                        self.multispectral.stop_stream()
                        self.multispectral.close()
                    return {"passed": False, "status": status, "preview": self._preview_status()}
                status = self._status_dict(self.multispectral)
                return {"passed": True, "status": status, "preview": self._preview_status()}
            ok = bool(self.multispectral.probe_available()) if hasattr(self.multispectral, "probe_available") else False
            status = self._status_dict(self.multispectral)
            status.update({
                "detected": bool(ok or status.get("detected")),
                "available": bool(ok),
                "connected": bool(status.get("detected")),
            })
            return {"passed": bool(ok), "status": status, "preview": self._preview_status()}

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
                "preview": self._preview_status(),
            }

    def apply_multispectral_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        setting_results: dict[str, Any] = {}
        with self._lock:
            with self._multispectral_capture_lock:
                if "exposure" in payload and payload.get("exposure") not in (None, ""):
                    requested = float(payload.get("exposure"))
                    actual = self.multispectral.set_exposure(requested)
                    setting_results["exposure"] = {
                        "requested": requested,
                        "actual": actual,
                        "accepted": True,
                    }
                if "gain" in payload and payload.get("gain") not in (None, ""):
                    requested = float(payload.get("gain"))
                    actual = self.multispectral.set_gain(requested)
                    setting_results["gain"] = {
                        "requested": requested,
                        "actual": actual,
                        "accepted": True,
                    }
            status = self._status_dict(self.multispectral)
            return {
                "settingResults": setting_results,
                "status": status,
                "summary": self._multispectral_requested_actual_summary(status, setting_results),
                "preview": self._preview_status(),
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
                "preview": self._preview_status(),
            }

    def stop_rgb_preview(self) -> dict[str, Any]:
        with self._lock:
            self._rgb_preview["running"] = False
            self.rgb.stop_stream()
            self.rgb.close()
            print("[camera.rgb] preview stopped", flush=True)
            return {
                "status": self._status_dict(self.rgb),
                "preview": self._preview_status(),
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

    def capture_rgb_frame(self) -> tuple[CameraFrame, dict[str, Any]]:
        """Capture one production RGB frame through the owned RGB adapter."""

        with self._lock:
            was_open = bool(getattr(self.rgb, "is_open", False))
            preview_was_running = bool(self._rgb_preview.get("running") and was_open)
            frame = self.rgb.capture_frame()
            status = self._status_dict(self.rgb)
            metadata = {
                "status": status,
                "preview": self._preview_status(),
                "previewWasRunning": preview_was_running,
                "openedForCapture": not was_open,
                "requestedSettings": dict(status.get("requested") or {}),
                "actualSettings": dict(status.get("actual") or {}),
                "device": self._camera_device_metadata(status, frame.metadata),
            }
            if not was_open:
                self.rgb.stop_stream()
                self.rgb.close()
            return frame, metadata

    def capture_multispectral_frame(self) -> tuple[CameraFrame, dict[str, Any]]:
        """Capture one production DVP2 mono frame through the owned adapter."""

        with self._lock:
            was_open = bool(getattr(self.multispectral, "is_open", False))
            preview_was_running = bool(self._multispectral_preview.get("running") and was_open)
            try:
                with self._multispectral_capture_lock:
                    frame = self.multispectral.capture_frame()
                status = self._status_dict(self.multispectral)
                metadata = {
                    "status": status,
                    "preview": self._preview_status(),
                    "previewWasRunning": preview_was_running,
                    "openedForCapture": not was_open,
                    "requestedSettings": dict(status.get("requested") or {}),
                    "actualSettings": dict(status.get("actual") or {}),
                    "pixelFormat": frame.metadata.get("pixelFormat") or status.get("pixelFormat") or "",
                    "dtype": frame.dtype,
                    "shape": tuple(int(value) for value in frame.shape),
                    "width": int(frame.shape[1]) if len(frame.shape) >= 2 else None,
                    "height": int(frame.shape[0]) if len(frame.shape) >= 2 else None,
                    "exposure": frame.metadata.get("exposure"),
                    "gain": frame.metadata.get("gain"),
                    "streaming": bool(status.get("streaming")),
                    "device": self._camera_device_metadata(status, frame.metadata),
                }
                return frame, metadata
            finally:
                if not was_open:
                    with self._multispectral_capture_lock:
                        self.multispectral.stop_stream()
                        self.multispectral.close()

    def evaluate_multispectral_focus(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Evaluate focus quality from a raw DVP2 mono frame without saving sample data."""

        payload = payload or {}
        with self._lock:
            frame, capture_meta = self.capture_multispectral_frame()
            result = self.focus_evaluator.evaluate(
                frame,
                roi=payload.get("roi") or payload.get("roiMode") or "center",
                band_id=payload.get("bandId"),
                wavelength_nm=payload.get("wavelengthNm"),
            ).to_dict()
            result["capture"] = {
                "previewWasRunning": bool(capture_meta.get("previewWasRunning")),
                "openedForCapture": bool(capture_meta.get("openedForCapture")),
                "streaming": bool(capture_meta.get("streaming")),
            }
            result["preview"] = self._preview_status()
            result["statusSnapshot"] = capture_meta.get("status") or self._status_dict(self.multispectral)
            return result

    def start_multispectral_preview(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        with self._lock:
            width = int(payload.get("width") or self._multispectral_preview["width"])
            height = int(payload.get("height") or self._multispectral_preview["height"])
            fps = float(payload.get("fps") or self._multispectral_preview["fps"])
            print(
                f"[camera.multispectral] preview start requested: width={width}; height={height}; fps={fps}",
                flush=True,
            )
            if self._multispectral_preview.get("running") and getattr(self.multispectral, "is_open", False):
                self._multispectral_preview.update({
                    "width": max(160, min(width, 1920)),
                    "height": max(90, min(height, 1080)),
                    "fps": max(1, min(fps, 15)),
                    "format": "image/jpeg",
                    "lowLatency": True,
                })
                return {
                    "status": self._status_dict(self.multispectral),
                    "preview": self._preview_status(),
                }
            if hasattr(self.multispectral, "probe_available") and not self.multispectral.probe_available():
                status = self._status_dict(self.multispectral)
                raise CameraError(
                    status.get("error") or "多光谱相机不可用",
                    status.get("technicalError") or "DVP2 probe failed before preview start",
                )
            with self._multispectral_capture_lock:
                self.multispectral.start_stream()
            self._reset_multispectral_preview_cache()
            self._multispectral_preview.update({
                "running": True,
                "width": max(160, min(width, 1920)),
                "height": max(90, min(height, 1080)),
                "fps": max(1, min(fps, 15)),
                "format": "image/jpeg",
                "lowLatency": True,
            })
            self._start_multispectral_preview_worker_locked()
            return {
                "status": self._status_dict(self.multispectral),
                "preview": self._preview_status(),
            }

    def stop_multispectral_preview(self) -> dict[str, Any]:
        thread: threading.Thread | None
        with self._lock:
            self._multispectral_preview["running"] = False
            self._multispectral_preview_stop_event.set()
            thread = self._multispectral_preview_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            with self._multispectral_capture_lock:
                self.multispectral.stop_stream()
                self.multispectral.close()
            self._multispectral_preview_thread = None
            self._reset_multispectral_preview_cache()
            print("[camera.multispectral] preview stopped", flush=True)
            return {
                "status": self._status_dict(self.multispectral),
                "preview": self._preview_status(),
            }

    def multispectral_preview_jpeg(self) -> tuple[bytes, dict[str, Any]]:
        server_started = time.perf_counter()
        with self._lock:
            if not self._multispectral_preview.get("running"):
                raise CameraError("多光谱预览未启动", "Call /api/camera/multispectral/preview/start first")
            target = (int(self._multispectral_preview["width"]), int(self._multispectral_preview["height"]))
            fps = self._multispectral_preview["fps"]

        frame, capture_diag = self._latest_multispectral_preview_frame()
        data, resize_duration_ms, jpeg_encode_duration_ms, encoder = self._encode_multispectral_preview_jpeg(frame, target)
        server_total_ms = (time.perf_counter() - server_started) * 1000.0
        measured_fps = self._record_multispectral_preview_served_fps()
        diagnostics = {
            **capture_diag,
            "resizeDurationMs": resize_duration_ms,
            "jpegEncodeDurationMs": jpeg_encode_duration_ms,
            "serverTotalMs": server_total_ms,
            "measuredPreviewFps": measured_fps,
            "lowLatency": True,
            "previewEncoder": encoder,
        }
        with self._lock:
            self._multispectral_preview["diagnostics"] = dict(diagnostics)
        return data, {
            "sourceShape": frame.shape,
            "sourceDtype": frame.dtype,
            "previewWidth": target[0],
            "previewHeight": target[1],
            "fps": fps,
            "contentType": "image/jpeg",
            "pixelFormat": frame.metadata.get("pixelFormat", ""),
            **self._frame_stats(frame.data),
            **diagnostics,
        }

    def _encode_multispectral_preview_jpeg(self, frame: CameraFrame, target: tuple[int, int]) -> tuple[bytes, float, float, str]:
        try:
            return self._encode_multispectral_preview_jpeg_cv2(frame.data, target)
        except Exception:
            return self._encode_multispectral_preview_jpeg_pil(frame.data, target)

    @staticmethod
    def _encode_multispectral_preview_jpeg_cv2(data: Any, target: tuple[int, int]) -> tuple[bytes, float, float, str]:
        import cv2
        import numpy as np

        resize_started = time.perf_counter()
        array = CameraManager._preview_uint8_array(data)
        if array.ndim == 3 and array.shape[2] >= 3:
            array = array[:, :, :3]
        if array.shape[1] != target[0] or array.shape[0] != target[1]:
            array = cv2.resize(array, target, interpolation=cv2.INTER_LINEAR)
        resize_duration_ms = (time.perf_counter() - resize_started) * 1000.0

        encode_started = time.perf_counter()
        ok, encoded = cv2.imencode(".jpg", array, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not ok:
            raise CameraError("多光谱预览 JPEG 编码失败", "cv2.imencode returned false")
        jpeg_encode_duration_ms = (time.perf_counter() - encode_started) * 1000.0
        return encoded.tobytes(), resize_duration_ms, jpeg_encode_duration_ms, "opencv"

    @staticmethod
    def _encode_multispectral_preview_jpeg_pil(data: Any, target: tuple[int, int]) -> tuple[bytes, float, float, str]:
        resize_started = time.perf_counter()
        image = CameraManager._preview_image_from_frame(data)
        if image.size != target:
            from PIL import Image

            resampling = getattr(getattr(Image, "Resampling", Image), "BILINEAR")
            image = image.resize(target, resampling)
        resize_duration_ms = (time.perf_counter() - resize_started) * 1000.0

        encode_started = time.perf_counter()
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=82, optimize=True)
        jpeg_encode_duration_ms = (time.perf_counter() - encode_started) * 1000.0
        return buffer.getvalue(), resize_duration_ms, jpeg_encode_duration_ms, "pil"

    def _start_multispectral_preview_worker_locked(self) -> None:
        if self._multispectral_preview_thread and self._multispectral_preview_thread.is_alive():
            return
        self._multispectral_preview_stop_event.clear()
        self._multispectral_preview_ready_event.clear()
        self._multispectral_preview_thread = threading.Thread(
            target=self._multispectral_preview_worker,
            name="dvp2-preview-latest-frame",
            daemon=True,
        )
        self._multispectral_preview_thread.start()

    def _multispectral_preview_worker(self) -> None:
        last_frame_id: int | None = None
        while not self._multispectral_preview_stop_event.is_set():
            with self._lock:
                target_fps = float(self._multispectral_preview.get("fps") or 12)
                running = bool(self._multispectral_preview.get("running"))
            if not running:
                break
            started = time.perf_counter()
            try:
                with self._multispectral_capture_lock:
                    frame = self.multispectral.capture_frame()
            except CameraError as exc:
                self._multispectral_preview_error = exc
                self._multispectral_preview_stop_event.set()
                break
            except Exception as exc:
                self._multispectral_preview_error = CameraError("多光谱预览取帧失败", str(exc))
                self._multispectral_preview_stop_event.set()
                break
            capture_duration_ms = (time.perf_counter() - started) * 1000.0
            frame_id = self._coerce_frame_id(frame.metadata.get("frameId"))
            dropped_frames = 0
            if frame_id is not None and last_frame_id is not None and frame_id > last_frame_id:
                dropped_frames = max(0, frame_id - last_frame_id - 1)
            if frame_id is not None:
                last_frame_id = frame_id
            with self._multispectral_latest_lock:
                self._multispectral_latest_frame = frame
                self._multispectral_latest_diagnostics = {
                    "frameId": frame_id,
                    "sourceTimestamp": frame.metadata.get("timestamp"),
                    "captureDurationMs": capture_duration_ms,
                    "droppedFrames": dropped_frames,
                    "acquiredAt": time.time(),
                }
                self._multispectral_preview_ready_event.set()
            elapsed = time.perf_counter() - started
            interval = 1.0 / max(1.0, min(target_fps, 60.0))
            self._multispectral_preview_stop_event.wait(timeout=max(0.0, interval - elapsed))

    def _latest_multispectral_preview_frame(self) -> tuple[CameraFrame, dict[str, Any]]:
        if not self._multispectral_preview_ready_event.wait(timeout=1.0):
            if self._multispectral_preview_error is not None:
                exc = self._multispectral_preview_error
                self._cleanup_failed_multispectral_preview()
                raise exc
            self._cleanup_failed_multispectral_preview()
            raise CameraError("多光谱预览尚未取得帧", "DVP2 latest-frame preview cache is empty")
        if self._multispectral_preview_error is not None:
            exc = self._multispectral_preview_error
            self._cleanup_failed_multispectral_preview()
            raise exc
        with self._multispectral_latest_lock:
            frame = self._multispectral_latest_frame
            diagnostics = dict(self._multispectral_latest_diagnostics)
        if frame is None:
            self._cleanup_failed_multispectral_preview()
            raise CameraError("多光谱预览尚未取得帧", "DVP2 latest-frame preview cache is empty")
        return frame, diagnostics

    def _cleanup_failed_multispectral_preview(self) -> None:
        with self._lock:
            self._multispectral_preview["running"] = False
            self._multispectral_preview_stop_event.set()
            with self._multispectral_capture_lock:
                self.multispectral.stop_stream()
                self.multispectral.close()
            self._multispectral_preview_thread = None
            self._reset_multispectral_preview_cache(clear_error=False)

    def _reset_multispectral_preview_cache(self, *, clear_error: bool = True) -> None:
        with self._multispectral_latest_lock:
            self._multispectral_latest_frame = None
            self._multispectral_latest_diagnostics = {}
        self._multispectral_preview_ready_event.clear()
        if clear_error:
            self._multispectral_preview_error = None
        self._multispectral_preview_served_count = 0
        self._multispectral_preview_served_started_at = None
        self._multispectral_preview["diagnostics"] = {}

    def _record_multispectral_preview_served_fps(self) -> float:
        now = time.perf_counter()
        if self._multispectral_preview_served_started_at is None:
            self._multispectral_preview_served_started_at = now
            self._multispectral_preview_served_count = 0
        self._multispectral_preview_served_count += 1
        elapsed = now - self._multispectral_preview_served_started_at
        if elapsed <= 0:
            return 0.0
        return self._multispectral_preview_served_count / elapsed

    @staticmethod
    def _coerce_frame_id(value: Any) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

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

    def _preview_status(self) -> dict[str, dict[str, Any]]:
        return {
            "rgb": dict(self._rgb_preview),
            "multispectral": dict(self._multispectral_preview),
        }

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

    @staticmethod
    def _camera_device_metadata(status: dict[str, Any], frame_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        requested = status.get("requested") or {}
        actual = status.get("actual") or {}
        frame_metadata = frame_metadata or {}
        return {
            "role": status.get("role") or "rgb",
            "deviceIndex": (
                frame_metadata.get("deviceIndex")
                if frame_metadata.get("deviceIndex") is not None
                else requested.get("deviceIndex")
            ),
            "deviceName": actual.get("deviceName") or status.get("deviceName") or "",
            "stableId": actual.get("stableId") or status.get("stableId") or "",
            "backend": actual.get("backend") or status.get("backend") or "opencv",
            "transport": status.get("transport") or actual.get("transport") or "",
            "model": actual.get("model") or "",
            "serial": actual.get("cameraSerial") or actual.get("serialNumber") or "",
            "userId": actual.get("userId") or requested.get("serialNumber") or "",
            "ip": actual.get("cameraIp") or "",
            "mac": actual.get("cameraMac") or "",
            "friendlyName": actual.get("friendlyName") or status.get("deviceName") or "",
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
        elif status.get("available") or status.get("opened") or status.get("streaming"):
            check_status = "passed"
            message = self._resolution_message(status)
        elif status.get("detected") or status.get("connected"):
            check_status = "warning"
            message = status.get("error") or "DVP2 已枚举到相机，尚未完成打开取帧检查"
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
    def _preview_uint8_array(data: Any):
        import numpy as np

        array = np.asarray(data)
        if array.ndim == 3 and array.shape[2] >= 3:
            array = array[:, :, :3]
        if array.dtype != np.uint8:
            minimum = float(np.min(array)) if array.size else 0.0
            maximum = float(np.max(array)) if array.size else 0.0
            if maximum > minimum:
                array = ((array.astype(np.float32) - minimum) * (255.0 / (maximum - minimum))).clip(0, 255)
            else:
                array = np.zeros(array.shape, dtype=np.float32)
            array = array.astype(np.uint8)
        return array

    @staticmethod
    def _preview_image_from_frame(data: Any):
        from PIL import Image

        array = CameraManager._preview_uint8_array(data)
        if array.ndim == 2:
            return Image.fromarray(array, mode="L")
        return Image.fromarray(array)

    @staticmethod
    def _resolution_message(status: dict[str, Any]) -> str:
        resolution = status.get("resolution") or {}
        actual = status.get("actual") or {}
        if actual.get("width") and actual.get("height"):
            resolution = {"width": actual.get("width"), "height": actual.get("height")}
        width = resolution.get("width")
        height = resolution.get("height")
        fps = actual.get("fps")
        if fps is None:
            fps = actual.get("streamFps")
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

    @staticmethod
    def _multispectral_requested_actual_summary(status: dict[str, Any], setting_results: dict[str, Any]) -> dict[str, Any]:
        actual = status.get("actual") or {}
        exposure = setting_results.get("exposure") or {}
        gain = setting_results.get("gain") or {}
        return {
            "requestedExposure": exposure.get("requested"),
            "actualExposure": actual.get("exposure") if actual.get("exposure") is not None else exposure.get("actual"),
            "requestedGain": gain.get("requested"),
            "actualGain": actual.get("gain") if actual.get("gain") is not None else gain.get("actual"),
            "pixelFormat": actual.get("pixelFormat") or status.get("pixelFormat") or "",
            "frameDtype": actual.get("frameDtype") or status.get("frameDtype") or "",
            "streamFps": actual.get("streamFps"),
            "matchesRequested": all(result.get("accepted") for result in setting_results.values()) if setting_results else False,
        }

    @staticmethod
    def _frame_stats(data: Any) -> dict[str, Any]:
        try:
            import numpy as np

            array = np.asarray(data)
            if array.size == 0:
                return {"frameMin": None, "frameMax": None, "frameMean": None}
            return {
                "frameMin": float(np.min(array)),
                "frameMax": float(np.max(array)),
                "frameMean": float(np.mean(array)),
            }
        except Exception:
            return {"frameMin": None, "frameMax": None, "frameMean": None}
