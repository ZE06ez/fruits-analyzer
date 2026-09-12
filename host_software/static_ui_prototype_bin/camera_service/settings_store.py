from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_RGB_SCIENTIFIC_FOURCC,
    DEFAULT_RGB_SCIENTIFIC_FPS,
    DEFAULT_RGB_SCIENTIFIC_HEIGHT,
    DEFAULT_RGB_SCIENTIFIC_WIDTH,
    RgbCameraConfig,
)
from .dvp2_mono import DEFAULT_DVP2_SERIAL


CAMERA_SETTINGS_VERSION = 1


def default_camera_settings_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "camera_settings.json"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def default_camera_settings_document() -> dict[str, Any]:
    rgb = RgbCameraConfig().to_dict()
    return {
        "version": CAMERA_SETTINGS_VERSION,
        "rgb": {
            **rgb,
            "resolution": f"{rgb['width']} x {rgb['height']}",
            "autoExposureEnabled": True,
            "gainAuto": True,
            "autoWhiteBalanceEnabled": True,
            "deviceStableId": "",
            "deviceName": "",
            "identitySource": "deviceIndex",
            "autoExposure": 0.75,
            "autoWhiteBalance": 1,
            "lastActual": {},
            "settingResults": {},
            "settingsSource": "default",
            "scientificProfile": {
                "width": DEFAULT_RGB_SCIENTIFIC_WIDTH,
                "height": DEFAULT_RGB_SCIENTIFIC_HEIGHT,
                "fps": DEFAULT_RGB_SCIENTIFIC_FPS,
                "fourcc": DEFAULT_RGB_SCIENTIFIC_FOURCC,
            },
        },
        "multispectral": {
            "deviceStableId": f"DS{DEFAULT_DVP2_SERIAL}",
            "serialNumber": DEFAULT_DVP2_SERIAL,
            "deviceIndex": None,
            "friendlyName": "",
            "exposure": 10000.0,
            "gain": 1.0,
            "lastActual": {},
            "settingResults": {},
            "settingsSource": "default",
        },
    }


