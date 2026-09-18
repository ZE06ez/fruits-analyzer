from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from runtime_support import atomic_write_json


REGISTRATION_SCHEMA_VERSION = 1
REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1 = "planar_homography_v1"
REGISTRATION_PROFILE_MISMATCH = "REGISTRATION_PROFILE_MISMATCH"


class RegistrationError(RuntimeError):
    pass


class RegistrationProfileMismatch(RegistrationError):
    pass


@dataclass(frozen=True)
class CameraRegistrationEndpoint:
    stableId: str = ""
    deviceIndex: int | None = None
    serial: str = ""
    width: int = 0
    height: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "CameraRegistrationEndpoint":
        payload = payload or {}
        return cls(
            stableId=str(payload.get("stableId") or ""),
            deviceIndex=_optional_int(payload.get("deviceIndex")),
            serial=str(payload.get("serial") or ""),
            width=int(payload.get("width") or 0),
            height=int(payload.get("height") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CheckerboardTarget:
    type: str = "checkerboard"
    innerCornersCols: int = 9
    innerCornersRows: int = 6
    squareSizeMm: float = 20.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "CheckerboardTarget":
        payload = payload or {}
        return cls(
            type=str(payload.get("type") or "checkerboard"),
            innerCornersCols=int(payload.get("innerCornersCols") or payload.get("boardCols") or 9),
            innerCornersRows=int(payload.get("innerCornersRows") or payload.get("boardRows") or 6),
            squareSizeMm=float(payload.get("squareSizeMm") or payload.get("squareMm") or 20.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RegistrationMetrics:
    pointCount: int
    rmsePx: float
    meanErrorPx: float
    maxErrorPx: float
    p95ErrorPx: float

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "RegistrationMetrics":
        payload = payload or {}
        return cls(
            pointCount=int(payload.get("pointCount") or 0),
            rmsePx=float(payload.get("rmsePx") or 0.0),
            meanErrorPx=float(payload.get("meanErrorPx") or 0.0),
            maxErrorPx=float(payload.get("maxErrorPx") or 0.0),
            p95ErrorPx=float(payload.get("p95ErrorPx") or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RegistrationProfile:
    schemaVersion: int
    method: str
    rgb: CameraRegistrationEndpoint
    multispectral: CameraRegistrationEndpoint
    referenceBandNm: int | None
    target: CheckerboardTarget
    matrixRgbToMultispectral: list[list[float]]
    calibrationPlane: str
    metrics: RegistrationMetrics
    createdAt: str
    sourcePairs: list[dict[str, Any]] = field(default_factory=list)
    valid: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RegistrationProfile":
        if not isinstance(payload, dict):
            raise RegistrationError("registration profile root must be an object")
        profile = cls(
            schemaVersion=int(payload.get("schemaVersion") or 0),
            method=str(payload.get("method") or ""),
            rgb=CameraRegistrationEndpoint.from_dict(payload.get("rgb")),
            multispectral=CameraRegistrationEndpoint.from_dict(payload.get("multispectral")),
            referenceBandNm=_optional_int(payload.get("referenceBandNm")),
            target=CheckerboardTarget.from_dict(payload.get("target")),
            matrixRgbToMultispectral=_matrix_to_nested_list(payload.get("matrixRgbToMultispectral")),
            calibrationPlane=str(payload.get("calibrationPlane") or ""),
            metrics=RegistrationMetrics.from_dict(payload.get("metrics")),
            createdAt=str(payload.get("createdAt") or ""),
            sourcePairs=list(payload.get("sourcePairs") or []),
            valid=bool(payload.get("valid")),
        )
        profile.validate()
        return profile

    @classmethod
    def from_json_file(cls, path: str | Path) -> "RegistrationProfile":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            raise RegistrationError(f"registration profile could not be loaded: {path}") from exc
        return cls.from_dict(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schemaVersion,
            "method": self.method,
            "rgb": self.rgb.to_dict(),
            "multispectral": self.multispectral.to_dict(),
            "referenceBandNm": self.referenceBandNm,
            "target": self.target.to_dict(),
            "matrixRgbToMultispectral": _matrix_to_nested_list(self.matrixRgbToMultispectral),
            "calibrationPlane": self.calibrationPlane,
            "metrics": self.metrics.to_dict(),
            "createdAt": self.createdAt,
            "sourcePairs": list(self.sourcePairs),
            "valid": self.valid,
        }

    def save_json(self, path: str | Path) -> None:
        path = Path(path)
        atomic_write_json(path, self.to_dict())

    def validate(self) -> None:
        if self.schemaVersion != REGISTRATION_SCHEMA_VERSION:
            raise RegistrationError(f"unsupported registration schemaVersion: {self.schemaVersion}")
        if self.method != REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1:
            raise RegistrationError(f"unsupported registration method: {self.method}")
        validate_homography_matrix(self.matrixRgbToMultispectral)
        if self.rgb.width <= 0 or self.rgb.height <= 0:
            raise RegistrationError("RGB registration resolution must be positive")
        if self.multispectral.width <= 0 or self.multispectral.height <= 0:
            raise RegistrationError("multispectral registration resolution must be positive")
        if self.target.type != "checkerboard":
            raise RegistrationError(f"unsupported registration target: {self.target.type}")
        if self.target.innerCornersCols <= 1 or self.target.innerCornersRows <= 1:
            raise RegistrationError("checkerboard must have at least 2x2 inner corners")
        if self.target.squareSizeMm <= 0:
            raise RegistrationError("checkerboard squareSizeMm must be positive")
        if self.metrics.pointCount < 4:
            raise RegistrationError("registration profile has insufficient calibration points")
        if not self.calibrationPlane:
            raise RegistrationError("registration profile must describe calibrationPlane")
        if not self.createdAt:
            raise RegistrationError("registration profile missing createdAt")
        if not self.valid:
            raise RegistrationError("registration profile is marked invalid")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_homography_matrix(matrix: Any) -> np.ndarray:
    h = np.asarray(matrix, dtype=np.float64)
    if h.shape != (3, 3):
        raise RegistrationError("homography matrix must be 3x3")
    if not np.all(np.isfinite(h)):
        raise RegistrationError("homography matrix contains non-finite values")
    det = float(np.linalg.det(h))
    if abs(det) < 1e-12:
        raise RegistrationError("homography matrix is singular")
    if abs(float(h[2, 2])) < 1e-12:
        raise RegistrationError("homography matrix bottom-right scale is zero")
    return h


def compute_reprojection_errors(rgb_points: Any, multispectral_points: Any, homography: Any) -> np.ndarray:
    rgb = _points_array(rgb_points, name="rgb_points")
    ms = _points_array(multispectral_points, name="multispectral_points")
    if rgb.shape != ms.shape:
        raise RegistrationError("rgb_points and multispectral_points must have the same shape")
    h = validate_homography_matrix(homography)
    projected = project_points(rgb, h)
    return np.linalg.norm(projected - ms, axis=1).astype(np.float64)


def reprojection_metrics(errors: Any) -> RegistrationMetrics:
    arr = np.asarray(errors, dtype=np.float64).reshape(-1)
    if arr.size < 4:
        raise RegistrationError("at least 4 reprojection errors are required")
    if not np.all(np.isfinite(arr)):
        raise RegistrationError("reprojection errors contain non-finite values")
    return RegistrationMetrics(
        pointCount=int(arr.size),
        rmsePx=float(math.sqrt(float(np.mean(arr ** 2)))),
        meanErrorPx=float(np.mean(arr)),
        maxErrorPx=float(np.max(arr)),
        p95ErrorPx=float(np.percentile(arr, 95)),
    )


def project_points(points: Any, homography: Any) -> np.ndarray:
    pts = _points_array(points, name="points")
    h = validate_homography_matrix(homography)
    homo = np.concatenate([pts, np.ones((pts.shape[0], 1), dtype=np.float64)], axis=1)
    projected = homo @ h.T
    denom = projected[:, 2:3]
    if np.any(np.abs(denom) < 1e-12):
        raise RegistrationError("homography projects points to infinity")
    return (projected[:, :2] / denom).astype(np.float64)


def estimate_planar_homography(
    rgb_points: Any,
    multispectral_points: Any,
    *,
    ransac_reproj_threshold: float = 3.0,
) -> tuple[np.ndarray, np.ndarray, RegistrationMetrics]:
    rgb = _points_array(rgb_points, name="rgb_points")
    ms = _points_array(multispectral_points, name="multispectral_points")
    if rgb.shape != ms.shape:
        raise RegistrationError("rgb_points and multispectral_points must have the same shape")
    if rgb.shape[0] < 4:
        raise RegistrationError("insufficient point correspondences for homography")
    cv2 = _cv2()
    h, inlier_mask = cv2.findHomography(rgb.astype(np.float64), ms.astype(np.float64), method=cv2.RANSAC, ransacReprojThreshold=float(ransac_reproj_threshold))
    if h is None or inlier_mask is None:
        raise RegistrationError("homography estimation failed")
    h = validate_homography_matrix(h)
    inliers = inlier_mask.reshape(-1).astype(bool)
    if int(np.count_nonzero(inliers)) < 4:
        raise RegistrationError("homography has insufficient RANSAC inliers")
    errors = compute_reprojection_errors(rgb[inliers], ms[inliers], h)
    return h, inliers, reprojection_metrics(errors)


def detect_checkerboard_corners(
    image: Any,
    *,
    inner_corners_cols: int,
    inner_corners_rows: int,
    refine: bool = True,
) -> np.ndarray:
    if inner_corners_cols <= 1 or inner_corners_rows <= 1:
        raise RegistrationError("checkerboard dimensions must be greater than 1")
    cv2 = _cv2()
    gray = _grayscale_uint8(image)
    pattern_size = (int(inner_corners_cols), int(inner_corners_rows))
    found = False
    corners = None
    if hasattr(cv2, "findChessboardCornersSB"):
        found, corners = cv2.findChessboardCornersSB(gray, pattern_size)
    if not found:
        flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
        found, corners = cv2.findChessboardCorners(gray, pattern_size, flags)
        if found and refine:
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
            corners = cv2.cornerSubPix(gray, corners, (5, 5), (-1, -1), criteria)
    if not found or corners is None:
        raise RegistrationError("CHECKERBOARD_CORNERS_NOT_FOUND")
    pts = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    expected = int(inner_corners_cols) * int(inner_corners_rows)
    if pts.shape[0] != expected:
        raise RegistrationError(f"checkerboard corner count mismatch: expected={expected} actual={pts.shape[0]}")
    return pts


def build_registration_profile(
    *,
    rgb_points: Any,
    multispectral_points: Any,
    rgb: CameraRegistrationEndpoint,
    multispectral: CameraRegistrationEndpoint,
    target: CheckerboardTarget,
    reference_band_nm: int | None,
    source_pairs: list[dict[str, Any]] | None = None,
    calibration_plane: str | None = None,
    ransac_reproj_threshold: float = 3.0,
) -> tuple[RegistrationProfile, np.ndarray]:
    h, inliers, metrics = estimate_planar_homography(
        rgb_points,
        multispectral_points,
        ransac_reproj_threshold=ransac_reproj_threshold,
    )
    profile = RegistrationProfile(
        schemaVersion=REGISTRATION_SCHEMA_VERSION,
        method=REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1,
        rgb=rgb,
        multispectral=multispectral,
        referenceBandNm=reference_band_nm,
        target=target,
        matrixRgbToMultispectral=_matrix_to_nested_list(h),
        calibrationPlane=calibration_plane
        or "Planar checkerboard placed near representative fruit center depth; planar homography is not a perfect 3D registration.",
        metrics=metrics,
        createdAt=utc_timestamp(),
        sourcePairs=list(source_pairs or []),
        valid=True,
    )
    profile.validate()
    return profile, inliers


def validate_profile_for_runtime(
    profile: RegistrationProfile,
    *,
    rgb: CameraRegistrationEndpoint,
    multispectral: CameraRegistrationEndpoint,
    output_shape: tuple[int, int] | None = None,
) -> None:
    profile.validate()
    _require_endpoint_match("rgb", profile.rgb, rgb)
    _require_endpoint_match("multispectral", profile.multispectral, multispectral)
    if output_shape is not None:
        height, width = int(output_shape[0]), int(output_shape[1])
        if (width, height) != (profile.multispectral.width, profile.multispectral.height):
            raise RegistrationProfileMismatch(
                f"{REGISTRATION_PROFILE_MISMATCH}: target resolution expected="
                f"{profile.multispectral.width}x{profile.multispectral.height} actual={width}x{height}"
            )


def warp_mask_rgb_to_multispectral(
    rgb_mask: Any,
    profile: RegistrationProfile,
    output_shape: tuple[int, int],
    *,
    rgb: CameraRegistrationEndpoint | None = None,
    multispectral: CameraRegistrationEndpoint | None = None,
) -> np.ndarray:
    mask = np.asarray(rgb_mask)
    if mask.ndim != 2:
        raise RegistrationError("rgb_mask must be a 2D array")
    rgb_endpoint = rgb or profile.rgb
    ms_endpoint = multispectral or profile.multispectral
    validate_profile_for_runtime(profile, rgb=rgb_endpoint, multispectral=ms_endpoint, output_shape=output_shape)
    if (mask.shape[1], mask.shape[0]) != (profile.rgb.width, profile.rgb.height):
        raise RegistrationProfileMismatch(
            f"{REGISTRATION_PROFILE_MISMATCH}: RGB mask resolution expected="
            f"{profile.rgb.width}x{profile.rgb.height} actual={mask.shape[1]}x{mask.shape[0]}"
        )
    cv2 = _cv2()
    warped = cv2.warpPerspective(
        mask.astype(np.uint8),
        validate_homography_matrix(profile.matrixRgbToMultispectral),
        (int(output_shape[1]), int(output_shape[0])),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return warped.astype(bool)


def load_image(path: str | Path) -> np.ndarray:
    cv2 = _cv2()
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise RegistrationError(f"cannot read image: {path}")
    if image.ndim == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return image


def save_detected_corners_visualization(path: str | Path, image: Any, corners: Any, *, inner_corners_cols: int, inner_corners_rows: int) -> None:
    cv2 = _cv2()
    rgb = _rgb_uint8(image)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    cv2.drawChessboardCorners(
        bgr,
        (int(inner_corners_cols), int(inner_corners_rows)),
        np.asarray(corners, dtype=np.float32).reshape(-1, 1, 2),
        True,
    )
    _write_image(path, bgr)


def save_warp_visualizations(
    *,
    rgb_image: Any,
    multispectral_image: Any,
    profile: RegistrationProfile,
    output_dir: str | Path,
) -> dict[str, str]:
    cv2 = _cv2()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ms_gray = _grayscale_uint8(multispectral_image)
    rgb = _rgb_uint8(rgb_image)
    h = validate_homography_matrix(profile.matrixRgbToMultispectral)
    warped_rgb = cv2.warpPerspective(
        rgb,
        h,
        (profile.multispectral.width, profile.multispectral.height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    ms_rgb = cv2.cvtColor(ms_gray, cv2.COLOR_GRAY2RGB)
    overlay = cv2.addWeighted(warped_rgb, 0.5, ms_rgb, 0.5, 0.0)
    diff = cv2.absdiff(cv2.cvtColor(warped_rgb, cv2.COLOR_RGB2GRAY), ms_gray)
    paths = {
        "rgbWarped": str(output_dir / "rgb_warped.png"),
        "overlay": str(output_dir / "overlay.png"),
        "difference": str(output_dir / "difference.png"),
    }
    _write_image(paths["rgbWarped"], cv2.cvtColor(warped_rgb, cv2.COLOR_RGB2BGR))
    _write_image(paths["overlay"], cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
    _write_image(paths["difference"], diff)
    return paths


def _require_endpoint_match(name: str, expected: CameraRegistrationEndpoint, actual: CameraRegistrationEndpoint) -> None:
    if expected.width != actual.width or expected.height != actual.height:
        raise RegistrationProfileMismatch(
            f"{REGISTRATION_PROFILE_MISMATCH}: {name} resolution expected="
            f"{expected.width}x{expected.height} actual={actual.width}x{actual.height}"
        )
    if expected.stableId and expected.stableId != actual.stableId:
        raise RegistrationProfileMismatch(
            f"{REGISTRATION_PROFILE_MISMATCH}: {name} stableId expected={expected.stableId} actual={actual.stableId}"
        )
    if expected.serial and expected.serial != actual.serial:
        raise RegistrationProfileMismatch(
            f"{REGISTRATION_PROFILE_MISMATCH}: {name} serial expected={expected.serial} actual={actual.serial}"
        )
    if expected.deviceIndex is not None and expected.deviceIndex != actual.deviceIndex:
        raise RegistrationProfileMismatch(
            f"{REGISTRATION_PROFILE_MISMATCH}: {name} deviceIndex expected={expected.deviceIndex} actual={actual.deviceIndex}"
        )


def _points_array(points: Any, *, name: str) -> np.ndarray:
    arr = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if arr.shape[0] == 0:
        raise RegistrationError(f"{name} is empty")
    if not np.all(np.isfinite(arr)):
        raise RegistrationError(f"{name} contains non-finite values")
    return arr


def _matrix_to_nested_list(matrix: Any) -> list[list[float]]:
    h = np.asarray(matrix, dtype=np.float64)
    if h.shape != (3, 3):
        raise RegistrationError("homography matrix must be 3x3")
    return [[float(value) for value in row] for row in h.tolist()]


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _cv2():
    try:
        import cv2
    except Exception as exc:
        raise RegistrationError("OpenCV is required for geometric registration") from exc
    return cv2


def _grayscale_uint8(image: Any) -> np.ndarray:
    cv2 = _cv2()
    arr = np.asarray(image)
    if arr.ndim == 3:
        if arr.shape[2] >= 3:
            arr = cv2.cvtColor(arr[:, :, :3].astype(np.uint8, copy=False), cv2.COLOR_RGB2GRAY)
        else:
            arr = arr[:, :, 0]
    if arr.ndim != 2:
        raise RegistrationError("image must be 2D grayscale or RGB")
    if arr.dtype == np.uint8:
        return arr
    arr_f = arr.astype(np.float32, copy=False)
    min_value = float(np.nanmin(arr_f)) if arr_f.size else 0.0
    max_value = float(np.nanmax(arr_f)) if arr_f.size else 0.0
    if max_value <= min_value:
        return np.zeros(arr_f.shape, dtype=np.uint8)
    return np.clip((arr_f - min_value) * (255.0 / (max_value - min_value)), 0, 255).astype(np.uint8)


def _rgb_uint8(image: Any) -> np.ndarray:
    gray_or_rgb = np.asarray(image)
    if gray_or_rgb.ndim == 2:
        gray = _grayscale_uint8(gray_or_rgb)
        return np.repeat(gray[:, :, None], 3, axis=2)
    if gray_or_rgb.ndim != 3 or gray_or_rgb.shape[2] < 3:
        raise RegistrationError("RGB image must have shape HxWx3")
    arr = gray_or_rgb[:, :, :3]
    if arr.dtype == np.uint8:
        return arr
    return _grayscale_or_rgb_to_uint8(arr)


def _grayscale_or_rgb_to_uint8(image: np.ndarray) -> np.ndarray:
    arr = image.astype(np.float32, copy=False)
    min_value = float(np.nanmin(arr)) if arr.size else 0.0
    max_value = float(np.nanmax(arr)) if arr.size else 0.0
    if max_value <= min_value:
        return np.zeros(arr.shape, dtype=np.uint8)
    return np.clip((arr - min_value) * (255.0 / (max_value - min_value)), 0, 255).astype(np.uint8)


def _write_image(path: str | Path, image: Any) -> None:
    cv2 = _cv2()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), np.asarray(image))
    if not ok:
        raise RegistrationError(f"failed to write image: {path}")
