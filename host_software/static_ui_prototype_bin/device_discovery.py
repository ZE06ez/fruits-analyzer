from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from camera_service.dvp2_mono import _best_ip_text, _best_mac_text, _device_stable_id, find_dvp2_sdk
from camera_service.rgb_uvc import RgbUvcCamera
from serial_service import SerialService


class DeviceRole:
    MAIN_CONTROLLER = "MAIN_CONTROLLER"
    ROTATION_CONTROLLER = "ROTATION_CONTROLLER"
    RGB_CAMERA = "RGB_CAMERA"
    MULTISPECTRAL_CAMERA = "MULTISPECTRAL_CAMERA"


MUTUALLY_EXCLUSIVE_ROLES = {
    DeviceRole.MAIN_CONTROLLER,
    DeviceRole.ROTATION_CONTROLLER,
    DeviceRole.RGB_CAMERA,
    DeviceRole.MULTISPECTRAL_CAMERA,
}


@dataclass(frozen=True)
class DeviceCandidate:
    kind: str
    stable_id: str | None
    display_name: str
    connection: Any
    status: str = "available"
    role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "role": self.role,
            "stableId": self.stable_id,
            "displayName": self.display_name,
            "connection": self.connection,
            "status": self.status,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DeviceBinding:
    role: str
    kind: str
    stable_id: str | None = None
    display_name: str = ""
    last_port: str | None = None
    last_device_index: int | None = None
    backend: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {
            "role": data["role"],
            "kind": data["kind"],
            "stableId": data["stable_id"],
            "displayName": data["display_name"],
            "lastPort": data["last_port"],
            "lastDeviceIndex": data["last_device_index"],
            "backend": data["backend"],
            "metadata": data["metadata"],
            "updatedAt": data["updated_at"],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DeviceBinding":
        return cls(
            role=str(payload.get("role") or ""),
            kind=str(payload.get("kind") or ""),
            stable_id=_optional_text(payload.get("stableId") if "stableId" in payload else payload.get("stable_id")),
            display_name=str(payload.get("displayName") or payload.get("display_name") or ""),
            last_port=_optional_text(payload.get("lastPort") if "lastPort" in payload else payload.get("last_port")),
            last_device_index=_optional_int(payload.get("lastDeviceIndex") if "lastDeviceIndex" in payload else payload.get("last_device_index")),
            backend=_optional_text(payload.get("backend")),
            metadata=dict(payload.get("metadata") or {}),
            updated_at=str(payload.get("updatedAt") or payload.get("updated_at") or ""),
        )


class DeviceRegistry:
    def __init__(self, profile_path: str | Path) -> None:
        self.profile_path = Path(profile_path)
        self._bindings: dict[str, DeviceBinding] = {}
        self.load()

    def load(self) -> dict[str, DeviceBinding]:
        self._bindings = {}
        if not self.profile_path.exists():
            return self.bindings()
        try:
            payload = json.loads(self.profile_path.read_text(encoding="utf-8"))
        except Exception:
            return self.bindings()
        raw_bindings = payload.get("bindings") if isinstance(payload, dict) else {}
        if not isinstance(raw_bindings, dict):
            return self.bindings()
        for role, data in raw_bindings.items():
            if isinstance(data, dict):
                binding = DeviceBinding.from_dict({"role": role, **data})
                if binding.role:
                    self._bindings[binding.role] = binding
        return self.bindings()

    def save(self) -> None:
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "notes": {
                "stableId": "Persistent hardware identity when the device exposes one.",
                "lastPort": "Last known serial port location cache, not a permanent identity.",
                "lastDeviceIndex": "Last known OpenCV camera index cache, not a permanent identity.",
            },
            "bindings": {role: binding.to_dict() for role, binding in sorted(self._bindings.items())},
        }
        self.profile_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def bindings(self) -> dict[str, DeviceBinding]:
        return dict(self._bindings)

    def snapshot(self, candidates: Iterable[DeviceCandidate] | None = None) -> dict[str, Any]:
        candidate_list = list(candidates or [])
        matches = {
            role: self.resolve_binding(binding, candidate_list)
            for role, binding in self._bindings.items()
        }
        return {
            "profilePath": str(self.profile_path),
            "bindings": {role: binding.to_dict() for role, binding in sorted(self._bindings.items())},
            "matches": matches,
        }

    def bind(self, role: str, candidate: DeviceCandidate) -> DeviceBinding:
        role = _normalize_role(role)
        self._ensure_candidate_role_available(role, candidate)
        binding = DeviceBinding(
            role=role,
            kind=candidate.kind,
            stable_id=candidate.stable_id,
            display_name=candidate.display_name,
            last_port=_last_port(candidate),
            last_device_index=_last_device_index(candidate),
            backend=_backend(candidate),
            metadata={
                key: value
                for key, value in dict(candidate.metadata).items()
                if key not in {"error", "technicalError"}
            },
            updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._bindings[role] = binding
        self.save()
        return binding

    def bind_from_payload(self, payload: dict[str, Any], candidates: Iterable[DeviceCandidate]) -> DeviceBinding:
        role = _normalize_role(payload.get("role"))
        candidate = find_candidate(candidates, payload)
        if candidate is None:
            raise ValueError("未找到要绑定的设备候选，请先重新扫描。")
        return self.bind(role, candidate)

    def resolve_binding(self, binding: DeviceBinding, candidates: Iterable[DeviceCandidate]) -> dict[str, Any]:
        candidate_list = list(candidates)
        for candidate in candidate_list:
            if binding.stable_id and candidate.stable_id == binding.stable_id:
                return {"state": "matched", "method": "stableId", "candidate": candidate.to_dict()}
        if binding.kind == "serial" and binding.last_port:
            for candidate in candidate_list:
                if candidate.kind == "serial" and _last_port(candidate) == binding.last_port and candidate.metadata.get("protocolMatched"):
                    return {"state": "matched", "method": "lastPortVerified", "candidate": candidate.to_dict()}
        if binding.kind == "uvc" and binding.last_device_index is not None:
            for candidate in candidate_list:
                if candidate.kind == "uvc" and _last_device_index(candidate) == binding.last_device_index and candidate.status == "available":
                    return {"state": "matched", "method": "lastDeviceIndexVerified", "candidate": candidate.to_dict()}
        return {"state": "unbound", "method": None, "candidate": None}

    def _ensure_candidate_role_available(self, role: str, candidate: DeviceCandidate) -> None:
        if role not in MUTUALLY_EXCLUSIVE_ROLES:
            return
        key = candidate_physical_key(candidate)
        if not key:
            return
        for existing_role, binding in self._bindings.items():
            if existing_role == role or existing_role not in MUTUALLY_EXCLUSIVE_ROLES:
                continue
            if binding_physical_key(binding) == key:
                raise ValueError(f"设备已绑定到 {existing_role}，不能同时绑定到 {role}")


class DeviceDiscovery:
    def __init__(
        self,
        *,
        serial_service: Any | None = None,
        serial_service_factory: Callable[[], Any] | None = None,
        rgb_scanner: Callable[[], list[DeviceCandidate]] | None = None,
        dvp2_scanner: Callable[[], list[DeviceCandidate]] | None = None,
        camera_manager: Any | None = None,
        rgb_max_index: int = 10,
    ) -> None:
        self.serial_service = serial_service or SerialService()
        self.serial_service_factory = serial_service_factory or (lambda: SerialService())
        self.rgb_scanner = rgb_scanner
        self.dvp2_scanner = dvp2_scanner
        self.camera_manager = camera_manager
        self.rgb_max_index = max(0, int(rgb_max_index))

    def discover_all(self) -> dict[str, Any]:
        serial = self.discover_serial()
        rgb = self.discover_rgb()
        dvp2 = self.discover_dvp2()
        candidates = [*serial, *rgb, *dvp2]
        return {
            "ok": True,
            "discoveredAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "candidates": [candidate.to_dict() for candidate in candidates],
            "byKind": {
                "serial": [candidate.to_dict() for candidate in serial],
                "uvc": [candidate.to_dict() for candidate in rgb],
                "dvp2": [candidate.to_dict() for candidate in dvp2],
            },
        }

    def discover_serial(self) -> list[DeviceCandidate]:
        candidates: list[DeviceCandidate] = []
        connected_port = str(getattr(self.serial_service, "port_name", "") or "")
        for port in self.serial_service.list_ports():
            port_dict = port.to_dict() if hasattr(port, "to_dict") else dict(port)
            device = str(port_dict.get("device") or "").strip()
            if not device:
                continue
            in_use = bool(connected_port and device.upper() == connected_port.upper())
            protocol_matched = bool(in_use)
            status = "in_use" if in_use else "available"
            error = ""
            if not in_use:
                probe = self.serial_service_factory()
                try:
                    probe.connect(device)
                    protocol_matched = bool(probe.ping())
                except Exception as exc:
                    status = "unavailable"
                    error = str(exc)
                finally:
                    try:
                        probe.disconnect()
                    except Exception:
                        pass
            stable_id = _serial_stable_id(port_dict)
            metadata = {
                "protocolMatched": protocol_matched,
                "deviceType": None,
                "deviceId": None,
                "firmwareVersion": None,
                "capabilities": None,
                "inUse": in_use,
                "description": port_dict.get("description") or "",
                "hwid": port_dict.get("hwid") or "",
                "vid": port_dict.get("vid"),
                "pid": port_dict.get("pid"),
                "serialNumber": port_dict.get("serial_number") or port_dict.get("serialNumber"),
                "manufacturer": port_dict.get("manufacturer"),
                "product": port_dict.get("product"),
                "location": port_dict.get("location"),
            }
            if error:
                metadata["error"] = error
            candidates.append(DeviceCandidate(
                kind="serial",
                stable_id=stable_id,
                display_name=port_dict.get("description") or port_dict.get("product") or f"Serial controller {device}",
                connection=device,
                status=status,
                metadata=metadata,
            ))
        return candidates

    def discover_rgb(self) -> list[DeviceCandidate]:
        if self.rgb_scanner:
            return list(self.rgb_scanner())
        rgb = getattr(self.camera_manager, "rgb", None) if self.camera_manager is not None else None
        if rgb is None:
            return []
        if bool(getattr(rgb, "is_open", False)):
            status = rgb.get_status().to_dict() if hasattr(rgb, "get_status") else {}
            return [DeviceCandidate(
                kind="uvc",
                stable_id=None,
                display_name=status.get("deviceName") or "RGB camera in use",
                connection={"backend": "DSHOW", "deviceIndex": status.get("deviceIndex")},
                status="in_use",
                metadata={
                    "backend": "DSHOW",
                    "opened": True,
                    "frameReadable": bool(status.get("available")),
                    "stableIdentityAvailable": False,
                    "inUse": True,
                },
            )]
        config = getattr(rgb, "config", None)
        probe_camera = RgbUvcCamera(config=config, max_probe_index=self.rgb_max_index + 1) if config is not None else rgb
        result: list[DeviceCandidate] = []
        for index in range(self.rgb_max_index + 1):
            candidate = self._probe_rgb_index(probe_camera, index)
            if candidate:
                result.append(candidate)
        return result

    def discover_dvp2(self) -> list[DeviceCandidate]:
        if self.dvp2_scanner:
            return list(self.dvp2_scanner())
        camera = getattr(self.camera_manager, "multispectral", None) if self.camera_manager is not None else None
        if camera is None:
            return []
        try:
            devices = camera._enum_devices(camera._ensure_binding())
        except Exception:
            return []
        return [_dvp2_candidate(device) for device in devices]

    def _probe_rgb_index(self, rgb: Any, index: int) -> DeviceCandidate | None:
        previous_config = getattr(rgb, "config", None)
        previous_index = getattr(rgb, "device_index", None)
        was_open = bool(getattr(rgb, "is_open", False))
        if was_open:
            return None
        try:
            if hasattr(rgb, "configure") and previous_config is not None:
                config = previous_config.to_dict() if hasattr(previous_config, "to_dict") else dict(previous_config)
                rgb.configure({**config, "deviceIndex": index})
            else:
                rgb.device_index = index
            rgb.open()
            frame_readable = False
            try:
                frame = rgb.capture_frame()
                frame_readable = True
                shape = tuple(frame.shape)
            except Exception:
                shape = None
            status = rgb.get_status().to_dict()
            actual = status.get("actual") or {}
            return DeviceCandidate(
                kind="uvc",
                stable_id=None,
                display_name=status.get("deviceName") or f"OpenCV DirectShow camera {index}",
                connection={"backend": "DSHOW", "deviceIndex": index},
                status="available" if frame_readable else "unavailable",
                metadata={
                    "backend": "DSHOW",
                    "opened": True,
                    "frameReadable": frame_readable,
                    "width": actual.get("width"),
                    "height": actual.get("height"),
                    "fps": actual.get("fps"),
                    "fourcc": actual.get("fourcc"),
                    "frameShape": shape,
                    "stableIdentityAvailable": False,
                },
            )
        except Exception:
            return None
        finally:
            try:
                rgb.close()
            finally:
                if previous_config is not None and hasattr(rgb, "configure"):
                    rgb.configure(previous_config)
                elif previous_index is not None:
                    rgb.device_index = previous_index


def _dvp2_candidate(device: Any) -> DeviceCandidate:
    stable_id = _device_stable_id(device) or None
    return DeviceCandidate(
        kind="dvp2",
        stable_id=stable_id,
        display_name=getattr(device, "friendly_name", "") or getattr(device, "model", "") or "DVP2 multispectral camera",
        connection={
            "serial": getattr(device, "serial_number", "") or "",
            "userId": getattr(device, "user_id", "") or "",
            "index": getattr(device, "index", None),
        },
        status="available",
        metadata={
            **(device.to_dict() if hasattr(device, "to_dict") else {}),
            "ip": _best_ip_text(device),
            "mac": _best_mac_text(device),
        },
    )


def discover_dvp2_candidates_from_sdk(sdk_dir: str | Path | None = None, binding_factory: Callable[[Path], Any] | None = None) -> list[DeviceCandidate]:
    info = find_dvp2_sdk(sdk_dir)
    if not info.dll_path:
        return []
    if binding_factory is None:
        from camera_service.dvp2_binding import Dvp2Binding

        binding_factory = Dvp2Binding
    binding = binding_factory(info.dll_path)
    return [_dvp2_candidate(device) for device in binding.enum_devices()]


def find_candidate(candidates: Iterable[DeviceCandidate], payload: dict[str, Any]) -> DeviceCandidate | None:
    kind = str(payload.get("kind") or "").strip()
    stable_id = _optional_text(payload.get("stableId"))
    connection = payload.get("connection")
    for candidate in candidates:
        if kind and candidate.kind != kind:
            continue
        if stable_id and candidate.stable_id == stable_id:
            return candidate
        if connection is not None and candidate.connection == connection:
            return candidate
    return None


def candidate_physical_key(candidate: DeviceCandidate) -> str:
    if candidate.stable_id:
        return f"{candidate.kind}:stable:{candidate.stable_id}"
    if candidate.kind == "serial":
        return f"serial:port:{_last_port(candidate) or ''}"
    if candidate.kind == "uvc":
        return f"uvc:{_backend(candidate) or ''}:{_last_device_index(candidate)}"
    if candidate.kind == "dvp2":
        return f"dvp2:index:{(candidate.connection or {}).get('index') if isinstance(candidate.connection, dict) else ''}"
    return ""


def binding_physical_key(binding: DeviceBinding) -> str:
    if binding.stable_id:
        return f"{binding.kind}:stable:{binding.stable_id}"
    if binding.kind == "serial":
        return f"serial:port:{binding.last_port or ''}"
    if binding.kind == "uvc":
        return f"uvc:{binding.backend or ''}:{binding.last_device_index}"
    return ""


def _serial_stable_id(port: dict[str, Any]) -> str | None:
    vid = _optional_text(port.get("vid"))
    pid = _optional_text(port.get("pid"))
    serial_number = _optional_text(port.get("serial_number") or port.get("serialNumber"))
    if vid and pid and serial_number:
        return f"usb:VID_{vid}&PID_{pid}:{serial_number}"
    return None


def _last_port(candidate: DeviceCandidate) -> str | None:
    if candidate.kind == "serial" and isinstance(candidate.connection, str):
        return candidate.connection
    return None


def _last_device_index(candidate: DeviceCandidate) -> int | None:
    if isinstance(candidate.connection, dict):
        return _optional_int(candidate.connection.get("deviceIndex"))
    return None


def _backend(candidate: DeviceCandidate) -> str | None:
    if isinstance(candidate.connection, dict):
        return _optional_text(candidate.connection.get("backend"))
    return _optional_text(candidate.metadata.get("backend"))


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_role(value: Any) -> str:
    role = str(value or "").strip().upper()
    allowed = {
        DeviceRole.MAIN_CONTROLLER,
        DeviceRole.ROTATION_CONTROLLER,
        DeviceRole.RGB_CAMERA,
        DeviceRole.MULTISPECTRAL_CAMERA,
    }
    if role not in allowed:
        raise ValueError(f"未知设备角色: {value}")
    return role
