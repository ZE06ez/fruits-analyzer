from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DVP_STATUS_OK = 1
DVP_STATUS_TIME_OUT = -1000

OPEN_NORMAL = 1 << 0
OPEN_AUTOIP = 1 << 5

FORMAT_MONO = 0
FORMAT_BAYER_BG = 1
FORMAT_BAYER_GB = 2
FORMAT_BAYER_GR = 3
FORMAT_BAYER_RG = 4
FORMAT_BGR24 = 10
FORMAT_BGR32 = 11
FORMAT_BGR48 = 12
FORMAT_BGR64 = 13
FORMAT_RGB24 = 14
FORMAT_RGB32 = 15
FORMAT_RGB48 = 16
FORMAT_RGB64 = 17

BITS_8 = 0
BITS_10 = 1
BITS_12 = 2
BITS_14 = 3
BITS_16 = 4

STATE_STOPPED = 0
STATE_STARTED = 2

TRIGGER_SOURCE_SOFTWARE = 0


STATUS_NAMES = {
    1: "DVP_STATUS_OK",
    0: "DVP_STATUS_FAILED",
    -1: "DVP_STATUS_UNKNOW",
    -2: "DVP_STATUS_NOT_SUPPORTED",
    -3: "DVP_STATUS_NOT_INITIALIZED",
    -4: "DVP_STATUS_PARAMETER_INVALID",
    -5: "DVP_STATUS_PARAMETER_OUT_OF_BOUND",
    -6: "DVP_STATUS_UNENABLED",
    -7: "DVP_STATUS_UNCONNECTED",
    -8: "DVP_STATUS_NOT_VALID",
    -9: "DVP_STATUS_UNPLAY",
    -10: "DVP_STATUS_NOT_STARTED",
    -11: "DVP_STATUS_NOT_STOPPED",
    -12: "DVP_STATUS_NOT_READY",
    -13: "DVP_STATUS_INVALID_HANDLE",
    -1000: "DVP_STATUS_TIME_OUT",
    -1001: "DVP_STATUS_IO_ERROR",
    -1002: "DVP_STATUS_COMM_ERROR",
    -1003: "DVP_STATUS_BUS_ERROR",
    -1004: "DVP_STATUS_FORMAT_INVALID",
    -1100: "DVP_STATUS_NO_DEVICE_FOUND",
    -1102: "DVP_STATUS_DEVICE_IS_OPENED",
    -1105: "DVP_STATUS_DEVICE_IS_OPENED_BY_ANOTHER",
    -1106: "DVP_STATUS_DEVICE_IS_STARTED",
}

BITS_NAMES = {
    BITS_8: "BITS_8",
    BITS_10: "BITS_10",
    BITS_12: "BITS_12",
    BITS_14: "BITS_14",
    BITS_16: "BITS_16",
}

FORMAT_NAMES = {
    FORMAT_MONO: "FORMAT_MONO",
    FORMAT_BAYER_BG: "FORMAT_BAYER_BG",
    FORMAT_BAYER_GB: "FORMAT_BAYER_GB",
    FORMAT_BAYER_GR: "FORMAT_BAYER_GR",
    FORMAT_BAYER_RG: "FORMAT_BAYER_RG",
    FORMAT_BGR24: "FORMAT_BGR24",
    FORMAT_BGR32: "FORMAT_BGR32",
    FORMAT_BGR48: "FORMAT_BGR48",
    FORMAT_BGR64: "FORMAT_BGR64",
    FORMAT_RGB24: "FORMAT_RGB24",
    FORMAT_RGB32: "FORMAT_RGB32",
    FORMAT_RGB48: "FORMAT_RGB48",
    FORMAT_RGB64: "FORMAT_RGB64",
}


dvpByte = ctypes.c_uint8
dvpHandle = ctypes.c_uint32


class Dvp2ApiError(RuntimeError):
    def __init__(self, action: str, status: int) -> None:
        self.action = action
        self.status = int(status)
        self.status_name = STATUS_NAMES.get(self.status, f"DVP_STATUS_{self.status}")
        super().__init__(f"{action} failed: {self.status_name} ({self.status})")


class Dvp2BindingError(RuntimeError):
    pass


