from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import CameraDeviceInfo, CameraFrame, CameraStatus
from .dvp2_binding import (
    DVP_STATUS_TIME_OUT,
    Dvp2ApiError,
    Dvp2Binding,
    Dvp2BindingError,
    Dvp2DeviceInfo,
    frame_pixel_format,
    frame_to_array,
    image_format_name,
)
from .errors import (
    CameraCaptureError,
    CameraOpenError,
    CameraSdkUnavailableError,
    CameraSettingUnsupported,
    CameraTimeoutError,
    CameraUnavailableError,
)


DEFAULT_DVP2_SERIAL = "GP23400004963"


@dataclass(frozen=True)
class Dvp2SdkInfo:
    sdk_dir: Path | None
    dll_path: Path | None
    reason: str = ""

    @property
    def sdk_available(self) -> bool:
        return self.dll_path is not None


def find_dvp2_sdk(configured_dir: str | os.PathLike[str] | None = None) -> Dvp2SdkInfo:
    """Find an installed DVP2 runtime without scanning the whole disk."""

    candidates: list[Path] = []
    if configured_dir:
        candidates.append(Path(configured_dir).expanduser())
    else:
        for value in (
            os.environ.get("DVP2_SDK_DIR"),
            r"D:\Netease\DVP2 SDK CN",
        ):
            if value:
                candidates.append(Path(value).expanduser())

    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env_name)
        if not root:
            continue
        base = Path(root)
        candidates.extend([
            base / "DVP2",
            base / "DVP2 x64",
            base / "BasedCam3",
            base / "Do3Think" / "DVP2",
            base / "DO3THINK" / "DVP2",
        ])

    seen: set[Path] = set()
    checked: list[str] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if not candidate.exists() or not candidate.is_dir():
            checked.append(str(candidate))
            continue
        for dll_path in _candidate_dll_paths(candidate):
            checked.append(str(dll_path))
            if dll_path.exists() and dll_path.is_file():
                return Dvp2SdkInfo(candidate, dll_path)

    reason = "DVPCamera64.dll not found"
    if checked:
        reason += " in: " + "; ".join(checked[:8])
    return Dvp2SdkInfo(None, None, reason)