class CameraSettingsStore:
    """Backend-authoritative JSON store for camera startup restore settings."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else default_camera_settings_path()
        self._last_warnings: list[dict[str, str]] = []

    def load(self) -> dict[str, Any]:
        self._last_warnings = []
        document = default_camera_settings_document()
        if not self.path.exists():
            return self.save(document)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            warning = {
                "code": "CAMERA_SETTINGS_CORRUPT",
                "message": f"Camera settings JSON could not be loaded: {exc}",
            }
            self._last_warnings.append(warning)
            print(f"[camera.settings] load corrupt path={self.path}: {exc}", flush=True)
            return document
        if not isinstance(raw, dict):
            self._last_warnings.append({
                "code": "CAMERA_SETTINGS_INVALID",
                "message": "Camera settings JSON root must be an object.",
            })
            return document
        document["version"] = int(raw.get("version") or CAMERA_SETTINGS_VERSION)
        document["rgb"] = self.normalize_rgb(raw.get("rgb") or {})
        document["multispectral"] = self.normalize_multispectral(raw.get("multispectral") or {})
        return document

    def save(self, document: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            "version": CAMERA_SETTINGS_VERSION,
            "rgb": self.normalize_rgb((document or {}).get("rgb") or {}),
            "multispectral": self.normalize_multispectral((document or {}).get("multispectral") or {}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        tmp_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, self.path)
        print(f"[camera.settings] save path={self.path}", flush=True)
        return normalized

    def snapshot(self) -> dict[str, Any]:
        document = self.load()
        return {
            **document,
            "path": str(self.path),
            "exists": self.path.exists(),
            "warnings": list(self._last_warnings),
            "hasCustom": {
                "rgb": self.has_custom("rgb", document),
                "multispectral": self.has_custom("multispectral", document),
            },
        }

    def get_rgb(self) -> dict[str, Any]:
        return self.load()["rgb"]

    def get_multispectral(self) -> dict[str, Any]:
        return self.load()["multispectral"]

    def update_rgb(
        self,
        settings: dict[str, Any],
        *,
        status: dict[str, Any] | None = None,
        setting_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        document = self.load()
        rgb = self.normalize_rgb({**document.get("rgb", {}), **(settings or {})})
        self._merge_device_identity(rgb, status or {})
        actual = dict((status or {}).get("actual") or {})
        if actual:
            rgb["lastActual"] = actual
        if setting_results is not None:
            rgb["settingResults"] = copy.deepcopy(setting_results)
        rgb["settingsSource"] = "persistent"
        rgb["updatedAt"] = utc_timestamp()
        document["rgb"] = rgb
        return self.save(document)

    def update_multispectral(
        self,
        settings: dict[str, Any],
        *,
        status: dict[str, Any] | None = None,
        setting_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        document = self.load()
        multispectral = self.normalize_multispectral({**document.get("multispectral", {}), **(settings or {})})
        self._merge_device_identity(multispectral, status or {})
        actual = dict((status or {}).get("actual") or {})
        if actual:
            multispectral["lastActual"] = actual
        if setting_results is not None:
            multispectral["settingResults"] = copy.deepcopy(setting_results)
        multispectral["settingsSource"] = "persistent"
        multispectral["updatedAt"] = utc_timestamp()
        document["multispectral"] = multispectral
        return self.save(document)

    def reset(self, section: str | None = None) -> dict[str, Any]:
        if section not in (None, "rgb", "multispectral"):
            raise ValueError("section must be rgb, multispectral, or omitted")
        document = self.load()
        defaults = default_camera_settings_document()
        if section is None:
            document = defaults
        else:
            document[section] = defaults[section]
        document = self.save(document)
        print(f"[camera.settings] reset section={section or 'all'}", flush=True)
        return document

    def migrate_legacy_rgb(self, legacy_settings: dict[str, Any]) -> dict[str, Any]:
        if self.path.exists():
            return {
                **self.snapshot(),
                "migrated": False,
                "reason": "backend_settings_exists",
            }
        document = default_camera_settings_document()
        document["rgb"] = {
            **self.normalize_rgb(legacy_settings or {}),
            "settingsSource": "persistent",
            "updatedAt": utc_timestamp(),
            "migratedFrom": "fruitAnalyzer.cameraSettings",
        }
        saved = self.save(document)
        return {
            **self.snapshot(),
            "migrated": True,
            "rgb": saved["rgb"],
        }

    def has_custom(self, section: str, document: dict[str, Any] | None = None) -> bool:
        document = document or self.load()
        settings = document.get(section) or {}
        return bool(settings.get("updatedAt") or settings.get("migratedFrom"))

    @classmethod
    def normalize_rgb(cls, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = default_camera_settings_document()["rgb"]
        payload = payload or {}
        merged = {**defaults, **payload}
        config = RgbCameraConfig.from_dict(merged)
        auto_exposure_value = cls._optional_float(merged.get("autoExposure"))
        if auto_exposure_value is None:
            auto_exposure_value = float(defaults["autoExposure"])
        auto_exposure_enabled = cls._bool(
            payload.get("autoExposureEnabled") if "autoExposureEnabled" in payload else None,
            default=(auto_exposure_value >= 0.5),
        )
        gain_auto = cls._bool(
            payload.get("gainAuto") if "gainAuto" in payload else None,
            default=merged.get("gain") is None,
        )
        auto_wb_enabled = cls._bool(
            payload.get("autoWhiteBalanceEnabled") if "autoWhiteBalanceEnabled" in payload else None,
            default=bool(merged.get("autoWhiteBalance", defaults["autoWhiteBalance"])),
        )
        scientific_profile_payload = None
        if "scientificProfile" in payload:
            scientific_profile_payload = payload.get("scientificProfile")
        elif any(key in payload for key in ("scientificWidth", "scientificHeight", "scientificFps", "scientificFourcc")):
            scientific_profile_payload = payload
        rgb: dict[str, Any] = {
            "deviceStableId": cls._string(merged.get("deviceStableId") or merged.get("stableId")),
            "deviceIndex": int(config.device_index),
            "deviceName": cls._string(merged.get("deviceName") or merged.get("friendlyName")),
            "identitySource": cls._string(merged.get("identitySource") or "deviceIndex"),
            "width": int(config.width),
            "height": int(config.height),
            "resolution": f"{int(config.width)} x {int(config.height)}",
            "fps": float(config.fps),
            "fourcc": str(config.fourcc or "MJPG").upper()[:4],
            "autoExposureEnabled": auto_exposure_enabled,
            "autoExposure": 0.75 if auto_exposure_enabled else 0.25,
            "exposure": None if auto_exposure_enabled else cls._optional_float(merged.get("exposure")),
            "gainAuto": gain_auto,
            "gain": None if gain_auto else cls._optional_float(merged.get("gain")),
            "autoWhiteBalanceEnabled": auto_wb_enabled,
            "autoWhiteBalance": 1 if auto_wb_enabled else 0,
            "whiteBalance": None if auto_wb_enabled else cls._optional_float(merged.get("whiteBalance")),
            "lastActual": dict(merged.get("lastActual") or {}),
            "settingResults": dict(merged.get("settingResults") or {}),
            "settingsSource": cls._string(merged.get("settingsSource") or "default") or "default",
            "scientificProfile": cls.normalize_rgb_scientific_profile(scientific_profile_payload),
        }
        for key in ("fx", "fy", "cx", "cy"):
            value = cls._optional_float(merged.get(key))
            if value is not None:
                rgb[key] = value
        for key in ("updatedAt", "migratedFrom"):
            if merged.get(key):
                rgb[key] = cls._string(merged.get(key))
        return rgb

    @classmethod
    def normalize_rgb_scientific_profile(cls, payload: dict[str, Any]) -> dict[str, Any]:
        payload = payload or {}
        profile: dict[str, Any] = {}
        width = payload.get("scientificWidth", payload.get("width"))
        height = payload.get("scientificHeight", payload.get("height"))
        fps = payload.get("scientificFps", payload.get("fps"))
        fourcc = payload.get("scientificFourcc", payload.get("fourcc"))
        if width not in (None, ""):
            profile["width"] = int(width)
        if height not in (None, ""):
            profile["height"] = int(height)
        if fps not in (None, ""):
            profile["fps"] = float(fps)
        if fourcc not in (None, ""):
            profile["fourcc"] = str(fourcc).upper()[:4]
        return profile

    @classmethod
    def normalize_multispectral(cls, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = default_camera_settings_document()["multispectral"]
        payload = payload or {}
        merged = {**defaults, **payload}
        multispectral: dict[str, Any] = {
            "deviceStableId": cls._string(merged.get("deviceStableId") or merged.get("stableId")),
            "serialNumber": cls._string(merged.get("serialNumber") or DEFAULT_DVP2_SERIAL),
            "deviceIndex": cls._optional_int(merged.get("deviceIndex")),
            "friendlyName": cls._string(merged.get("friendlyName") or merged.get("deviceName")),
            "exposure": cls._required_float(merged.get("exposure"), defaults["exposure"]),
            "gain": cls._required_float(merged.get("gain"), defaults["gain"]),
            "lastActual": dict(merged.get("lastActual") or {}),
            "settingResults": dict(merged.get("settingResults") or {}),
            "settingsSource": cls._string(merged.get("settingsSource") or "default") or "default",
        }
        for key in ("updatedAt", "migratedFrom"):
            if merged.get(key):
                multispectral[key] = cls._string(merged.get(key))
        return multispectral

    @classmethod
    def _merge_device_identity(cls, settings: dict[str, Any], status: dict[str, Any]) -> None:
        actual = status.get("actual") or {}
        stable_id = status.get("stableId") or actual.get("stableId")
        if stable_id:
            settings["deviceStableId"] = cls._string(stable_id)
        device_index = status.get("deviceIndex")
        if device_index is None:
            device_index = actual.get("deviceIndex") or (status.get("requested") or {}).get("deviceIndex")
        parsed_index = cls._optional_int(device_index)
        if parsed_index is not None:
            settings["deviceIndex"] = parsed_index
        device_name = status.get("deviceName") or actual.get("deviceName") or actual.get("friendlyName")
        if device_name:
            if "friendlyName" in settings:
                settings["friendlyName"] = cls._string(device_name)
            else:
                settings["deviceName"] = cls._string(device_name)
        serial = actual.get("cameraSerial") or actual.get("serialNumber") or actual.get("userId")
        if serial and "serialNumber" in settings:
            settings["serialNumber"] = cls._string(serial)

    @staticmethod
    def _string(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "auto", "自动"}
        return bool(default)

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        parsed = float(value)
        return parsed if parsed == parsed else None

    @classmethod
    def _required_float(cls, value: Any, fallback: float) -> float:
        parsed = cls._optional_float(value)
        return float(fallback if parsed is None else parsed)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        return int(value)