class dvpCameraInfo(ctypes.Structure):
    _fields_ = [
        ("Vendor", ctypes.c_char * 64),
        ("Manufacturer", ctypes.c_char * 64),
        ("Model", ctypes.c_char * 64),
        ("Family", ctypes.c_char * 64),
        ("LinkName", ctypes.c_char * 64),
        ("SensorInfo", ctypes.c_char * 64),
        ("HardwareVersion", ctypes.c_char * 64),
        ("FirmwareVersion", ctypes.c_char * 64),
        ("KernelVersion", ctypes.c_char * 64),
        ("DscamVersion", ctypes.c_char * 64),
        ("FriendlyName", ctypes.c_char * 64),
        ("PortInfo", ctypes.c_char * 64),
        ("SerialNumber", ctypes.c_char * 64),
        ("CameraInfo", ctypes.c_char * 128),
        ("UserID", ctypes.c_char * 128),
        ("OriginalSerialNumber", ctypes.c_char * 64),
        ("reserved", ctypes.c_char * 64),
    ]


class dvpRegion(ctypes.Structure):
    _fields_ = [
        ("X", ctypes.c_int32),
        ("Y", ctypes.c_int32),
        ("W", ctypes.c_int32),
        ("H", ctypes.c_int32),
        ("reserved", ctypes.c_uint32 * 32),
    ]


class dvpFrame(ctypes.Structure):
    _fields_ = [
        ("format", ctypes.c_int),
        ("bits", ctypes.c_int),
        ("uBytes", ctypes.c_uint32),
        ("iWidth", ctypes.c_int32),
        ("iHeight", ctypes.c_int32),
        ("uFrameID", ctypes.c_uint64),
        ("uTimestamp", ctypes.c_uint64),
        ("fExposure", ctypes.c_double),
        ("fAGain", ctypes.c_float),
        ("position", ctypes.c_int),
        ("bFlipHorizontalState", ctypes.c_bool),
        ("bFlipVerticalState", ctypes.c_bool),
        ("bRotateState", ctypes.c_bool),
        ("bRotateOpposite", ctypes.c_bool),
        ("internalFlags", ctypes.c_uint32),
        ("internalValue", ctypes.c_uint32),
        ("uTriggerId", ctypes.c_uint64),
        ("uLineLevelStatus", ctypes.c_uint16),
        ("reserved1", dvpByte * 6),
        ("pExtra", ctypes.c_void_p),
        ("reserved2", ctypes.c_uint32 * 22),
    ]


class dvpFrameCount(ctypes.Structure):
    _fields_ = [
        ("uFrameCount", ctypes.c_uint32),
        ("uFrameDrop", ctypes.c_uint32),
        ("uFrameIgnore", ctypes.c_uint32),
        ("uFrameError", ctypes.c_uint32),
        ("uFrameOK", ctypes.c_uint32),
        ("uFrameOut", ctypes.c_uint32),
        ("uFrameResend", ctypes.c_uint32),
        ("uFrameProc", ctypes.c_uint32),
        ("fFrameRate", ctypes.c_float),
        ("fProcRate", ctypes.c_float),
        ("reserved", ctypes.c_uint32 * 32),
    ]


class dvpFloatDescr(ctypes.Structure):
    _fields_ = [
        ("fStep", ctypes.c_float),
        ("fMin", ctypes.c_float),
        ("fMax", ctypes.c_float),
        ("fDefault", ctypes.c_float),
        ("reserved", ctypes.c_uint32 * 32),
    ]


class dvpDoubleDescr(ctypes.Structure):
    _fields_ = [
        ("fStep", ctypes.c_double),
        ("fMin", ctypes.c_double),
        ("fMax", ctypes.c_double),
        ("fDefault", ctypes.c_double),
        ("reserved", ctypes.c_uint32 * 32),
    ]


@dataclass(frozen=True)
class Dvp2DeviceInfo:
    index: int
    vendor: str = ""
    manufacturer: str = ""
    model: str = ""
    family: str = ""
    link_name: str = ""
    sensor_info: str = ""
    friendly_name: str = ""
    port_info: str = ""
    serial_number: str = ""
    camera_info: str = ""
    user_id: str = ""
    original_serial_number: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "vendor": self.vendor,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "family": self.family,
            "linkName": self.link_name,
            "sensorInfo": self.sensor_info,
            "friendlyName": self.friendly_name,
            "portInfo": self.port_info,
            "serialNumber": self.serial_number,
            "cameraInfo": self.camera_info,
            "userId": self.user_id,
            "originalSerialNumber": self.original_serial_number,
        }