class Dvp2MonoCamera:
    """DO3THINK/DVP2 monochrome GigE camera adapter.

    This adapter is limited to the vendor DVP2 C API confirmed in
    DVPCamera.h and the official examples. It deliberately does not use
    OpenCV VideoCapture for the GigE mono camera and does not coordinate the
    filter wheel or sample stage.
    """

    role = "multispectral"
    transport = "GigE/DVP2"

    def __init__(
        self,
        *,
        sdk_dir: str | os.PathLike[str] | None = None,
        serial_number: str | None = None,
        stable_id: str | None = None,
        device_index: int | None = None,
        friendly_name: str | None = None,
        timeout_ms: int = 3000,
        auto_ip: bool = False,
        loader: Any | None = None,
        binding_factory: Any | None = None,
    ) -> None:
        self.sdk_dir = Path(sdk_dir).expanduser() if sdk_dir else None
        self.serial_number = str(serial_number or os.environ.get("DVP2_CAMERA_SERIAL") or DEFAULT_DVP2_SERIAL).strip()
        self.stable_id = str(stable_id or os.environ.get("DVP2_CAMERA_STABLE_ID") or "").strip()
        self.device_index = _optional_int(device_index, os.environ.get("DVP2_CAMERA_INDEX"))
        self.friendly_name = str(friendly_name or os.environ.get("DVP2_CAMERA_FRIENDLY_NAME") or "").strip()
        self.timeout_ms = int(timeout_ms)
        self.auto_ip = bool(auto_ip)
        self._loader = loader
        self._binding_factory = binding_factory
        self._binding: Any | None = None
        self._sdk_info: Dvp2SdkInfo | None = None
        self._handle: int | None = None
        self._streaming = False
        self._selected_device: Dvp2DeviceInfo | None = None
        self._last_devices: list[Dvp2DeviceInfo] = []
        self._last_error = ""
        self._last_technical_error = ""
        self._available = False
        self._resolution: tuple[int, int] | None = None
        self._exposure: float | None = None
        self._gain: float | None = None
        self._pixel_format = ""
        self._frame_dtype = ""
        self._last_frame_metadata: dict[str, Any] = {}
        self._capabilities: dict[str, Any] = {
            "supportedPixelFormats": ["Mono8"],
            "verifiedPixelFormats": ["Mono8"],
            "pixelFormatSwitching": {
                "supported": False,
                "reason": "本阶段只验证 Mono8，不开放格式切换。",
            },
        }

    def list_devices(self) -> list[CameraDeviceInfo]:
        binding = self._ensure_binding()
        devices = self._enum_devices(binding)
        return [
            CameraDeviceInfo(
                role=self.role,
                device_index=device.index if device.index >= 0 else None,
                device_name=device.friendly_name or device.model,
                stable_id=_device_stable_id(device),
                backend="dvp2",
                transport=self.transport,
            )
            for device in devices
        ]

    def probe_available(self) -> bool:
        if not self._binding_factory:
            return self._probe_available_subprocess()
        try:
            self.open()
            return True
        except (CameraSdkUnavailableError, CameraUnavailableError, CameraOpenError):
            return False
        finally:
            self.close()

    def open(self) -> None:
        if self.is_open:
            return
        binding = self._ensure_binding()
        devices = self._enum_devices(binding)
        device = self._select_device(devices)
        try:
            handle = _open_device(binding, device, auto_ip=self.auto_ip)
            self._handle = int(handle)
            self._selected_device = self._merge_open_device_info(binding, device)
            self._available = True
            self._last_error = ""
            self._last_technical_error = ""
            self._refresh_open_status(binding)
        except Dvp2ApiError as exc:
            self._last_error = _friendly_open_error(self._selected_device or device)
            self._last_technical_error = str(exc)
            raise CameraOpenError(self._last_error, self._last_technical_error) from exc

    def close(self) -> None:
        binding = self._binding
        handle = self._handle
        self._handle = None
        if binding is None or handle is None:
            self._streaming = False
            return
        try:
            if self._streaming:
                try:
                    binding.stop(handle)
                except Dvp2ApiError:
                    pass
            binding.close(handle)
        except Dvp2ApiError:
            return
        finally:
            self._streaming = False

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    def get_status(self) -> CameraStatus:
        info = self._sdk_info or find_dvp2_sdk(self.sdk_dir)
        self._sdk_info = info
        detected = bool(self._selected_device)
        if info.sdk_available and not self.is_open:
            try:
                devices = self._enum_devices(self._ensure_binding())
                detected = bool(devices)
                selected = self._select_device_or_none(devices)
                if selected:
                    self._selected_device = selected
                    detected = True
                    self._last_error = "" if self._available else self._last_error
                    self._last_technical_error = "" if self._available else self._last_technical_error
                elif devices and self.serial_number:
                    self._last_error = "已枚举 DVP2 相机，但未找到配置的黑白相机序列号"
                    self._last_technical_error = f"configured serial={self.serial_number}; devices={[d.serial_number for d in devices]}"
            except (CameraSdkUnavailableError, CameraUnavailableError):
                detected = False
        selected = self._selected_device
        status = CameraStatus(
            role=self.role,
            detected=detected,
            available=bool(self._available),
            connected=bool(detected),
            opened=self.is_open,
            streaming=self._streaming,
            sdk_available=info.sdk_available,
            backend="dvp2",
            transport=self.transport,
            device_index=selected.index if selected and selected.index >= 0 else self.device_index,
            device_name=(selected.friendly_name or selected.model) if selected else "",
            stable_id=_device_stable_id(selected) if selected else self.serial_number,
            resolution=self._resolution,
            exposure=self._exposure,
            gain=self._gain,
            requested={
                "vendor": "DO3THINK",
                "model": "MGV231M-H2",
                "transport": "GigE/RJ45 Ethernet",
                "sdk": "DVP2",
                "serialNumber": self.serial_number,
                "deviceIndex": self.device_index,
                "friendlyName": self.friendly_name,
            },
            actual=self._actual_dict(selected),
            capabilities=dict(self._capabilities),
            pixel_format=self._pixel_format,
            color_space="MONO",
            frame_dtype=self._frame_dtype,
            sdk_path=str(info.sdk_dir) if info.sdk_dir else "",
            dll_path=str(info.dll_path) if info.dll_path else "",
        )
        if not info.sdk_available:
            status.detected = False
            status.available = False
            status.connected = False
            status.error = "多光谱 GigE 相机 DVP2 SDK 尚未安装"
            status.technical_error = info.reason or "DVPCamera64.dll not found"
        elif not detected:
            status.error = self._last_error or "DVP2 SDK 已加载，但未枚举到多光谱相机"
            status.technical_error = self._last_technical_error or "dvpRefresh/dvpEnum returned no target device"
        elif self._last_error and not self._available:
            status.error = self._last_error
            status.technical_error = self._last_technical_error
        return status

    def start_stream(self) -> None:
        self.open()
        if self._streaming:
            return
        try:
            self._binding.set_trigger_state(self._handle, False)
            self._binding.start(self._handle)
            self._streaming = True
            self._refresh_open_status(self._binding)
        except Dvp2ApiError as exc:
            self._last_error = "多光谱相机视频流启动失败"
            self._last_technical_error = str(exc)
            raise CameraCaptureError(self._last_error, self._last_technical_error) from exc

    def stop_stream(self) -> None:
        if not self.is_open or not self._streaming:
            self._streaming = False
            return
        try:
            self._binding.stop(self._handle)
        except Dvp2ApiError:
            pass
        finally:
            self._streaming = False

    def capture_frame(self) -> CameraFrame:
        if not self._streaming:
            self.start_stream()
        try:
            frame, buffer_ptr = self._binding.get_frame(self._handle, timeout_ms=self.timeout_ms)
            array = frame_to_array(frame, buffer_ptr)
            pixel_format = frame_pixel_format(frame)
            dtype = str(array.dtype)
            stats = _array_stats(array)
            self._resolution = (int(frame.iWidth), int(frame.iHeight))
            self._exposure = float(frame.fExposure)
            self._gain = float(frame.fAGain)
            self._pixel_format = pixel_format
            self._frame_dtype = dtype
            self._last_frame_metadata = {
                "frameId": int(frame.uFrameID),
                "timestamp": int(frame.uTimestamp),
                "width": int(frame.iWidth),
                "height": int(frame.iHeight),
                "bytes": int(frame.uBytes),
                "format": int(frame.format),
                "formatName": image_format_name(frame.format),
                "bits": int(frame.bits),
                "pixelFormat": pixel_format,
                "exposure": self._exposure,
                "gain": self._gain,
                "dtype": dtype,
                **stats,
            }
            return CameraFrame(
                data=array,
                color_space="MONO" if array.ndim == 2 else "MULTI",
                dtype=dtype,
                shape=tuple(array.shape),
                metadata=dict(self._last_frame_metadata),
            )
        except Dvp2ApiError as exc:
            self._last_error = "多光谱相机取帧超时" if exc.status == DVP_STATUS_TIME_OUT else "多光谱相机取帧失败"
            self._last_technical_error = str(exc)
            error_type = CameraTimeoutError if exc.status == DVP_STATUS_TIME_OUT else CameraCaptureError
            raise error_type(self._last_error, self._last_technical_error) from exc
        except Dvp2BindingError as exc:
            self._last_error = "多光谱相机帧格式暂不支持"
            self._last_technical_error = str(exc)
            raise CameraCaptureError(self._last_error, self._last_technical_error) from exc

    def set_exposure(self, value: float) -> float:
        self.open()
        try:
            self._binding.set_exposure(self._handle, float(value))
            self._exposure = self._binding.get_exposure(self._handle)
            return self._exposure
        except Dvp2ApiError as exc:
            self._last_error = "多光谱相机曝光设置失败"
            self._last_technical_error = str(exc)
            raise CameraSettingUnsupported(self._last_error, self._last_technical_error) from exc

    def get_exposure(self) -> float | None:
        self.open()
        try:
            self._exposure = self._binding.get_exposure(self._handle)
            return self._exposure
        except Dvp2ApiError as exc:
            raise CameraSettingUnsupported("多光谱相机曝光读取失败", str(exc)) from exc

    def set_gain(self, value: float) -> float:
        self.open()
        try:
            self._binding.set_analog_gain(self._handle, float(value))
            self._gain = self._binding.get_analog_gain(self._handle)
            return self._gain
        except Dvp2ApiError as exc:
            self._last_error = "多光谱相机增益设置失败"
            self._last_technical_error = str(exc)
            raise CameraSettingUnsupported(self._last_error, self._last_technical_error) from exc

    def get_gain(self) -> float | None:
        self.open()
        try:
            self._gain = self._binding.get_analog_gain(self._handle)
            return self._gain
        except Dvp2ApiError as exc:
            raise CameraSettingUnsupported("多光谱相机增益读取失败", str(exc)) from exc

    def get_resolution(self) -> tuple[int, int] | None:
        self.open()
        try:
            _, _, width, height = self._binding.get_roi(self._handle)
            self._resolution = (width, height)
            return self._resolution
        except Dvp2ApiError as exc:
            raise CameraSettingUnsupported("多光谱相机分辨率读取失败", str(exc)) from exc

    def set_trigger_mode(self, mode: str | bool) -> None:
        self.open()
        enabled = bool(mode)
        if isinstance(mode, str):
            enabled = mode.strip().lower() not in {"", "off", "false", "0", "continuous"}
        try:
            self._binding.set_trigger_state(self._handle, enabled)
            if enabled:
                self._binding.set_trigger_source(self._handle)
            self._capabilities["triggerMode"] = "software" if enabled else "continuous"
        except Dvp2ApiError as exc:
            raise CameraSettingUnsupported("多光谱相机触发模式设置失败", str(exc)) from exc

    def software_trigger(self) -> None:
        self.open()
        try:
            self._binding.trigger_fire(self._handle)
        except Dvp2ApiError as exc:
            raise CameraCaptureError("多光谱相机软件触发失败", str(exc)) from exc

    def _ensure_binding(self) -> Any:
        info = self._sdk_info or find_dvp2_sdk(self.sdk_dir)
        self._sdk_info = info
        if not info.dll_path:
            self._last_error = "多光谱 GigE 相机 DVP2 SDK 尚未安装"
            self._last_technical_error = info.reason or "DVPCamera64.dll not found"
            raise CameraSdkUnavailableError(self._last_error, self._last_technical_error)
        if self._binding is None:
            try:
                if self._binding_factory:
                    self._binding = self._binding_factory(info.dll_path)
                else:
                    dll = self._loader(str(info.dll_path)) if self._loader else None
                    self._binding = Dvp2Binding(info.dll_path, dll=dll)
            except Exception as exc:
                self._last_error = "多光谱相机 SDK 加载失败"
                self._last_technical_error = str(exc)
                raise CameraSdkUnavailableError(self._last_error, self._last_technical_error) from exc
        return self._binding

    def _enum_devices(self, binding: Any) -> list[Dvp2DeviceInfo]:
        try:
            devices = list(binding.enum_devices())
            self._last_devices = devices
            if devices and not self._last_error:
                self._last_error = ""
                self._last_technical_error = ""
            return devices
        except Dvp2ApiError as exc:
            self._last_error = "DVP2 相机枚举失败"
            self._last_technical_error = str(exc)
            raise CameraUnavailableError(self._last_error, self._last_technical_error) from exc

    def _select_device(self, devices: list[Dvp2DeviceInfo]) -> Dvp2DeviceInfo:
        selected = self._select_device_or_none(devices)
        if selected:
            return selected
        if not devices:
            self._last_error = "DVP2 SDK 已加载，但未枚举到多光谱相机"
            self._last_technical_error = "dvpRefresh returned 0 devices"
            raise CameraUnavailableError(self._last_error, self._last_technical_error)
        self._last_error = "已枚举 DVP2 相机，但未匹配到配置的黑白相机"
        self._last_technical_error = (
            f"serial={self.serial_number}; friendlyName={self.friendly_name}; "
            f"deviceIndex={self.device_index}; devices={[device.to_dict() for device in devices]}"
        )
        raise CameraOpenError(self._last_error, self._last_technical_error)

    def _select_device_or_none(self, devices: list[Dvp2DeviceInfo]) -> Dvp2DeviceInfo | None:
        if not devices:
            return None
        for target in (self.serial_number, self.stable_id):
            if not target:
                continue
            for device in devices:
                if target in {_device_stable_id(device), device.serial_number, device.original_serial_number, device.user_id}:
                    return device
        if self.friendly_name:
            for device in devices:
                if device.friendly_name == self.friendly_name:
                    return device
        if self.device_index is not None:
            for device in devices:
                if device.index == self.device_index:
                    return device
        if not self.serial_number and len(devices) == 1:
            return devices[0]
        return None

    def _merge_open_device_info(self, binding: Any, fallback: Dvp2DeviceInfo) -> Dvp2DeviceInfo:
        try:
            opened = binding.get_camera_info(self._handle)
            return Dvp2DeviceInfo(
                index=fallback.index,
                vendor=opened.vendor or fallback.vendor,
                manufacturer=opened.manufacturer or fallback.manufacturer,
                model=opened.model or fallback.model,
                family=opened.family or fallback.family,
                link_name=opened.link_name or fallback.link_name,
                sensor_info=opened.sensor_info or fallback.sensor_info,
                friendly_name=opened.friendly_name or fallback.friendly_name,
                port_info=opened.port_info or fallback.port_info,
                serial_number=opened.serial_number or fallback.serial_number,
                camera_info=opened.camera_info or fallback.camera_info,
                user_id=opened.user_id or fallback.user_id,
                original_serial_number=opened.original_serial_number or fallback.original_serial_number,
            )
        except Exception:
            return fallback

    def _refresh_open_status(self, binding: Any) -> None:
        if not self.is_open:
            return
        handle = self._handle
        try:
            _, _, width, height = binding.get_roi(handle)
            self._resolution = (width, height)
        except Exception:
            pass
        try:
            self._exposure = binding.get_exposure(handle)
        except Exception:
            pass
        try:
            self._gain = binding.get_analog_gain(handle)
        except Exception:
            pass
        try:
            trigger_enabled = binding.get_trigger_state(handle)
            self._capabilities["triggerMode"] = "trigger" if trigger_enabled else "continuous"
        except Exception:
            pass
        try:
            self._capabilities["exposure"] = binding.get_exposure_descr(handle)
        except Exception:
            pass
        try:
            self._capabilities["gain"] = binding.get_analog_gain_descr(handle)
        except Exception:
            pass
        try:
            frame_count = binding.get_frame_count(handle)
            self._capabilities["frameCount"] = frame_count
        except Exception:
            pass
        source_format = None
        target_format = None
        try:
            source_format = binding.get_source_format(handle)
        except Exception:
            pass
        try:
            target_format = binding.get_target_format(handle)
        except Exception:
            pass
        if source_format is not None or target_format is not None:
            self._capabilities["streamFormat"] = {
                "source": source_format,
                "target": target_format,
            }
            pixel, dtype = _stream_format_to_pixel_dtype(target_format)
            if pixel:
                self._pixel_format = pixel
            if dtype:
                self._frame_dtype = dtype

    def _actual_dict(self, device: Dvp2DeviceInfo | None) -> dict[str, Any]:
        width = self._resolution[0] if self._resolution else None
        height = self._resolution[1] if self._resolution else None
        frame_count = (self._capabilities.get("frameCount") or {}) if isinstance(self._capabilities, dict) else {}
        stream_fps = frame_count.get("frameRate") if isinstance(frame_count, dict) else None
        return {
            "vendor": device.vendor if device else "",
            "manufacturer": device.manufacturer if device else "",
            "model": device.model if device else "",
            "friendlyName": device.friendly_name if device else "",
            "cameraSerial": device.serial_number if device else self.serial_number,
            "originalSerialNumber": device.original_serial_number if device else "",
            "userId": device.user_id if device else "",
            "cameraIp": _best_ip_text(device),
            "hostAdapterIp": "",
            "cameraMac": _best_mac_text(device),
            "linkSpeed": "",
            "linkSpeedMbps": None,
            "streamFps": float(stream_fps) if isinstance(stream_fps, (int, float)) else None,
            "portInfo": device.port_info if device else "",
            "cameraInfo": device.camera_info if device else "",
            "width": width,
            "height": height,
            "exposure": self._exposure,
            "gain": self._gain,
            "pixelFormat": self._pixel_format,
            "frameDtype": self._frame_dtype,
            "supportedPixelFormats": list(self._capabilities.get("supportedPixelFormats") or []),
            "lastFrame": dict(self._last_frame_metadata),
        }

    def _probe_available_subprocess(self, timeout_seconds: float = 8.0) -> bool:
        info = self._sdk_info or find_dvp2_sdk(self.sdk_dir)
        self._sdk_info = info
        if not info.dll_path:
            self._last_error = "多光谱 GigE 相机 DVP2 SDK 尚未安装"
            self._last_technical_error = info.reason or "DVPCamera64.dll not found"
            return False
        selected: Dvp2DeviceInfo | None = None
        enum_payload = self._enum_target_subprocess(info, timeout_seconds=min(timeout_seconds, 3.0))
        if not enum_payload.get("ok"):
            self._last_error = enum_payload.get("error") or "DVP2 SDK 已加载，但未枚举到多光谱相机"
            self._last_technical_error = enum_payload.get("technicalError") or "dvpRefresh/dvpEnum returned no target device"
            self._available = False
            return False
        selected = _device_from_payload(enum_payload.get("device") or {})
        if selected:
            self._selected_device = selected
        code = f"""
import json
from camera_service.dvp2_binding import Dvp2Binding

b = Dvp2Binding({str(info.dll_path)!r})
ds = b.enum_devices()
target = {self.serial_number!r}
stable = {self.stable_id!r}
friendly = {self.friendly_name!r}
index = {self.device_index!r}

def sid(d):
    return d.serial_number or d.original_serial_number or d.user_id or d.friendly_name

dev = next((d for d in ds if target and target in {{sid(d), d.serial_number, d.original_serial_number, d.user_id}}), None)
dev = dev or next((d for d in ds if stable and stable in {{sid(d), d.serial_number, d.original_serial_number, d.user_id}}), None)
dev = dev or next((d for d in ds if friendly and d.friendly_name == friendly), None)
dev = dev or next((d for d in ds if index is not None and d.index == index), None)
dev = dev or (ds[0] if len(ds) == 1 and not target else None)
result = {{"ok": False, "device": dev.to_dict() if dev else None}}

if dev:
    h = b.open_by_user_id(dev.user_id) if dev.user_id else (b.open_by_name(dev.friendly_name) if dev.friendly_name else b.open_by_index(dev.index))
    result.update({{"ok": True, "roi": b.get_roi(h), "exposure": b.get_exposure(h), "gain": b.get_analog_gain(h)}})
    result["triggerState"] = b.get_trigger_state(h)
    result["exposureDescr"] = b.get_exposure_descr(h)
    result["gainDescr"] = b.get_analog_gain_descr(h)
    result["frameCount"] = b.get_frame_count(h)
    result["sourceFormat"] = b.get_source_format(h)
    result["targetFormat"] = b.get_target_format(h)
    b.close(h)

print(json.dumps(result, ensure_ascii=False), flush=True)
"""
        try:
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", "-c", code],
                cwd=str(Path(__file__).parents[1]),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._last_error = _friendly_open_error(selected)
            self._last_technical_error = f"dvpOpenByName/dvpOpenByUserId did not return within {timeout_seconds:g}s"
            self._available = False
            return False
        if completed.returncode != 0:
            self._last_error = _friendly_open_error(selected) if selected else "多光谱相机探测失败"
            self._last_technical_error = (completed.stderr or completed.stdout or "").strip()
            self._available = False
            return False
        try:
            payload = json.loads((completed.stdout or "").strip().splitlines()[-1])
        except Exception as exc:
            self._last_error = "多光谱相机探测结果解析失败"
            self._last_technical_error = str(exc)
            self._available = False
            return False
        device_payload = payload.get("device") or {}
        if device_payload:
            self._selected_device = Dvp2DeviceInfo(
                index=int(device_payload.get("index", -1)),
                vendor=str(device_payload.get("vendor") or ""),
                manufacturer=str(device_payload.get("manufacturer") or ""),
                model=str(device_payload.get("model") or ""),
                family=str(device_payload.get("family") or ""),
                link_name=str(device_payload.get("linkName") or ""),
                sensor_info=str(device_payload.get("sensorInfo") or ""),
                friendly_name=str(device_payload.get("friendlyName") or ""),
                port_info=str(device_payload.get("portInfo") or ""),
                serial_number=str(device_payload.get("serialNumber") or ""),
                camera_info=str(device_payload.get("cameraInfo") or ""),
                user_id=str(device_payload.get("userId") or ""),
                original_serial_number=str(device_payload.get("originalSerialNumber") or ""),
            )
        if not payload.get("ok"):
            self._last_error = "DVP2 SDK 已加载，但未枚举到多光谱相机"
            self._last_technical_error = "subprocess probe returned ok=false"
            self._available = False
            return False
        roi = payload.get("roi") or []
        if len(roi) == 4:
            self._resolution = (int(roi[2]), int(roi[3]))
        self._exposure = float(payload["exposure"]) if payload.get("exposure") is not None else self._exposure
        self._gain = float(payload["gain"]) if payload.get("gain") is not None else self._gain
        self._capabilities["triggerMode"] = "trigger" if payload.get("triggerState") else "continuous"
        if isinstance(payload.get("exposureDescr"), dict):
            self._capabilities["exposure"] = payload["exposureDescr"]
        if isinstance(payload.get("gainDescr"), dict):
            self._capabilities["gain"] = payload["gainDescr"]
        if isinstance(payload.get("frameCount"), dict):
            self._capabilities["frameCount"] = payload["frameCount"]
        source_format = payload.get("sourceFormat")
        target_format = payload.get("targetFormat")
        if source_format is not None or target_format is not None:
            self._capabilities["streamFormat"] = {"source": source_format, "target": target_format}
            pixel, dtype = _stream_format_to_pixel_dtype(target_format)
            if pixel:
                self._pixel_format = pixel
            if dtype:
                self._frame_dtype = dtype
        self._available = True
        self._last_error = ""
        self._last_technical_error = ""
        return True

    def _enum_target_subprocess(self, info: Dvp2SdkInfo, timeout_seconds: float) -> dict[str, Any]:
        code = f"""
import json
from camera_service.dvp2_binding import Dvp2Binding

b = Dvp2Binding({str(info.dll_path)!r})
ds = b.enum_devices()
target = {self.serial_number!r}
stable = {self.stable_id!r}
friendly = {self.friendly_name!r}
index = {self.device_index!r}

def sid(d):
    return d.serial_number or d.original_serial_number or d.user_id or d.friendly_name

dev = next((d for d in ds if target and target in {{sid(d), d.serial_number, d.original_serial_number, d.user_id}}), None)
dev = dev or next((d for d in ds if stable and stable in {{sid(d), d.serial_number, d.original_serial_number, d.user_id}}), None)
dev = dev or next((d for d in ds if friendly and d.friendly_name == friendly), None)
dev = dev or next((d for d in ds if index is not None and d.index == index), None)
dev = dev or (ds[0] if len(ds) == 1 and not target else None)
result = {{"ok": bool(dev), "device": dev.to_dict() if dev else None, "deviceCount": len(ds), "devices": [d.to_dict() for d in ds]}}
if ds and not dev:
    result["error"] = "已枚举 DVP2 相机，但未匹配到配置的黑白相机"
if not ds:
    result["error"] = "DVP2 SDK 已加载，但未枚举到多光谱相机"
print(json.dumps(result, ensure_ascii=False), flush=True)
"""
        try:
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", "-c", code],
                cwd=str(Path(__file__).parents[1]),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": "DVP2 相机枚举超时",
                "technicalError": f"dvpRefresh/dvpEnum did not return within {timeout_seconds:g}s",
            }
        if completed.returncode != 0:
            return {
                "ok": False,
                "error": "DVP2 相机枚举失败",
                "technicalError": (completed.stderr or completed.stdout or "").strip(),
            }
        try:
            return json.loads((completed.stdout or "").strip().splitlines()[-1])
        except Exception as exc:
            return {
                "ok": False,
                "error": "DVP2 相机枚举结果解析失败",
                "technicalError": str(exc),
            }


