from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from runtime_support import atomic_write_json


BACKGROUND_REFERENCE_MISSING = "BACKGROUND_REFERENCE_MISSING"
BACKGROUND_REFERENCE_IMAGE_MISSING = "BACKGROUND_REFERENCE_IMAGE_MISSING"
BACKGROUND_REFERENCE_HASH_MISMATCH = "BACKGROUND_REFERENCE_HASH_MISMATCH"
BACKGROUND_REFERENCE_RESOLUTION_MISMATCH = "BACKGROUND_REFERENCE_RESOLUTION_MISMATCH"
BACKGROUND_REFERENCE_DEVICE_MISMATCH = "BACKGROUND_REFERENCE_DEVICE_MISMATCH"
BACKGROUND_REFERENCE_CAMERA_PROFILE_MISMATCH = "BACKGROUND_REFERENCE_CAMERA_PROFILE_MISMATCH"
BACKGROUND_REFERENCE_CAMERA_SETTINGS_MISMATCH = "BACKGROUND_REFERENCE_CAMERA_SETTINGS_MISMATCH"
BACKGROUND_REFERENCE_ILLUMINATION_MISMATCH = "BACKGROUND_REFERENCE_ILLUMINATION_MISMATCH"
BACKGROUND_REFERENCE_INVALID = "BACKGROUND_REFERENCE_INVALID"


@dataclass
class BackgroundReference:
    referenceId: str
    imagePath: str
    imageSha256: str
    width: int
    height: int
    channels: int = 3
    dtype: str = "uint8"
    capturedAt: str = ""
    device: dict = field(default_factory=dict)
    cameraProfile: dict = field(default_factory=dict)
    cameraSettings: dict = field(default_factory=dict)
    illumination: dict = field(default_factory=dict)
    stage: dict = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BackgroundReferenceValidation:
    valid: bool
    errorCode: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def compute_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_metadata(path: str | Path) -> dict:
    with Image.open(path) as image:
        mode = image.mode
        width, height = image.size
    channels = 1 if mode in {"1", "L", "I;16", "I"} else 3
    dtype = "uint16" if mode in {"I;16", "I"} else "uint8"
    return {"width": int(width), "height": int(height), "channels": channels, "dtype": dtype, "mode": mode}


def create_background_reference(
    image_path: str | Path,
    *,
    reference_id: str = "",
    device: dict | None = None,
    camera_profile: dict | None = None,
    camera_settings: dict | None = None,
    illumination: dict | None = None,
    stage: dict | None = None,
    notes: str = "",
) -> BackgroundReference:
    path = Path(image_path)
    meta = image_metadata(path)
    return BackgroundReference(
        referenceId=reference_id or f"bg_{int(time.time())}_{compute_sha256(path)[:10]}",
        imagePath=str(path),
        imageSha256=compute_sha256(path),
        width=meta["width"],
        height=meta["height"],
        channels=meta["channels"],
        dtype=meta["dtype"],
        capturedAt=time.strftime("%Y-%m-%d %H:%M:%S"),
        device=dict(device or {}),
        cameraProfile=dict(camera_profile or {}),
        cameraSettings=dict(camera_settings or {}),
        illumination=dict(illumination or {}),
        stage=dict(stage or {}),
        notes=notes,
    )


def save_background_reference(reference: BackgroundReference, path: str | Path) -> None:
    target = Path(path)
    atomic_write_json(target, reference.to_dict())


def load_background_reference(path: str | Path) -> BackgroundReference:
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("background reference JSON must be an object")
    image_path = Path(str(data.get("imagePath") or ""))
    if image_path and not image_path.is_absolute():
        data["imagePath"] = str((source.parent / image_path).resolve())
    return BackgroundReference(
        referenceId=str(data.get("referenceId") or ""),
        imagePath=str(data.get("imagePath") or ""),
        imageSha256=str(data.get("imageSha256") or ""),
        width=int(data.get("width") or 0),
        height=int(data.get("height") or 0),
        channels=int(data.get("channels") or 3),
        dtype=str(data.get("dtype") or "uint8"),
        capturedAt=str(data.get("capturedAt") or ""),
        device=dict(data.get("device") or {}),
        cameraProfile=dict(data.get("cameraProfile") or {}),
        cameraSettings=dict(data.get("cameraSettings") or {}),
        illumination=dict(data.get("illumination") or {}),
        stage=dict(data.get("stage") or {}),
        notes=str(data.get("notes") or ""),
    )