def status_name(status: int) -> str:
    return STATUS_NAMES.get(int(status), f"DVP_STATUS_{int(status)}")


def image_format_name(value: int) -> str:
    return FORMAT_NAMES.get(int(value), f"FORMAT_{int(value)}")


def bits_name(value: int) -> str:
    return BITS_NAMES.get(int(value), f"BITS_{int(value)}")


def frame_pixel_format(frame: dvpFrame) -> str:
    if frame.format == FORMAT_MONO:
        return f"Mono{_bits_suffix(frame.bits)}"
    if FORMAT_BAYER_BG <= frame.format <= FORMAT_BAYER_RG:
        return f"Bayer{_bits_suffix(frame.bits)}"
    return image_format_name(frame.format).removeprefix("FORMAT_")


def frame_channels(frame_format: int) -> int:
    if FORMAT_MONO <= int(frame_format) <= FORMAT_BAYER_RG:
        return 1
    if int(frame_format) in {FORMAT_BGR24, FORMAT_RGB24, FORMAT_BGR48, FORMAT_RGB48}:
        return 3
    if int(frame_format) in {FORMAT_BGR32, FORMAT_RGB32, FORMAT_BGR64, FORMAT_RGB64}:
        return 4
    raise Dvp2BindingError(f"Unsupported DVP2 frame format: {image_format_name(frame_format)}")


def frame_dtype(frame_bits: int) -> Any:
    import numpy as np

    return np.uint8 if int(frame_bits) == BITS_8 else np.uint16


def frame_to_array(frame: dvpFrame, buffer_ptr: int | ctypes.c_void_p) -> Any:
    import numpy as np

    width = int(frame.iWidth)
    height = int(frame.iHeight)
    channels = frame_channels(frame.format)
    dtype = frame_dtype(frame.bits)
    ptr = int(buffer_ptr.value) if isinstance(buffer_ptr, ctypes.c_void_p) else int(buffer_ptr)
    if ptr == 0:
        raise Dvp2BindingError("DVP2 returned an empty frame buffer")
    itemsize = np.dtype(dtype).itemsize
    pixel_count = width * height * channels
    expected_bytes = pixel_count * itemsize
    available_bytes = int(frame.uBytes)
    if available_bytes < expected_bytes:
        raise Dvp2BindingError(
            f"Packed DVP2 frame is unsupported: {available_bytes} bytes for "
            f"{width}x{height}x{channels} {bits_name(frame.bits)}"
        )
    raw = ctypes.string_at(ptr, expected_bytes)
    array = np.frombuffer(raw, dtype=dtype).copy()
    if channels == 1:
        return array.reshape(height, width)
    return array.reshape(height, width, channels)