def _candidate_dll_paths(root: Path) -> list[Path]:
    known = [
        root / "DVPCamera64.dll",
        root / "bin" / "x64" / "DVPCamera64.dll",
        root / "bin" / "win64" / "DVPCamera64.dll",
        root / "x64" / "DVPCamera64.dll",
        root / "library" / "Visual C++" / "bin" / "x64" / "DVPCamera64.dll",
        root / "library" / "Visual C++" / "bin" / "win64" / "DVPCamera64.dll",
    ]
    try:
        for child in root.iterdir():
            if child.is_dir():
                known.append(child / "DVPCamera64.dll")
    except Exception:
        pass
    return known


def _device_stable_id(device: Dvp2DeviceInfo | None) -> str:
    if not device:
        return ""
    return device.serial_number or device.original_serial_number or device.user_id or device.friendly_name


def _device_from_payload(payload: dict[str, Any]) -> Dvp2DeviceInfo | None:
    if not payload:
        return None
    return Dvp2DeviceInfo(
        index=int(payload.get("index", -1)),
        vendor=str(payload.get("vendor") or ""),
        manufacturer=str(payload.get("manufacturer") or ""),
        model=str(payload.get("model") or ""),
        family=str(payload.get("family") or ""),
        link_name=str(payload.get("linkName") or ""),
        sensor_info=str(payload.get("sensorInfo") or ""),
        friendly_name=str(payload.get("friendlyName") or ""),
        port_info=str(payload.get("portInfo") or ""),
        serial_number=str(payload.get("serialNumber") or ""),
        camera_info=str(payload.get("cameraInfo") or ""),
        user_id=str(payload.get("userId") or ""),
        original_serial_number=str(payload.get("originalSerialNumber") or ""),
    )


