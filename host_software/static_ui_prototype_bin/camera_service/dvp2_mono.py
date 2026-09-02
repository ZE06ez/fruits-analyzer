from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import CameraDeviceInfo, CameraFrame, CameraStatus
from .errors import CameraSdkUnavailableError, CameraSettingUnsupported


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
    for value in (configured_dir, os.environ.get("DVP2_SDK_DIR")):
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
            base / "Daheng Imaging" / "DVP2",
        ])

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if not candidate.exists() or not candidate.is_dir():
            continue
        direct = candidate / "DVPCamera64.dll"
        if direct.exists() and direct.is_file():
            return Dvp2SdkInfo(candidate, direct)
        for child in candidate.iterdir():
            if not child.is_dir():
                continue
            nested = child / "DVPCamera64.dll"
            if nested.exists() and nested.is_file():
                return Dvp2SdkInfo(child, nested)

    return Dvp2SdkInfo(None, None, "DVPCamera64.dll not found")


class Dvp2MonoCamera:
    """DVP2 monochrome GigE camera boundary.

    This adapter only discovers and loads the SDK DLL in P1A-1. It does not bind
    vendor functions until the real header/examples are available. The project's
    current multispectral camera is a DO3THINK GigE/RJ45 industrial mono camera;
    Ethernet link alone must not be treated as camera connection.
    """

    role = "multispectral"
    transport = "GigE/DVP2"

    def __init__(
        self,
        *,
        sdk_dir: str | os.PathLike[str] | None = None,
        loader: Any | None = None,
    ) -> None:
        self.sdk_dir = Path(sdk_dir).expanduser() if sdk_dir else None
        self._loader = loader
        self._dll = None
        self._sdk_info: Dvp2SdkInfo | None = None
        self._last_error = ""
        self._last_technical_error = ""

    def list_devices(self) -> list[CameraDeviceInfo]:
        self._ensure_sdk_loaded()
        return []

    def open(self) -> None:
        self._raise_unconfirmed_api("打开多光谱相机")

    def close(self) -> None:
        return

    @property
    def is_open(self) -> bool:
        return False

    def get_status(self) -> CameraStatus:
        info = self._sdk_info or find_dvp2_sdk(self.sdk_dir)
        self._sdk_info = info
        status = CameraStatus(
            role=self.role,
            available=False,
            connected=False,
            streaming=False,
            sdk_available=info.sdk_available,
            backend="dvp2",
            transport="GigE/DVP2",
            pixel_format="Mono8/Mono16/RAW8/RAW16 pending SDK query",
            color_space="MONO",
            frame_dtype="uint8 or uint16",
            requested={
                "vendor": "DO3THINK",
                "transport": "GigE/RJ45 Ethernet",
                "sdk": "DVP2",
            },
            actual={
                "ethernetLink": None,
                "cameraIp": "",
                "hostAdapterIp": "",
                "cameraMac": "",
                "cameraSerial": "",
                "linkSpeed": "",
            },
            sdk_path=str(info.sdk_dir) if info.sdk_dir else "",
            dll_path=str(info.dll_path) if info.dll_path else "",
        )
        if not info.sdk_available:
            status.error = "多光谱 GigE 相机 DVP2 SDK 尚未安装"
            status.technical_error = info.reason or "DVPCamera64.dll not found"
        else:
            status.error = "DVP2 API 尚未根据真实 header/examples 绑定"
            status.technical_error = "DVPCamera64.dll found; GigE device enumeration bindings pending"
        return status

    def start_stream(self) -> None:
        self._raise_unconfirmed_api("启动多光谱相机流")

    def stop_stream(self) -> None:
        self._raise_unconfirmed_api("停止多光谱相机流")

    def capture_frame(self) -> CameraFrame:
        self._raise_unconfirmed_api("采集多光谱图像")

    def set_exposure(self, value: float) -> None:
        self._raise_unconfirmed_api("设置多光谱相机曝光")

    def get_exposure(self) -> float | None:
        self._raise_unconfirmed_api("读取多光谱相机曝光")

    def set_gain(self, value: float) -> None:
        self._raise_unconfirmed_api("设置多光谱相机增益")

    def get_gain(self) -> float | None:
        self._raise_unconfirmed_api("读取多光谱相机增益")

    def get_resolution(self) -> tuple[int, int] | None:
        self._raise_unconfirmed_api("读取多光谱相机分辨率")

    def _ensure_sdk_loaded(self) -> None:
        info = self._sdk_info or find_dvp2_sdk(self.sdk_dir)
        self._sdk_info = info
        if not info.dll_path:
            self._last_error = "多光谱 GigE 相机 DVP2 SDK 尚未安装"
            self._last_technical_error = info.reason or "DVPCamera64.dll not found"
            raise CameraSdkUnavailableError(self._last_error, self._last_technical_error)
        if self._dll is None:
            loader = self._loader or ctypes.WinDLL
            try:
                self._dll = loader(str(info.dll_path))
            except Exception as exc:
                self._last_error = "多光谱相机 SDK 加载失败"
                self._last_technical_error = str(exc)
                raise CameraSdkUnavailableError(self._last_error, self._last_technical_error) from exc

    def _raise_unconfirmed_api(self, action: str) -> None:
        try:
            self._ensure_sdk_loaded()
        except CameraSdkUnavailableError:
            raise
        raise CameraSettingUnsupported(
            f"{action}暂不可用",
            "DVP2 function names and signatures are not bound until DVPCamera.h/examples are available",
        )
