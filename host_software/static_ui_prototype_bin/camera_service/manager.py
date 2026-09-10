from __future__ import annotations

import io
import threading
import time
from typing import Any

from .base import CameraFrame, CameraStatus
from .config import RgbCameraConfig
from .dvp2_mono import Dvp2MonoCamera
from .errors import CameraCaptureError, CameraError
from .focus_quality import FocusEvaluator
from .rgb_scientific import classify_rgb_scientific_transport
from .rgb_uvc import RgbUvcCamera
from .settings_store import CameraSettingsStore, utc_timestamp


class CameraManager:
    """Owns camera adapters and reports their status without coordinating capture."""

    def __init__(
        self,
        rgb_camera: Any | None = None,
        multispectral_camera: Any | None = None,
        rgb_config: RgbCameraConfig | dict[str, Any] | None = None,
        focus_evaluator: FocusEvaluator | None = None,
        settings_store: CameraSettingsStore | None = None,
    ) -> None:
        self.settings_store = settings_store or CameraSettingsStore()
        settings_snapshot = self.settings_store.snapshot()
        saved_rgb = settings_snapshot.get("rgb") or {}
        saved_multispectral = settings_snapshot.get("multispectral") or {}
        initial_rgb_config = rgb_config or saved_rgb
        self.rgb = rgb_camera or RgbUvcCamera(config=initial_rgb_config)
        self.multispectral = multispectral_camera or Dvp2MonoCamera(
            serial_number=saved_multispectral.get("serialNumber"),
            stable_id=saved_multispectral.get("deviceStableId"),
            device_index=saved_multispectral.get("deviceIndex"),
            friendly_name=saved_multispectral.get("friendlyName"),
        )
        self.focus_evaluator = focus_evaluator or FocusEvaluator()
        self._lock = threading.RLock()
        self._settings_restore_state = {
            "rgb": self._restore_state("not_attempted", "persistent" if settings_snapshot.get("hasCustom", {}).get("rgb") else "default"),
            "multispectral": self._restore_state(
                "not_attempted",
                "persistent" if settings_snapshot.get("hasCustom", {}).get("multispectral") else "default",
            ),
        }
        self._settings_source = {
            "rgb": self._settings_restore_state["rgb"]["settingsSource"],
            "multispectral": self._settings_restore_state["multispectral"]["settingsSource"],
        }
        self._rgb_preview = {
            "running": False,
            "width": 960,
            "height": 540,
            "fps": 12,
            "format": "image/jpeg",
            "lowLatency": True,
            "diagnostics": {},
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
        self._rgb_capture_lock = threading.RLock()
        self._rgb_latest_lock = threading.Lock()
        self._rgb_latest_frame: CameraFrame | None = None
        self._rgb_latest_diagnostics: dict[str, Any] = {}
        self._rgb_latest_jpeg: tuple[bytes, dict[str, Any]] | None = None
        self._rgb_preview_error: CameraError | None = None
        self._rgb_preview_stop_event = threading.Event()
        self._rgb_preview_ready_event = threading.Event()
        self._rgb_preview_thread: threading.Thread | None = None
        self._rgb_preview_served_count = 0
        self._rgb_preview_served_started_at: float | None = None
        self._rgb_preview_sequence = 0
        self._rgb_preview_last_served_frame_id = 0
        self._rgb_preview_dropped_frames_total = 0
        self._multispectral_latest_lock = threading.Lock()
        self._multispectral_latest_frame: CameraFrame | None = None
        self._multispectral_latest_diagnostics: dict[str, Any] = {}
        self._multispectral_latest_jpeg: tuple[bytes, dict[str, Any]] | None = None
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
                "settings": self.camera_settings(),
                "preview": {
                    "rgb": dict(self._rgb_preview),
                    "multispectral": dict(self._multispectral_preview),
                },
            }

    def release_all(self) -> None:
        """Best-effort release of all preview workers, streams, handles, and device locks."""

        rgb_thread: threading.Thread | None
        multispectral_thread: threading.Thread | None
        with self._lock:
            self._rgb_preview["running"] = False
            self._multispectral_preview["running"] = False
            self._rgb_preview_stop_event.set()
            self._multispectral_preview_stop_event.set()
            rgb_thread = self._rgb_preview_thread
            multispectral_thread = self._multispectral_preview_thread
        for thread in (rgb_thread, multispectral_thread):
            if thread and thread.is_alive():
                thread.join(timeout=2.0)
        with self._lock:
            with self._rgb_capture_lock:
                try:
                    self.rgb.stop_stream()
                except Exception:
                    pass
                try:
                    self.rgb.close()
                except Exception:
                    pass
            with self._multispectral_capture_lock:
                try:
                    self.multispectral.stop_stream()
                except Exception:
                    pass
                try:
                    self.multispectral.close()
                except Exception:
                    pass
            self._rgb_preview_thread = None
            self._multispectral_preview_thread = None
            self._reset_rgb_preview_cache()
            self._reset_multispectral_preview_cache()

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
            if ok:
                status = self._restore_multispectral_settings_locked(force=True, reason="probe")
                if not self._multispectral_preview.get("running"):
                    self.multispectral.stop_stream()
                    self.multispectral.close()
                    status = self._status_dict(self.multispectral)
                    status.update({
                        "detected": True,
                        "available": True,
                        "connected": True,
                        "opened": False,
                        "streaming": False,
                    })
            return {"passed": bool(ok), "status": status, "preview": self._preview_status()}

    def apply_rgb_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        persist = bool((payload or {}).get("persist"))
        config = RgbCameraConfig.from_dict(payload)
        thread: threading.Thread | None = None
        with self._lock:
            current = getattr(self.rgb, "config", RgbCameraConfig.from_env())
            restart_required = self._rgb_restart_required(current, config)
            preview_was_running = bool(self._rgb_preview.get("running"))
            if preview_was_running and restart_required:
                self._rgb_preview["running"] = False
                self._rgb_preview_stop_event.set()
                thread = self._rgb_preview_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            with self._rgb_capture_lock:
                result = self.rgb.apply_config(config, restart=restart_required)
                self._annotate_setting_results(result)
                if preview_was_running:
                    self.rgb.start_stream()
                    self._reset_rgb_preview_cache()
                    self._rgb_preview["running"] = True
                    self._start_rgb_preview_worker_locked()
            status = result["status"]
            setting_results = result.get("settingResults") or {}
            if persist:
                self.settings_store.update_rgb(payload, status=status, setting_results=setting_results)
                self._settings_source["rgb"] = "persistent"
            else:
                self._settings_source["rgb"] = "manual_current_session"
            self._settings_restore_state["rgb"] = self._restore_state(
                "restored" if self._setting_results_accepted(setting_results) else "partial",
                self._settings_source["rgb"],
                setting_results=setting_results,
            )
            status = self._status_dict(self.rgb)
            return {
                "restartRequired": restart_required,
                "previewRestarted": preview_was_running and restart_required,
                "settingResults": setting_results,
                "status": status,
                "summary": self._requested_actual_summary(status),
                "settings": self.camera_settings(),
                "persisted": persist,
                "preview": self._preview_status(),
            }

    def apply_multispectral_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        persist = bool((payload or {}).get("persist"))
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
            if persist:
                self.settings_store.update_multispectral(payload, status=status, setting_results=setting_results)
                self._settings_source["multispectral"] = "persistent"
            else:
                self._settings_source["multispectral"] = "manual_current_session"
            self._settings_restore_state["multispectral"] = self._restore_state(
                "restored" if self._setting_results_accepted(setting_results) else "partial",
                self._settings_source["multispectral"],
                setting_results=setting_results,
            )
            status = self._status_dict(self.multispectral)
            return {
                "settingResults": setting_results,
                "status": status,
                "summary": self._multispectral_requested_actual_summary(status, setting_results),
                "settings": self.camera_settings(),
                "persisted": persist,
                "preview": self._preview_status(),
            }

    def camera_settings(self) -> dict[str, Any]:
        snapshot = self.settings_store.snapshot()
        snapshot["restoreState"] = {
            "rgb": dict(self._settings_restore_state["rgb"]),
            "multispectral": dict(self._settings_restore_state["multispectral"]),
        }
        return snapshot

    def save_camera_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = payload or {}
        with self._lock:
            status = self.status()
            if payload.get("rgb") is not None:
                self.settings_store.update_rgb(payload.get("rgb") or {}, status=status.get("rgb") or {})
                self._settings_source["rgb"] = "persistent"
            if payload.get("multispectral") is not None:
                self.settings_store.update_multispectral(
                    payload.get("multispectral") or {},
                    status=status.get("multispectral") or {},
                )
                self._settings_source["multispectral"] = "persistent"
            return {"settings": self.camera_settings()}

    def reset_camera_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        section = payload.get("section")
        apply_defaults = bool(payload.get("apply"))
        with self._lock:
            self.settings_store.reset(section)
            roles = ("rgb", "multispectral") if section in (None, "") else (str(section),)
            for role in roles:
                self._settings_source[role] = "default"
                self._settings_restore_state[role] = self._restore_state("not_attempted", "default")
        result: dict[str, Any] = {"settings": self.camera_settings(), "applied": {}}
        if apply_defaults:
            if section in (None, "", "rgb"):
                try:
                    result["applied"]["rgb"] = self.restore_camera_settings({"section": "rgb", "force": True})
                except CameraError as exc:
                    result["applied"]["rgb"] = {"ok": False, "error": exc.user_message, "technicalError": exc.technical_message}
            if section in (None, "", "multispectral"):
                try:
                    result["applied"]["multispectral"] = self.restore_camera_settings({"section": "multispectral", "force": True})
                except CameraError as exc:
                    result["applied"]["multispectral"] = {"ok": False, "error": exc.user_message, "technicalError": exc.technical_message}
            result["settings"] = self.camera_settings()
        return result

    def restore_camera_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        section = payload.get("section")
        force = bool(payload.get("force", True))
        with self._lock:
            restored: dict[str, Any] = {}
            if section in (None, "", "rgb"):
                restored["rgb"] = self._restore_rgb_settings_locked(force=force, reason="explicit_restore")
            if section in (None, "", "multispectral"):
                restored["multispectral"] = self._restore_multispectral_settings_locked(force=force, reason="explicit_restore")
            return {"restored": restored, "settings": self.camera_settings(), "preview": self._preview_status()}

    def migrate_legacy_camera_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        legacy = (payload or {}).get("rgb") or payload or {}
        with self._lock:
            result = self.settings_store.migrate_legacy_rgb(legacy)
            if result.get("migrated"):
                self._settings_source["rgb"] = "persistent"
                self._settings_restore_state["rgb"] = self._restore_state("not_attempted", "persistent")
            return {"settings": self.camera_settings(), "migration": result}

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
            if self._rgb_preview.get("running") and getattr(self.rgb, "is_open", False):
                self._rgb_preview.update({
                    "width": max(160, min(width, 1920)),
                    "height": max(90, min(height, 1080)),
                    "fps": max(1, min(fps, 30)),
                    "format": "image/jpeg",
                    "lowLatency": True,
                })
                return {
                    "status": self._status_dict(self.rgb),
                    "preview": self._preview_status(),
                }
            with self._rgb_capture_lock:
                self.rgb.start_stream()
                status = self._restore_rgb_settings_locked(force=True, reason="preview_start")
            self._reset_rgb_preview_cache()
            self._rgb_preview.update({
                "running": True,
                "width": max(160, min(width, 1920)),
                "height": max(90, min(height, 1080)),
                "fps": max(1, min(fps, 30)),
                "format": "image/jpeg",
                "lowLatency": True,
            })
            self._start_rgb_preview_worker_locked()
            return {
                "status": status,
                "preview": self._preview_status(),
            }

    def stop_rgb_preview(self) -> dict[str, Any]:
        thread: threading.Thread | None
        with self._lock:
            self._rgb_preview["running"] = False
            self._rgb_preview_stop_event.set()
            thread = self._rgb_preview_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            with self._rgb_capture_lock:
                self.rgb.stop_stream()
                self.rgb.close()
            self._rgb_preview_thread = None
            self._reset_rgb_preview_cache()
            print("[camera.rgb] preview stopped", flush=True)
            return {
                "status": self._status_dict(self.rgb),
                "preview": self._preview_status(),
            }

    def rgb_preview_jpeg(self) -> tuple[bytes, dict[str, Any]]:
        server_started = time.perf_counter()
        with self._lock:
            if not self._rgb_preview.get("running"):
                raise CameraError("RGB 预览未启动", "Call /api/camera/rgb/preview/start first")
            fps = self._rgb_preview["fps"]

        data, encoded_meta = self._latest_rgb_preview_jpeg()
        now_epoch = time.time()
        source_age_ms = max(0.0, (now_epoch - float(encoded_meta.get("capturedAt") or now_epoch)) * 1000.0)
        server_total_ms = (time.perf_counter() - server_started) * 1000.0
        measured_fps = self._record_rgb_preview_served_fps(encoded_meta.get("frameId"))
        diagnostics = {
            **encoded_meta,
            "sourceAgeMs": source_age_ms,
            "serverTotalMs": server_total_ms,
            "measuredPreviewFps": measured_fps,
            "lowLatency": True,
        }
        with self._lock:
            self._rgb_preview["diagnostics"] = dict(diagnostics)
        return data, {
            "fps": fps,
            "contentType": "image/jpeg",
            **diagnostics,
        }

    def capture_rgb_frame(self) -> tuple[CameraFrame, dict[str, Any]]:
        """Capture one production RGB frame through the owned RGB adapter."""

        thread: threading.Thread | None = None
        with self._lock:
            preview_was_running = bool(self._rgb_preview.get("running") and getattr(self.rgb, "is_open", False))
            if preview_was_running:
                self._rgb_preview["running"] = False
                self._rgb_preview_stop_event.set()
                thread = self._rgb_preview_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            with self._rgb_capture_lock:
                try:
                    if preview_was_running and getattr(self.rgb, "is_open", False):
                        self.rgb.stop_stream()
                        self.rgb.close()
                    scientific_config = self._rgb_scientific_config()
                    if hasattr(self.rgb, "apply_config"):
                        self.rgb.apply_config(scientific_config, restart=True)
                    else:
                        self.rgb.open()
                    frame = self.rgb.capture_frame()
                    status = self._status_dict(self.rgb)
                    policy = classify_rgb_scientific_transport(
                        requested_fourcc=scientific_config.fourcc,
                        actual_fourcc=(status.get("actual") or {}).get("fourcc"),
                        color_space=frame.color_space,
                        dtype=frame.dtype,
                    )
                    metadata = {
                        "status": status,
                        "preview": self._preview_status(),
                        "previewWasRunning": preview_was_running,
                        "openedForCapture": True,
                        "scientificProfile": scientific_config.to_dict(),
                        "requestedSettings": dict(status.get("requested") or {}),
                        "actualSettings": dict(status.get("actual") or {}),
                        "settingsSource": self._settings_source.get("rgb") or "default",
                        "settingsRestoreState": dict(self._settings_restore_state["rgb"]),
                        "device": self._camera_device_metadata(status, frame.metadata),
                        **policy,
                    }
                    if not policy["scientificStrictLossless"]:
                        code = (
                            "RGB_SCIENTIFIC_TRANSPORT_LOSSY"
                            if policy["sourceCompression"] == "lossy"
                            else "RGB_SCIENTIFIC_LOSSLESS_UNAVAILABLE"
                        )
                        raise CameraCaptureError(
                            "当前 RGB 相机正式采集链路存在有损或未验证传输，禁止用于科学采集。",
                            f"{code}: requested={policy['requestedFourcc']} actual={policy['actualFourcc']} "
                            f"compression={policy['sourceCompression']} reason={policy['reason']}",
                        )
                    return frame, metadata
                finally:
                    self.rgb.stop_stream()
                    self.rgb.close()
                    if preview_was_running:
                        self._reset_rgb_preview_cache()
                        self._rgb_preview["running"] = True
                        self._start_rgb_preview_worker_locked()

    def capture_multispectral_frame(self) -> tuple[CameraFrame, dict[str, Any]]:
        """Capture one production DVP2 mono frame through the owned adapter."""

        with self._lock:
            was_open = bool(getattr(self.multispectral, "is_open", False))
            preview_was_running = bool(self._multispectral_preview.get("running") and was_open)
            try:
                with self._multispectral_capture_lock:
                    if not was_open:
                        self.multispectral.open()
                        self._restore_multispectral_settings_locked(force=True, reason="capture_open")
                    frame = self.multispectral.capture_frame()
                status = self._status_dict(self.multispectral)
                metadata = {
                    "status": status,
                    "preview": self._preview_status(),
                    "previewWasRunning": preview_was_running,
                    "openedForCapture": not was_open,
                    "requestedSettings": dict(status.get("requested") or {}),
                    "actualSettings": dict(status.get("actual") or {}),
                    "settingsSource": self._settings_source.get("multispectral") or "default",
                    "settingsRestoreState": dict(self._settings_restore_state["multispectral"]),
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
                status = self._restore_multispectral_settings_locked(force=True, reason="preview_start")
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
                "status": status,
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
            fps = self._multispectral_preview["fps"]

        data, encoded_meta = self._latest_multispectral_preview_jpeg()
        now_epoch = time.time()
        source_age_ms = max(0.0, (now_epoch - float(encoded_meta.get("capturedAt") or now_epoch)) * 1000.0)
        server_total_ms = (time.perf_counter() - server_started) * 1000.0
        measured_fps = self._record_multispectral_preview_served_fps()
        diagnostics = {
            **encoded_meta,
            "sourceAgeMs": source_age_ms,
            "serverTotalMs": server_total_ms,
            "measuredPreviewFps": measured_fps,
            "lowLatency": True,
            "encodedPreviewCache": True,
        }
        with self._lock:
            self._multispectral_preview["diagnostics"] = dict(diagnostics)
        return data, {
            "fps": fps,
            "contentType": "image/jpeg",
            **diagnostics,
        }

    def _encode_rgb_preview_jpeg(self, frame: CameraFrame, target: tuple[int, int]) -> tuple[bytes, float, float, str]:
        try:
            return self._encode_rgb_preview_jpeg_cv2(frame.data, target)
        except Exception:
            return self._encode_rgb_preview_jpeg_pil(frame.data, target)

    @staticmethod
    def _encode_rgb_preview_jpeg_cv2(data: Any, target: tuple[int, int]) -> tuple[bytes, float, float, str]:
        import cv2
        import numpy as np

        resize_started = time.perf_counter()
        array = np.asarray(data)
        if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] < 3:
            raise CameraError("RGB 预览帧格式无效", f"Unexpected RGB preview array: shape={array.shape}; dtype={array.dtype}")
        array = array[:, :, :3]
        if array.shape[1] != target[0] or array.shape[0] != target[1]:
            array = cv2.resize(array, target, interpolation=cv2.INTER_LINEAR)
        resize_duration_ms = (time.perf_counter() - resize_started) * 1000.0

        encode_started = time.perf_counter()
        bgr = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
        ok, encoded = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            raise CameraError("RGB 预览 JPEG 编码失败", "cv2.imencode returned false")
        jpeg_encode_duration_ms = (time.perf_counter() - encode_started) * 1000.0
        return encoded.tobytes(), resize_duration_ms, jpeg_encode_duration_ms, "opencv"

    @staticmethod
    def _encode_rgb_preview_jpeg_pil(data: Any, target: tuple[int, int]) -> tuple[bytes, float, float, str]:
        from PIL import Image
        import numpy as np

        resize_started = time.perf_counter()
        array = np.asarray(data)
        if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] < 3:
            raise CameraError("RGB 预览帧格式无效", f"Unexpected RGB preview array: shape={array.shape}; dtype={array.dtype}")
        image = Image.fromarray(array[:, :, :3], mode="RGB")
        if image.size != target:
            resampling = getattr(getattr(Image, "Resampling", Image), "BILINEAR")
            image = image.resize(target, resampling)
        resize_duration_ms = (time.perf_counter() - resize_started) * 1000.0

        encode_started = time.perf_counter()
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=80, optimize=False)
        jpeg_encode_duration_ms = (time.perf_counter() - encode_started) * 1000.0
        return buffer.getvalue(), resize_duration_ms, jpeg_encode_duration_ms, "pil"

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
        ok, encoded = cv2.imencode(".jpg", array, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
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
        image.save(buffer, format="JPEG", quality=80, optimize=False)
        jpeg_encode_duration_ms = (time.perf_counter() - encode_started) * 1000.0
        return buffer.getvalue(), resize_duration_ms, jpeg_encode_duration_ms, "pil"

    def _start_rgb_preview_worker_locked(self) -> None:
        if self._rgb_preview_thread and self._rgb_preview_thread.is_alive():
            return
        self._rgb_preview_stop_event.clear()
        self._rgb_preview_ready_event.clear()
        self._rgb_preview_thread = threading.Thread(
            target=self._rgb_preview_worker,
            name="rgb-preview-latest-frame",
            daemon=True,
        )
        self._rgb_preview_thread.start()

    def _rgb_preview_worker(self) -> None:
        while not self._rgb_preview_stop_event.is_set():
            with self._lock:
                target_fps = float(self._rgb_preview.get("fps") or 12)
                target = (int(self._rgb_preview["width"]), int(self._rgb_preview["height"]))
                running = bool(self._rgb_preview.get("running"))
            if not running:
                break
            perf_started = time.perf_counter()
            capture_started_at = time.time()
            try:
                with self._rgb_capture_lock:
                    frame = self.rgb.capture_frame()
            except CameraError as exc:
                self._rgb_preview_error = exc
                self._rgb_preview_stop_event.set()
                break
            except Exception as exc:
                self._rgb_preview_error = CameraError("RGB 预览取帧失败", str(exc))
                self._rgb_preview_stop_event.set()
                break
            captured_at = time.time()
            capture_duration_ms = (time.perf_counter() - perf_started) * 1000.0
            try:
                data, resize_duration_ms, jpeg_encode_duration_ms, encoder = self._encode_rgb_preview_jpeg(frame, target)
            except CameraError as exc:
                self._rgb_preview_error = exc
                self._rgb_preview_stop_event.set()
                break
            except Exception as exc:
                self._rgb_preview_error = CameraError("RGB 预览 JPEG 编码失败", str(exc))
                self._rgb_preview_stop_event.set()
                break
            with self._rgb_latest_lock:
                if (
                    self._rgb_preview_sequence > 0
                    and self._rgb_preview_last_served_frame_id < self._rgb_preview_sequence
                ):
                    self._rgb_preview_dropped_frames_total += 1
                self._rgb_preview_sequence += 1
                frame_id = self._rgb_preview_sequence
                diagnostics = {
                    "frameId": frame_id,
                    "sourceTimestamp": frame.metadata.get("timestamp"),
                    "captureStartedAt": capture_started_at,
                    "capturedAt": captured_at,
                    "captureDurationMs": capture_duration_ms,
                    "resizeDurationMs": resize_duration_ms,
                    "jpegEncodeDurationMs": jpeg_encode_duration_ms,
                    "droppedFrames": self._rgb_preview_dropped_frames_total,
                    "acquiredAt": captured_at,
                    "sourceShape": frame.shape,
                    "sourceDtype": frame.dtype,
                    "previewWidth": target[0],
                    "previewHeight": target[1],
                    "previewEncoder": encoder,
                    "previewQuality": 80,
                }
                self._rgb_latest_frame = frame
                self._rgb_latest_diagnostics = dict(diagnostics)
                self._rgb_latest_jpeg = (data, dict(diagnostics))
                self._rgb_preview_ready_event.set()
            elapsed = time.perf_counter() - perf_started
            interval = 1.0 / max(1.0, min(target_fps, 60.0))
            self._rgb_preview_stop_event.wait(timeout=max(0.0, interval - elapsed))

    def _latest_rgb_preview_jpeg(self) -> tuple[bytes, dict[str, Any]]:
        if not self._rgb_preview_ready_event.wait(timeout=1.0):
            if self._rgb_preview_error is not None:
                exc = self._rgb_preview_error
                self._cleanup_failed_rgb_preview()
                raise exc
            self._cleanup_failed_rgb_preview()
            raise CameraError("RGB 预览尚未取得帧", "RGB latest-frame preview cache is empty")
        if self._rgb_preview_error is not None:
            exc = self._rgb_preview_error
            self._cleanup_failed_rgb_preview()
            raise exc
        with self._rgb_latest_lock:
            cached = self._rgb_latest_jpeg
        if cached is None:
            self._cleanup_failed_rgb_preview()
            raise CameraError("RGB 预览尚未取得帧", "RGB latest-frame preview JPEG cache is empty")
        return cached[0], dict(cached[1])

    def _cleanup_failed_rgb_preview(self) -> None:
        with self._lock:
            self._rgb_preview["running"] = False
            self._rgb_preview_stop_event.set()
            with self._rgb_capture_lock:
                self.rgb.stop_stream()
                self.rgb.close()
            self._rgb_preview_thread = None
            self._reset_rgb_preview_cache(clear_error=False)

    def _reset_rgb_preview_cache(self, *, clear_error: bool = True) -> None:
        with self._rgb_latest_lock:
            self._rgb_latest_frame = None
            self._rgb_latest_diagnostics = {}
            self._rgb_latest_jpeg = None
        self._rgb_preview_ready_event.clear()
        if clear_error:
            self._rgb_preview_error = None
        self._rgb_preview_served_count = 0
        self._rgb_preview_served_started_at = None
        self._rgb_preview_sequence = 0
        self._rgb_preview_last_served_frame_id = 0
        self._rgb_preview_dropped_frames_total = 0
        self._rgb_preview["diagnostics"] = {}

    def _record_rgb_preview_served_fps(self, frame_id: Any = None) -> float:
        now = time.perf_counter()
        if self._rgb_preview_served_started_at is None:
            self._rgb_preview_served_started_at = now
            self._rgb_preview_served_count = 0
        self._rgb_preview_served_count += 1
        coerced = self._coerce_frame_id(frame_id)
        if coerced is not None:
            with self._rgb_latest_lock:
                self._rgb_preview_last_served_frame_id = max(self._rgb_preview_last_served_frame_id, coerced)
        elapsed = now - self._rgb_preview_served_started_at
        if elapsed <= 0:
            return 0.0
        return self._rgb_preview_served_count / elapsed

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
            captured_at = time.time()
            frame_id = self._coerce_frame_id(frame.metadata.get("frameId"))
            dropped_frames = 0
            if frame_id is not None and last_frame_id is not None and frame_id > last_frame_id:
                dropped_frames = max(0, frame_id - last_frame_id - 1)
            if frame_id is not None:
                last_frame_id = frame_id
            with self._lock:
                target = (int(self._multispectral_preview["width"]), int(self._multispectral_preview["height"]))
            try:
                data, resize_duration_ms, jpeg_encode_duration_ms, encoder = self._encode_multispectral_preview_jpeg(frame, target)
            except CameraError as exc:
                self._multispectral_preview_error = exc
                self._multispectral_preview_stop_event.set()
                break
            except Exception as exc:
                self._multispectral_preview_error = CameraError("多光谱预览 JPEG 编码失败", str(exc))
                self._multispectral_preview_stop_event.set()
                break
            with self._multispectral_latest_lock:
                self._multispectral_latest_frame = frame
                diagnostics = {
                    "frameId": frame_id,
                    "sourceTimestamp": frame.metadata.get("timestamp"),
                    "captureStartedAt": captured_at - (capture_duration_ms / 1000.0),
                    "capturedAt": captured_at,
                    "captureDurationMs": capture_duration_ms,
                    "resizeDurationMs": resize_duration_ms,
                    "jpegEncodeDurationMs": jpeg_encode_duration_ms,
                    "droppedFrames": dropped_frames,
                    "acquiredAt": captured_at,
                    "sourceShape": frame.shape,
                    "sourceDtype": frame.dtype,
                    "previewWidth": target[0],
                    "previewHeight": target[1],
                    "pixelFormat": frame.metadata.get("pixelFormat", ""),
                    **self._frame_stats(frame.data),
                    "previewEncoder": encoder,
                    "previewQuality": 80,
                }
                self._multispectral_latest_diagnostics = dict(diagnostics)
                self._multispectral_latest_jpeg = (data, dict(diagnostics))
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

    def _latest_multispectral_preview_jpeg(self) -> tuple[bytes, dict[str, Any]]:
        if not self._multispectral_preview_ready_event.wait(timeout=1.0):
            if self._multispectral_preview_error is not None:
                exc = self._multispectral_preview_error
                self._cleanup_failed_multispectral_preview()
                raise exc
            self._cleanup_failed_multispectral_preview()
            raise CameraError("多光谱预览尚未取得帧", "DVP2 latest-frame preview JPEG cache is empty")
        if self._multispectral_preview_error is not None:
            exc = self._multispectral_preview_error
            self._cleanup_failed_multispectral_preview()
            raise exc
        with self._multispectral_latest_lock:
            cached = self._multispectral_latest_jpeg
        if cached is None:
            frame, diagnostics = self._latest_multispectral_preview_frame()
            with self._lock:
                target = (int(self._multispectral_preview["width"]), int(self._multispectral_preview["height"]))
            data, resize_duration_ms, jpeg_encode_duration_ms, encoder = self._encode_multispectral_preview_jpeg(frame, target)
            diagnostics = {
                **diagnostics,
                "resizeDurationMs": resize_duration_ms,
                "jpegEncodeDurationMs": jpeg_encode_duration_ms,
                "sourceShape": frame.shape,
                "sourceDtype": frame.dtype,
                "previewWidth": target[0],
                "previewHeight": target[1],
                "pixelFormat": frame.metadata.get("pixelFormat", ""),
                **self._frame_stats(frame.data),
                "previewEncoder": encoder,
            }
            return data, diagnostics
        return cached[0], dict(cached[1])

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
            self._multispectral_latest_jpeg = None
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
                with self._rgb_capture_lock:
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
                self._rgb_preview_stop_event.set()
                with self._rgb_capture_lock:
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
        if rgb_ok:
            rgb_status = self._restore_rgb_settings_locked(force=True, reason="probe")
            if not self._rgb_preview.get("running"):
                self.rgb.stop_stream()
                self.rgb.close()
                rgb_status = self._status_dict(self.rgb)
                rgb_status.update({
                    "detected": True,
                    "available": True,
                    "connected": True,
                    "opened": False,
                    "streaming": False,
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

    def _rgb_scientific_config(self) -> RgbCameraConfig:
        current = getattr(self.rgb, "config", RgbCameraConfig.from_env())
        settings = self.settings_store.get_rgb()
        profile = dict(settings.get("scientificProfile") or {})
        return RgbCameraConfig.from_dict({
            **current.to_dict(),
            "width": profile.get("width", current.width),
            "height": profile.get("height", current.height),
            "fps": profile.get("fps", current.fps),
            "fourcc": profile.get("fourcc", current.fourcc),
        })

    def rgb_scientific_status(self) -> dict[str, Any]:
        with self._lock:
            status = self._status_dict(self.rgb)
            actual = status.get("actual") or {}
            scientific_config = self._rgb_scientific_config()
            policy = classify_rgb_scientific_transport(
                requested_fourcc=scientific_config.fourcc,
                actual_fourcc=actual.get("fourcc"),
                color_space=status.get("colorSpace") or "RGB",
                dtype=status.get("frameDtype") or "uint8",
            )
            return {
                **policy,
                "available": bool(status.get("available")),
                "profile": scientific_config.to_dict(),
                "actual": actual,
            }

    @staticmethod
    def _restore_state(
        state: str,
        settings_source: str,
        *,
        reason: str = "",
        error: str = "",
        setting_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "settingsSource": settings_source,
            "lastRestoredAt": utc_timestamp() if state not in {"not_attempted"} else "",
            "restoreError": error,
            "reason": reason,
            "settingResults": dict(setting_results or {}),
        }

    @staticmethod
    def _setting_results_accepted(setting_results: dict[str, Any]) -> bool:
        relevant = [
            result
            for result in (setting_results or {}).values()
            if not result.get("skipped") and result.get("accepted") is not None
        ]
        return bool(relevant) and all(bool(result.get("accepted")) for result in relevant)

    @staticmethod
    def _annotate_setting_results(result: dict[str, Any]) -> None:
        status = result.get("status") or {}
        actual = status.get("actual") or {}
        for key, setting_result in (result.get("settingResults") or {}).items():
            if isinstance(setting_result, dict) and key in actual and "actual" not in setting_result:
                setting_result["actual"] = actual.get(key)

    def _restore_rgb_settings_locked(self, *, force: bool = False, reason: str = "") -> dict[str, Any]:
        settings = self.settings_store.get_rgb()
        has_saved = self.settings_store.has_custom("rgb")
        if not has_saved and not self.settings_store.path.exists():
            self._settings_source["rgb"] = "default"
            self._settings_restore_state["rgb"] = self._restore_state("not_attempted", "default", reason=reason)
            return self._status_dict(self.rgb)
        if not force and not has_saved:
            return self._status_dict(self.rgb)
        if not hasattr(self.rgb, "apply_config"):
            status = self._status_dict(self.rgb)
            state = "failed" if has_saved else "not_attempted"
            self._settings_restore_state["rgb"] = self._restore_state(
                state,
                "persistent" if has_saved else "default",
                reason=reason,
                error="RGB adapter does not support apply_config" if has_saved else "",
            )
            return status
        status = self._status_dict(self.rgb)
        if self._device_mismatch("rgb", settings, status):
            self._settings_restore_state["rgb"] = self._restore_state(
                "device_mismatch",
                "persistent" if has_saved else "default",
                reason=reason,
                error="CAMERA_SETTINGS_DEVICE_MISMATCH",
            )
            print("[camera.settings] rgb restore blocked: CAMERA_SETTINGS_DEVICE_MISMATCH", flush=True)
            return self._status_dict(self.rgb)
        try:
            config = RgbCameraConfig.from_dict(settings)
            current = getattr(self.rgb, "config", RgbCameraConfig.from_env())
            result = self.rgb.apply_config(config, restart=self._rgb_restart_required(current, config))
            self._annotate_setting_results(result)
            setting_results = result.get("settingResults") or {}
            self._settings_source["rgb"] = "persistent" if has_saved else "default"
            state = "restored" if self._setting_results_accepted(setting_results) else "partial"
            self._settings_restore_state["rgb"] = self._restore_state(
                state,
                self._settings_source["rgb"],
                reason=reason,
                setting_results=setting_results,
            )
            restored_status = self._status_dict(self.rgb)
            actual = restored_status.get("actual") or {}
            print(
                "[camera.settings] rgb restored "
                f"source={self._settings_source['rgb']} exposure={settings.get('exposure')} actual={actual.get('exposure')}",
                flush=True,
            )
            return restored_status
        except CameraError as exc:
            self._settings_restore_state["rgb"] = self._restore_state(
                "failed",
                "persistent" if has_saved else "default",
                reason=reason,
                error=exc.user_message,
            )
            print(f"[camera.settings] rgb restore failed: {exc.technical_message}", flush=True)
            raise

    def _restore_multispectral_settings_locked(self, *, force: bool = False, reason: str = "") -> dict[str, Any]:
        settings = self.settings_store.get_multispectral()
        has_saved = self.settings_store.has_custom("multispectral")
        if not has_saved and not self.settings_store.path.exists():
            self._settings_source["multispectral"] = "default"
            self._settings_restore_state["multispectral"] = self._restore_state("not_attempted", "default", reason=reason)
            return self._status_dict(self.multispectral)
        if not force and not has_saved:
            return self._status_dict(self.multispectral)
        if not (hasattr(self.multispectral, "set_exposure") and hasattr(self.multispectral, "set_gain")):
            status = self._status_dict(self.multispectral)
            state = "failed" if has_saved else "not_attempted"
            self._settings_restore_state["multispectral"] = self._restore_state(
                state,
                "persistent" if has_saved else "default",
                reason=reason,
                error="DVP2 adapter does not support exposure/gain setters" if has_saved else "",
            )
            return status
        status = self._status_dict(self.multispectral)
        if self._device_mismatch("multispectral", settings, status):
            self._settings_restore_state["multispectral"] = self._restore_state(
                "device_mismatch",
                "persistent" if has_saved else "default",
                reason=reason,
                error="CAMERA_SETTINGS_DEVICE_MISMATCH",
            )
            print("[camera.settings] dvp2 restore blocked: CAMERA_SETTINGS_DEVICE_MISMATCH", flush=True)
            return self._status_dict(self.multispectral)
        setting_results: dict[str, Any] = {}
        try:
            if settings.get("exposure") is not None:
                requested = float(settings.get("exposure"))
                actual = self.multispectral.set_exposure(requested)
                setting_results["exposure"] = {"requested": requested, "actual": actual, "accepted": True}
            if settings.get("gain") is not None:
                requested = float(settings.get("gain"))
                actual = self.multispectral.set_gain(requested)
                setting_results["gain"] = {"requested": requested, "actual": actual, "accepted": True}
            self._settings_source["multispectral"] = "persistent" if has_saved else "default"
            self._settings_restore_state["multispectral"] = self._restore_state(
                "restored" if self._setting_results_accepted(setting_results) else "partial",
                self._settings_source["multispectral"],
                reason=reason,
                setting_results=setting_results,
            )
            restored_status = self._status_dict(self.multispectral)
            print(
                "[camera.settings] dvp2 restored "
                f"source={self._settings_source['multispectral']} exposure={settings.get('exposure')} gain={settings.get('gain')}",
                flush=True,
            )
            return restored_status
        except CameraError as exc:
            self._settings_restore_state["multispectral"] = self._restore_state(
                "failed",
                "persistent" if has_saved else "default",
                reason=reason,
                error=exc.user_message,
                setting_results=setting_results,
            )
            print(f"[camera.settings] dvp2 restore failed: {exc.technical_message}", flush=True)
            raise

    def _device_mismatch(self, role: str, settings: dict[str, Any], status: dict[str, Any]) -> bool:
        actual = status.get("actual") or {}
        if role == "rgb":
            saved_id = str(settings.get("deviceStableId") or "").strip()
            current_id = str(status.get("stableId") or actual.get("stableId") or "").strip()
            if saved_id and current_id and not saved_id.startswith("opencv-dshow:") and saved_id != current_id:
                return True
            return False
        saved_ids = {
            str(settings.get("deviceStableId") or "").strip(),
            str(settings.get("serialNumber") or "").strip(),
        } - {""}
        current_ids = {
            str(status.get("stableId") or "").strip(),
            str(actual.get("stableId") or "").strip(),
            str(actual.get("cameraSerial") or "").strip(),
            str(actual.get("serialNumber") or "").strip(),
            str(actual.get("userId") or "").strip(),
        } - {""}
        if not saved_ids or not current_ids:
            return False
        normalized_saved = {self._normalize_device_id(value) for value in saved_ids}
        normalized_current = {self._normalize_device_id(value) for value in current_ids}
        return normalized_saved.isdisjoint(normalized_current)

    @staticmethod
    def _normalize_device_id(value: str) -> str:
        text = str(value or "").strip().upper()
        if text.startswith("DSGP"):
            return text[2:]
        return text

    def _status_dict(self, adapter: Any) -> dict[str, Any]:
        try:
            status = adapter.get_status()
            if isinstance(status, CameraStatus):
                status_dict = status.to_dict()
            else:
                status_dict = dict(status)
        except CameraError as exc:
            status_dict = {
                "role": getattr(adapter, "role", ""),
                "available": False,
                "connected": False,
                "streaming": False,
                "transport": getattr(adapter, "transport", ""),
                "error": exc.user_message,
                "technicalError": exc.technical_message,
            }
        except Exception as exc:
            status_dict = {
                "role": getattr(adapter, "role", ""),
                "available": False,
                "connected": False,
                "streaming": False,
                "transport": getattr(adapter, "transport", ""),
                "error": "相机状态读取失败",
                "technicalError": str(exc),
            }
        role = status_dict.get("role") or getattr(adapter, "role", "")
        if role in self._settings_restore_state:
            status_dict["settingsRestoreState"] = dict(self._settings_restore_state[role])
            status_dict["settingsSource"] = self._settings_source.get(role) or "default"
        if role == "rgb":
            actual = status_dict.get("actual") or {}
            scientific_config = self._rgb_scientific_config()
            status_dict["scientificTransport"] = classify_rgb_scientific_transport(
                requested_fourcc=scientific_config.fourcc,
                actual_fourcc=actual.get("fourcc"),
                color_space=status_dict.get("colorSpace") or "RGB",
                dtype=status_dict.get("frameDtype") or "uint8",
            )
            status_dict["scientificProfile"] = scientific_config.to_dict()
        return status_dict

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