def _open_device(binding: Any, device: Dvp2DeviceInfo, *, auto_ip: bool) -> int:
    if device.user_id:
        return binding.open_by_user_id(device.user_id, auto_ip=auto_ip)
    if device.friendly_name:
        return binding.open_by_name(device.friendly_name, auto_ip=auto_ip)
    return binding.open_by_index(device.index, auto_ip=auto_ip)


def _best_ip_text(device: Dvp2DeviceInfo | None) -> str:
    if not device:
        return ""
    for text in (device.port_info, device.camera_info, device.link_name, device.friendly_name):
        ip = _extract_ipv4(text)
        if ip:
            return ip
    return ""


def _best_mac_text(device: Dvp2DeviceInfo | None) -> str:
    if not device:
        return ""
    for text in (device.camera_info, device.port_info, device.link_name, device.friendly_name):
        mac = _extract_mac(text)
        if mac:
            return mac
    return ""


def _extract_ipv4(text: str) -> str:
    for match in re.finditer(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)", str(text or "")):
        value = match.group(0)
        if all(0 <= int(part) <= 255 for part in value.split(".")):
            return value
    return ""


def _extract_mac(text: str) -> str:
    match = re.search(r"(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}", str(text or ""))
    return match.group(0).upper().replace(":", "-") if match else ""