def validate_background_reference(
    reference: BackgroundReference | None,
    *,
    sample_shape: tuple[int, int] | None = None,
    camera_metadata: dict | None = None,
) -> BackgroundReferenceValidation:
    if reference is None:
        return _invalid(BACKGROUND_REFERENCE_MISSING)
    if not reference.imagePath:
        return _invalid(BACKGROUND_REFERENCE_IMAGE_MISSING)
    path = Path(reference.imagePath)
    if not path.exists() or not path.is_file():
        return _invalid(BACKGROUND_REFERENCE_IMAGE_MISSING)
    try:
        if reference.imageSha256 and compute_sha256(path) != reference.imageSha256:
            return _invalid(BACKGROUND_REFERENCE_HASH_MISMATCH)
        meta = image_metadata(path)
    except (OSError, UnidentifiedImageError, ValueError):
        return _invalid(BACKGROUND_REFERENCE_INVALID)
    if meta["width"] != reference.width or meta["height"] != reference.height:
        return _invalid(BACKGROUND_REFERENCE_RESOLUTION_MISMATCH)
    if sample_shape is not None:
        sample_h, sample_w = int(sample_shape[0]), int(sample_shape[1])
        if (sample_w, sample_h) != (reference.width, reference.height):
            return _invalid(BACKGROUND_REFERENCE_RESOLUTION_MISMATCH)
    mismatch = _first_metadata_mismatch(reference, camera_metadata or {})
    if mismatch:
        return _invalid(mismatch)
    return BackgroundReferenceValidation(valid=True)


def _first_metadata_mismatch(reference: BackgroundReference, runtime: dict) -> str:
    if not runtime:
        return ""
    runtime_device = runtime.get("device") or runtime
    if _stable_value(reference.device, "stableId") and _stable_value(runtime_device, "stableId"):
        if _stable_value(reference.device, "stableId") != _stable_value(runtime_device, "stableId"):
            return BACKGROUND_REFERENCE_DEVICE_MISMATCH
    elif _stable_value(reference.device, "deviceIndex") and _stable_value(runtime_device, "deviceIndex"):
        if _stable_value(reference.device, "deviceIndex") != _stable_value(runtime_device, "deviceIndex"):
            return BACKGROUND_REFERENCE_DEVICE_MISMATCH
    if _dict_mismatch(reference.cameraProfile, runtime.get("cameraProfile") or runtime.get("profile") or {}, {"width", "height", "fourcc", "actualFourcc", "qualityClass"}):
        return BACKGROUND_REFERENCE_CAMERA_PROFILE_MISMATCH
    if _dict_mismatch(reference.cameraSettings, runtime.get("cameraSettings") or runtime.get("settings") or {}, {"exposure", "gain", "autoExposure", "whiteBalance", "autoWhiteBalance"}):
        return BACKGROUND_REFERENCE_CAMERA_SETTINGS_MISMATCH
    if _dict_mismatch(reference.illumination, runtime.get("illumination") or {}, {"captureConditionId", "lightSource", "intensity", "ledState"}):
        return BACKGROUND_REFERENCE_ILLUMINATION_MISMATCH
    return ""


def _dict_mismatch(reference: dict, runtime: dict, keys: set[str]) -> bool:
    if not reference or not runtime:
        return False
    for key in keys:
        left = reference.get(key)
        right = runtime.get(key)
        if left in (None, "", "unknown") or right in (None, "", "unknown"):
            continue
        if str(left) != str(right):
            return True
    return False


def _stable_value(data: dict, key: str) -> str:
    value = data.get(key) if isinstance(data, dict) else ""
    return "" if value in (None, "", "unknown") else str(value)


def _invalid(code: str) -> BackgroundReferenceValidation:
    return BackgroundReferenceValidation(valid=False, errorCode=code, errors=[code])