class Dvp2Binding:
    """Minimal ctypes wrapper around the DVP2 C API confirmed from DVPCamera.h."""

    def __init__(self, dll_path: str | os.PathLike[str], *, dll: Any | None = None) -> None:
        self.dll_path = Path(dll_path)
        self._dll_dir_handle = None
        if dll is None:
            dll_dir = str(self.dll_path.parent)
            if hasattr(os, "add_dll_directory"):
                self._dll_dir_handle = os.add_dll_directory(dll_dir)
            dll = ctypes.CDLL(str(self.dll_path))
        self.dll = dll
        self._configure_signatures()

    def refresh(self) -> int:
        count = ctypes.c_uint32()
        self._check(self.dll.dvpRefresh(ctypes.byref(count)), "dvpRefresh")
        return int(count.value)

    def enum_devices(self) -> list[Dvp2DeviceInfo]:
        count = self.refresh()
        result: list[Dvp2DeviceInfo] = []
        for index in range(count):
            info = dvpCameraInfo()
            self._check(self.dll.dvpEnum(ctypes.c_uint32(index), ctypes.byref(info)), "dvpEnum")
            result.append(_camera_info_to_device(index, info))
        return result

    def open_by_name(self, friendly_name: str, *, auto_ip: bool = False) -> int:
        handle = dvpHandle()
        mode = OPEN_NORMAL | (OPEN_AUTOIP if auto_ip else 0)
        self._check(
            self.dll.dvpOpenByName(_to_bytes(friendly_name), ctypes.c_int(mode), ctypes.byref(handle)),
            "dvpOpenByName",
        )
        return int(handle.value)

    def open_by_user_id(self, user_id: str, *, auto_ip: bool = False) -> int:
        handle = dvpHandle()
        mode = OPEN_NORMAL | (OPEN_AUTOIP if auto_ip else 0)
        self._check(
            self.dll.dvpOpenByUserId(_to_bytes(user_id), ctypes.c_int(mode), ctypes.byref(handle)),
            "dvpOpenByUserId",
        )
        return int(handle.value)

    def open_by_index(self, index: int, *, auto_ip: bool = False) -> int:
        handle = dvpHandle()
        mode = OPEN_NORMAL | (OPEN_AUTOIP if auto_ip else 0)
        self._check(
            self.dll.dvpOpen(ctypes.c_uint32(index), ctypes.c_int(mode), ctypes.byref(handle)),
            "dvpOpen",
        )
        return int(handle.value)

    def close(self, handle: int) -> None:
        self._check(self.dll.dvpClose(dvpHandle(handle)), "dvpClose")

    def start(self, handle: int) -> None:
        self._check(self.dll.dvpStart(dvpHandle(handle)), "dvpStart")

    def stop(self, handle: int) -> None:
        self._check(self.dll.dvpStop(dvpHandle(handle)), "dvpStop")

    def get_frame(self, handle: int, timeout_ms: int = 3000) -> tuple[dvpFrame, ctypes.c_void_p]:
        frame = dvpFrame()
        buffer_ptr = ctypes.c_void_p()
        self._check(
            self.dll.dvpGetFrame(
                dvpHandle(handle),
                ctypes.byref(frame),
                ctypes.byref(buffer_ptr),
                ctypes.c_uint32(max(1, int(timeout_ms))),
            ),
            "dvpGetFrame",
        )
        return frame, buffer_ptr

    def get_camera_info(self, handle: int) -> Dvp2DeviceInfo:
        info = dvpCameraInfo()
        self._check(self.dll.dvpGetCameraInfo(dvpHandle(handle), ctypes.byref(info)), "dvpGetCameraInfo")
        return _camera_info_to_device(-1, info)

    def get_frame_count(self, handle: int) -> dict[str, Any]:
        count = dvpFrameCount()
        self._check(self.dll.dvpGetFrameCount(dvpHandle(handle), ctypes.byref(count)), "dvpGetFrameCount")
        return {
            "frameCount": int(count.uFrameCount),
            "frameDrop": int(count.uFrameDrop),
            "frameError": int(count.uFrameError),
            "frameOk": int(count.uFrameOK),
            "frameOut": int(count.uFrameOut),
            "frameResend": int(count.uFrameResend),
            "frameRate": float(count.fFrameRate),
            "procRate": float(count.fProcRate),
        }

    def get_roi(self, handle: int) -> tuple[int, int, int, int]:
        region = dvpRegion()
        self._check(self.dll.dvpGetRoi(dvpHandle(handle), ctypes.byref(region)), "dvpGetRoi")
        return int(region.X), int(region.Y), int(region.W), int(region.H)

    def get_exposure(self, handle: int) -> float:
        value = ctypes.c_double()
        self._check(self.dll.dvpGetExposure(dvpHandle(handle), ctypes.byref(value)), "dvpGetExposure")
        return float(value.value)

    def set_exposure(self, handle: int, value: float) -> None:
        self._check(self.dll.dvpSetExposure(dvpHandle(handle), ctypes.c_double(float(value))), "dvpSetExposure")

    def get_exposure_descr(self, handle: int) -> dict[str, float]:
        descr = dvpDoubleDescr()
        self._check(self.dll.dvpGetExposureDescr(dvpHandle(handle), ctypes.byref(descr)), "dvpGetExposureDescr")
        return _double_descr_to_dict(descr)

    def get_analog_gain(self, handle: int) -> float:
        value = ctypes.c_float()
        self._check(self.dll.dvpGetAnalogGain(dvpHandle(handle), ctypes.byref(value)), "dvpGetAnalogGain")
        return float(value.value)

    def set_analog_gain(self, handle: int, value: float) -> None:
        self._check(self.dll.dvpSetAnalogGain(dvpHandle(handle), ctypes.c_float(float(value))), "dvpSetAnalogGain")

    def get_analog_gain_descr(self, handle: int) -> dict[str, float]:
        descr = dvpFloatDescr()
        self._check(self.dll.dvpGetAnalogGainDescr(dvpHandle(handle), ctypes.byref(descr)), "dvpGetAnalogGainDescr")
        return _float_descr_to_dict(descr)

    def get_trigger_state(self, handle: int) -> bool:
        value = ctypes.c_bool()
        self._check(self.dll.dvpGetTriggerState(dvpHandle(handle), ctypes.byref(value)), "dvpGetTriggerState")
        return bool(value.value)

    def set_trigger_state(self, handle: int, enabled: bool) -> None:
        self._check(self.dll.dvpSetTriggerState(dvpHandle(handle), ctypes.c_bool(bool(enabled))), "dvpSetTriggerState")

    def set_trigger_source(self, handle: int, source: int = TRIGGER_SOURCE_SOFTWARE) -> None:
        self._check(self.dll.dvpSetTriggerSource(dvpHandle(handle), ctypes.c_int(int(source))), "dvpSetTriggerSource")

    def trigger_fire(self, handle: int) -> None:
        self._check(self.dll.dvpTriggerFire(dvpHandle(handle)), "dvpTriggerFire")

    def get_stream_state(self, handle: int) -> int:
        value = ctypes.c_int()
        self._check(self.dll.dvpGetStreamState(dvpHandle(handle), ctypes.byref(value)), "dvpGetStreamState")
        return int(value.value)

    def get_source_format(self, handle: int) -> int | None:
        return self._get_int_optional(handle, "dvpGetSourceFormat")

    def get_target_format(self, handle: int) -> int | None:
        return self._get_int_optional(handle, "dvpGetTargetFormat")

    def get_string_value(self, handle: int, key: str, *, size: int = 256) -> str:
        buffer = ctypes.create_string_buffer(size)
        self._check(
            self.dll.dvpGetStringValue(dvpHandle(handle), _to_bytes(key), buffer, ctypes.c_uint32(size)),
            "dvpGetStringValue",
        )
        return _decode_bytes(buffer.raw)

    def get_config_string(self, handle: int, key: str, *, size: int = 256) -> str:
        buffer = ctypes.create_string_buffer(size)
        self._check(
            self.dll.dvpGetConfigString(dvpHandle(handle), _to_bytes(key), buffer, ctypes.c_uint32(size)),
            "dvpGetConfigString",
        )
        return _decode_bytes(buffer.raw)

    def _get_int_optional(self, handle: int, function_name: str) -> int | None:
        function = getattr(self.dll, function_name, None)
        if function is None:
            return None
        value = ctypes.c_int()
        self._check(function(dvpHandle(handle), ctypes.byref(value)), function_name)
        return int(value.value)

    @staticmethod
    def _check(status: int, action: str) -> None:
        if int(status) != DVP_STATUS_OK:
            raise Dvp2ApiError(action, int(status))

    def _configure_signatures(self) -> None:
        specs = {
            "dvpRefresh": ([ctypes.POINTER(ctypes.c_uint32)], ctypes.c_int),
            "dvpEnum": ([ctypes.c_uint32, ctypes.POINTER(dvpCameraInfo)], ctypes.c_int),
            "dvpOpenByName": ([ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(dvpHandle)], ctypes.c_int),
            "dvpOpenByUserId": ([ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(dvpHandle)], ctypes.c_int),
            "dvpOpen": ([ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(dvpHandle)], ctypes.c_int),
            "dvpClose": ([dvpHandle], ctypes.c_int),
            "dvpStart": ([dvpHandle], ctypes.c_int),
            "dvpStop": ([dvpHandle], ctypes.c_int),
            "dvpGetFrame": ([dvpHandle, ctypes.POINTER(dvpFrame), ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32], ctypes.c_int),
            "dvpGetCameraInfo": ([dvpHandle, ctypes.POINTER(dvpCameraInfo)], ctypes.c_int),
            "dvpGetFrameCount": ([dvpHandle, ctypes.POINTER(dvpFrameCount)], ctypes.c_int),
            "dvpGetRoi": ([dvpHandle, ctypes.POINTER(dvpRegion)], ctypes.c_int),
            "dvpGetExposure": ([dvpHandle, ctypes.POINTER(ctypes.c_double)], ctypes.c_int),
            "dvpSetExposure": ([dvpHandle, ctypes.c_double], ctypes.c_int),
            "dvpGetExposureDescr": ([dvpHandle, ctypes.POINTER(dvpDoubleDescr)], ctypes.c_int),
            "dvpGetAnalogGain": ([dvpHandle, ctypes.POINTER(ctypes.c_float)], ctypes.c_int),
            "dvpSetAnalogGain": ([dvpHandle, ctypes.c_float], ctypes.c_int),
            "dvpGetAnalogGainDescr": ([dvpHandle, ctypes.POINTER(dvpFloatDescr)], ctypes.c_int),
            "dvpGetTriggerState": ([dvpHandle, ctypes.POINTER(ctypes.c_bool)], ctypes.c_int),
            "dvpSetTriggerState": ([dvpHandle, ctypes.c_bool], ctypes.c_int),
            "dvpSetTriggerSource": ([dvpHandle, ctypes.c_int], ctypes.c_int),
            "dvpTriggerFire": ([dvpHandle], ctypes.c_int),
            "dvpGetStreamState": ([dvpHandle, ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
            "dvpGetSourceFormat": ([dvpHandle, ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
            "dvpGetTargetFormat": ([dvpHandle, ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
            "dvpGetStringValue": ([dvpHandle, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_int),
            "dvpGetConfigString": ([dvpHandle, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_int),
        }
        for name, (argtypes, restype) in specs.items():
            function = getattr(self.dll, name, None)
            if function is None:
                continue
            function.argtypes = argtypes
            function.restype = restype
        missing = [name for name in ("dvpRefresh", "dvpEnum", "dvpOpenByName", "dvpOpenByUserId", "dvpClose", "dvpStart", "dvpStop", "dvpGetFrame") if getattr(self.dll, name, None) is None]
        if missing:
            raise Dvp2BindingError("DVPCamera64.dll is missing required DVP2 symbols: " + ", ".join(missing))


def _bits_suffix(value: int) -> str:
    suffix = {
        BITS_8: "8",
        BITS_10: "10",
        BITS_12: "12",
        BITS_14: "14",
        BITS_16: "16",
    }.get(int(value))
    return suffix or str(value)


def _camera_info_to_device(index: int, info: dvpCameraInfo) -> Dvp2DeviceInfo:
    return Dvp2DeviceInfo(
        index=index,
        vendor=_decode_bytes(info.Vendor),
        manufacturer=_decode_bytes(info.Manufacturer),
        model=_decode_bytes(info.Model),
        family=_decode_bytes(info.Family),
        link_name=_decode_bytes(info.LinkName),
        sensor_info=_decode_bytes(info.SensorInfo),
        friendly_name=_decode_bytes(info.FriendlyName),
        port_info=_decode_bytes(info.PortInfo),
        serial_number=_decode_bytes(info.SerialNumber),
        camera_info=_decode_bytes(info.CameraInfo),
        user_id=_decode_bytes(info.UserID),
        original_serial_number=_decode_bytes(info.OriginalSerialNumber),
    )


def _decode_bytes(value: bytes | bytearray | ctypes.Array) -> str:
    raw = bytes(value)
    raw = raw.split(b"\x00", 1)[0]
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace").strip()


def _to_bytes(value: str) -> bytes:
    return str(value or "").encode("utf-8")


def _float_descr_to_dict(descr: dvpFloatDescr) -> dict[str, float]:
    return {
        "step": float(descr.fStep),
        "min": float(descr.fMin),
        "max": float(descr.fMax),
        "default": float(descr.fDefault),
    }


def _double_descr_to_dict(descr: dvpDoubleDescr) -> dict[str, float]:
    return {
        "step": float(descr.fStep),
        "min": float(descr.fMin),
        "max": float(descr.fMax),
        "default": float(descr.fDefault),
    }