def _stream_format_to_pixel_dtype(value: Any) -> tuple[str, str]:
    mapping = {
        30: ("Mono8", "uint8"),
        31: ("Mono10", "uint16"),
        32: ("Mono12", "uint16"),
        33: ("Mono14", "uint16"),
        34: ("Mono16", "uint16"),
    }
    try:
        return mapping.get(int(value), ("", ""))
    except (TypeError, ValueError):
        return "", ""


def _array_stats(array: Any) -> dict[str, Any]:
    try:
        import numpy as np

        values = np.asarray(array)
        if values.size == 0:
            return {"frameMin": None, "frameMax": None, "frameMean": None}
        return {
            "frameMin": float(np.min(values)),
            "frameMax": float(np.max(values)),
            "frameMean": float(np.mean(values)),
        }
    except Exception:
        return {"frameMin": None, "frameMax": None, "frameMean": None}


def _friendly_open_error(device: Dvp2DeviceInfo | None) -> str:
    if device:
        return "已发现多光谱相机，但无法打开。请确认 BasedCam3 或其他相机程序已关闭，然后重试。"
    return "多光谱相机打开失败"


def _optional_int(value: int | None, env_value: str | None) -> int | None:
    if value is not None:
        return int(value)
    if env_value is None or str(env_value).strip() == "":
        return None
    try:
        return int(env_value)
    except ValueError:
        return None
