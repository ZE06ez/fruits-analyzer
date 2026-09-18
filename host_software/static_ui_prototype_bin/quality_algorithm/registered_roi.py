from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .registration import (
    CameraRegistrationEndpoint,
    RegistrationError,
    RegistrationProfile,
    RegistrationProfileMismatch,
    validate_profile_for_runtime,
    warp_mask_rgb_to_multispectral,
)


REGISTERED_ROI_PROFILE_MISSING = "REGISTRATION_PROFILE_MISSING"
REGISTERED_ROI_TARGET_RESOLUTION_MISMATCH = "REGISTERED_ROI_TARGET_RESOLUTION_MISMATCH"
REGISTERED_ROI_EMPTY = "REGISTERED_ROI_EMPTY"
REGISTERED_ROI_TOO_SMALL = "REGISTERED_ROI_TOO_SMALL"
REGISTERED_ROI_EXCESSIVE_EROSION = "REGISTERED_ROI_EXCESSIVE_EROSION"


class RegisteredRoiError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegisteredRoiConfig:
    # Engineering provisional default. Real hardware ROI tuning is pending.
    erosionPx: int = 2
    minRetainedPixelCount: int = 16
    minRetainedRatio: float = 0.25


@dataclass
class RegisteredRoiResult:
    valid: bool
    mask: np.ndarray | None = None
    maskShape: tuple[int, int] | None = None
    sourceRgbPixelCount: int = 0
    warpedPixelCount: int = 0
    erodedPixelCount: int = 0
    retainedRatio: float = 0.0
    erosionPx: int = 0
    boundingBox: list[int] | None = None
    centroid: list[float] | None = None
    registrationMethod: str = ""
    referenceBandNm: int | None = None
    registrationMetrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    qualityFlags: list[str] = field(default_factory=list)
    errorCode: str = ""
    timingMs: dict[str, float] = field(default_factory=dict)

    def to_dict(self, *, include_mask: bool = False) -> dict:
        data = asdict(self)
        if include_mask and self.mask is not None:
            data["mask"] = self.mask.astype(bool).tolist()
        else:
            data.pop("mask", None)
        return data


def resolve_registration_profile(profile: RegistrationProfile | str | Path | None) -> RegistrationProfile:
    if profile is None:
        raise RegisteredRoiError(REGISTERED_ROI_PROFILE_MISSING)
    if isinstance(profile, RegistrationProfile):
        return profile
    try:
        return RegistrationProfile.from_json_file(profile)
    except Exception as exc:
        raise RegisteredRoiError("REGISTRATION_PROFILE_INVALID") from exc


def build_registered_multispectral_roi(
    *,
    rgb_mask: np.ndarray,
    registration_profile: RegistrationProfile | str | Path | None,
    rgb_endpoint: CameraRegistrationEndpoint | dict | None,
    multispectral_endpoint: CameraRegistrationEndpoint | dict | None,
    target_shape: tuple[int, int],
    config: RegisteredRoiConfig | None = None,
) -> RegisteredRoiResult:
    started = time.perf_counter()
    cfg = config or RegisteredRoiConfig()
    profile = resolve_registration_profile(registration_profile)
    rgb = _endpoint(rgb_endpoint)
    multispectral = _endpoint(multispectral_endpoint)
    try:
        validate_profile_for_runtime(profile, rgb=rgb, multispectral=multispectral, output_shape=target_shape)
        source_mask = np.asarray(rgb_mask).astype(bool)
        source_count = int(np.count_nonzero(source_mask))
        warped = warp_mask_rgb_to_multispectral(
            source_mask,
            profile,
            target_shape,
            rgb=rgb,
            multispectral=multispectral,
        )
    except (RegistrationError, RegistrationProfileMismatch) as exc:
        raise RegisteredRoiError(str(exc)) from exc
    warped_count = int(np.count_nonzero(warped))
    if warped_count <= 0:
        return _result_error(profile, cfg, target_shape, REGISTERED_ROI_EMPTY, started, source_count, warped_count)

    erode_started = time.perf_counter()
    eroded = erode_mask(warped, int(cfg.erosionPx))
    eroded_count = int(np.count_nonzero(eroded))
    retained_ratio = float(eroded_count / warped_count) if warped_count else 0.0
    timing = {
        "erosion": round((time.perf_counter() - erode_started) * 1000.0, 3),
        "total": round((time.perf_counter() - started) * 1000.0, 3),
    }
    flags: list[str] = []
    error = ""
    if eroded_count <= 0:
        error = REGISTERED_ROI_EMPTY
    elif eroded_count < int(cfg.minRetainedPixelCount):
        error = REGISTERED_ROI_TOO_SMALL
    elif retained_ratio < float(cfg.minRetainedRatio):
        error = REGISTERED_ROI_EXCESSIVE_EROSION
    if error:
        flags.append(error)
    return RegisteredRoiResult(
        valid=not error,
        mask=eroded,
        maskShape=tuple(int(v) for v in eroded.shape),
        sourceRgbPixelCount=source_count,
        warpedPixelCount=warped_count,
        erodedPixelCount=eroded_count,
        retainedRatio=retained_ratio,
        erosionPx=int(cfg.erosionPx),
        boundingBox=_bounding_box(eroded),
        centroid=_centroid(eroded),
        registrationMethod=profile.method,
        referenceBandNm=profile.referenceBandNm,
        registrationMetrics=profile.metrics.to_dict(),
        warnings=[],
        qualityFlags=flags,
        errorCode=error,
        timingMs=timing,
    )


def erode_mask(mask: np.ndarray, erosion_px: int) -> np.ndarray:
    bool_mask = np.asarray(mask).astype(bool)
    radius = max(0, int(erosion_px))
    if radius <= 0:
        return bool_mask.copy()
    size = radius * 2 + 1
    try:
        import cv2

        kernel = np.ones((size, size), dtype=np.uint8)
        return cv2.erode(bool_mask.astype(np.uint8), kernel, iterations=1).astype(bool)
    except Exception:
        padded = np.pad(bool_mask, radius, mode="constant", constant_values=False)
        result = np.ones(bool_mask.shape, dtype=bool)
        for dy in range(size):
            for dx in range(size):
                result &= padded[dy : dy + bool_mask.shape[0], dx : dx + bool_mask.shape[1]]
        return result


def _endpoint(endpoint: CameraRegistrationEndpoint | dict | None) -> CameraRegistrationEndpoint:
    if endpoint is None:
        return CameraRegistrationEndpoint()
    if isinstance(endpoint, CameraRegistrationEndpoint):
        return endpoint
    return CameraRegistrationEndpoint.from_dict(endpoint)


def _result_error(
    profile: RegistrationProfile,
    config: RegisteredRoiConfig,
    shape: tuple[int, int],
    code: str,
    started: float,
    source_count: int,
    warped_count: int,
) -> RegisteredRoiResult:
    return RegisteredRoiResult(
        valid=False,
        mask=np.zeros(shape, dtype=bool),
        maskShape=tuple(int(v) for v in shape),
        sourceRgbPixelCount=source_count,
        warpedPixelCount=warped_count,
        erodedPixelCount=0,
        retainedRatio=0.0,
        erosionPx=int(config.erosionPx),
        registrationMethod=profile.method,
        referenceBandNm=profile.referenceBandNm,
        registrationMetrics=profile.metrics.to_dict(),
        qualityFlags=[code],
        errorCode=code,
        timingMs={"total": round((time.perf_counter() - started) * 1000.0, 3)},
    )


def _bounding_box(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def _centroid(mask: np.ndarray) -> list[float] | None:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return [float(xs.mean()), float(ys.mean())]
