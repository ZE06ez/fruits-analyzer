from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class FocusRoi:
    mode: str = "center"
    center_fraction: float = 0.6

    def resolve(self, shape: tuple[int, int]) -> dict[str, int | str | float]:
        height, width = int(shape[0]), int(shape[1])
        mode = (self.mode or "center").lower()
        if mode == "full":
            return {"mode": "full", "x": 0, "y": 0, "width": width, "height": height}
        if mode == "mask":
            raise ValueError("mask ROI is reserved for future focus evaluation")
        fraction = min(max(float(self.center_fraction), 0.1), 1.0)
        roi_width = max(1, int(round(width * fraction)))
        roi_height = max(1, int(round(height * fraction)))
        x = max(0, (width - roi_width) // 2)
        y = max(0, (height - roi_height) // 2)
        return {"mode": "center", "x": x, "y": y, "width": roi_width, "height": roi_height}


@dataclass(frozen=True)
class FocusThresholdConfig:
    blurry_below: float | None = None
    sharp_above: float | None = None
    provisional: bool = True

    def classify(self, score: float) -> str:
        if not np.isfinite(score):
            return "unknown"
        if self.blurry_below is None or self.sharp_above is None:
            return "unknown"
        if score >= float(self.sharp_above):
            return "sharp"
        if score < float(self.blurry_below):
            return "blurry"
        return "acceptable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "blurryBelow": self.blurry_below,
            "sharpAbove": self.sharp_above,
            "provisional": self.provisional,
        }


@dataclass(frozen=True)
class FocusMetrics:
    tenengrad: float
    laplacian_variance: float
    edge_density: float

    def to_dict(self) -> dict[str, float]:
        return {
            "tenengrad": self.tenengrad,
            "laplacianVariance": self.laplacian_variance,
            "edgeDensity": self.edge_density,
        }


@dataclass(frozen=True)
class FocusResult:
    status: str
    classification: str
    focus_score: float | None
    metrics: FocusMetrics | None
    roi: dict[str, Any]
    frame: dict[str, Any]
    thresholds: FocusThresholdConfig
    band_id: str | None = None
    wavelength_nm: int | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "classification": self.classification,
            "focusScore": self.focus_score,
            "metrics": self.metrics.to_dict() if self.metrics else {},
            "roi": dict(self.roi),
            "frame": dict(self.frame),
            "thresholds": self.thresholds.to_dict(),
            "bandId": self.band_id,
            "wavelengthNm": self.wavelength_nm,
            "error": self.error,
        }


class FocusEvaluator:
    """Computes focus metrics from raw two-dimensional scientific mono frames."""

    def __init__(
        self,
        *,
        thresholds: FocusThresholdConfig | None = None,
        default_roi: FocusRoi | None = None,
        cv2_module: Any | None = None,
    ) -> None:
        self.thresholds = thresholds or FocusThresholdConfig()
        self.default_roi = default_roi or FocusRoi()
        self._cv2 = cv2_module

    def evaluate(
        self,
        frame: Any,
        *,
        roi: FocusRoi | str | dict[str, Any] | None = None,
        band_id: str | None = None,
        wavelength_nm: int | None = None,
    ) -> FocusResult:
        data = getattr(frame, "data", frame)
        metadata = getattr(frame, "metadata", {}) or {}
        array = np.asarray(data)
        frame_info = {
            "width": int(array.shape[1]) if array.ndim >= 2 else None,
            "height": int(array.shape[0]) if array.ndim >= 2 else None,
            "dtype": str(array.dtype),
            "pixelFormat": metadata.get("pixelFormat", ""),
        }
        if array.ndim != 2:
            raise ValueError("focus evaluation requires a two-dimensional mono frame")
        if array.size == 0 or array.shape[0] == 0 or array.shape[1] == 0:
            raise ValueError("focus evaluation requires a non-empty frame")
        if array.dtype not in (np.uint8, np.uint16):
            raise ValueError("focus evaluation supports uint8 or uint16 mono frames")

        roi_config = self._coerce_roi(roi)
        roi_info = roi_config.resolve((array.shape[0], array.shape[1]))
        y, x = int(roi_info["y"]), int(roi_info["x"])
        height, width = int(roi_info["height"]), int(roi_info["width"])
        sample = array[y:y + height, x:x + width]
        if sample.size == 0:
            raise ValueError("focus ROI is empty")

        sample_f32 = sample.astype(np.float32, copy=False)
        gx, gy = self._sobel(sample_f32)
        gradient_sq = gx * gx + gy * gy
        tenengrad = float(np.mean(gradient_sq))
        laplacian = self._laplacian(sample_f32)
        laplacian_variance = float(np.var(laplacian))
        gradient_mag = np.sqrt(gradient_sq)
        edge_threshold = self._edge_threshold(sample_f32, gradient_mag)
        edge_density = float(np.mean(gradient_mag > edge_threshold))

        metrics = FocusMetrics(
            tenengrad=self._finite_or_zero(tenengrad),
            laplacian_variance=self._finite_or_zero(laplacian_variance),
            edge_density=self._finite_or_zero(edge_density),
        )
        focus_score = metrics.tenengrad
        return FocusResult(
            status="ok",
            classification=self.thresholds.classify(focus_score),
            focus_score=focus_score,
            metrics=metrics,
            roi=roi_info,
            frame=frame_info,
            thresholds=self.thresholds,
            band_id=band_id,
            wavelength_nm=wavelength_nm,
        )

    def safe_evaluate(self, frame: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return self.evaluate(frame, **kwargs).to_dict()
        except Exception as exc:
            data = getattr(frame, "data", frame)
            array = np.asarray(data)
            try:
                roi_mode = self._coerce_roi(kwargs.get("roi")).mode
            except Exception:
                roi_mode = self.default_roi.mode
            return {
                "status": "evaluation_failed",
                "classification": "unknown",
                "focusScore": None,
                "metrics": {},
                "roi": {"mode": roi_mode},
                "frame": {
                    "width": int(array.shape[1]) if array.ndim >= 2 else None,
                    "height": int(array.shape[0]) if array.ndim >= 2 else None,
                    "dtype": str(array.dtype),
                    "pixelFormat": (getattr(frame, "metadata", {}) or {}).get("pixelFormat", ""),
                },
                "thresholds": self.thresholds.to_dict(),
                "bandId": kwargs.get("band_id"),
                "wavelengthNm": kwargs.get("wavelength_nm"),
                "error": str(exc),
            }

    def _coerce_roi(self, roi: FocusRoi | str | dict[str, Any] | None) -> FocusRoi:
        if roi is None:
            return self.default_roi
        if isinstance(roi, FocusRoi):
            return roi
        if isinstance(roi, str):
            return FocusRoi(mode=roi)
        if isinstance(roi, dict):
            return FocusRoi(
                mode=str(roi.get("mode") or self.default_roi.mode),
                center_fraction=float(roi.get("centerFraction") or roi.get("center_fraction") or self.default_roi.center_fraction),
            )
        raise ValueError("invalid focus ROI")

    def _sobel(self, sample: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        cv2 = self._cv2 or _try_import_cv2()
        if cv2 is not None:
            return (
                cv2.Sobel(sample, cv2.CV_32F, 1, 0, ksize=3),
                cv2.Sobel(sample, cv2.CV_32F, 0, 1, ksize=3),
            )
        padded = np.pad(sample, 1, mode="edge")
        gx = (
            -padded[:-2, :-2] + padded[:-2, 2:]
            - 2 * padded[1:-1, :-2] + 2 * padded[1:-1, 2:]
            - padded[2:, :-2] + padded[2:, 2:]
        )
        gy = (
            -padded[:-2, :-2] - 2 * padded[:-2, 1:-1] - padded[:-2, 2:]
            + padded[2:, :-2] + 2 * padded[2:, 1:-1] + padded[2:, 2:]
        )
        return gx.astype(np.float32, copy=False), gy.astype(np.float32, copy=False)

    def _laplacian(self, sample: np.ndarray) -> np.ndarray:
        cv2 = self._cv2 or _try_import_cv2()
        if cv2 is not None:
            return cv2.Laplacian(sample, cv2.CV_32F, ksize=3)
        padded = np.pad(sample, 1, mode="edge")
        result = (
            -4 * padded[1:-1, 1:-1]
            + padded[:-2, 1:-1]
            + padded[2:, 1:-1]
            + padded[1:-1, :-2]
            + padded[1:-1, 2:]
        )
        return result.astype(np.float32, copy=False)

    @staticmethod
    def _edge_threshold(sample: np.ndarray, gradient_mag: np.ndarray) -> float:
        dynamic_range = float(np.max(sample) - np.min(sample))
        percentile = float(np.percentile(gradient_mag, 75)) if gradient_mag.size else 0.0
        return max(dynamic_range * 0.02, percentile, 1.0)

    @staticmethod
    def _finite_or_zero(value: float) -> float:
        return float(value) if np.isfinite(value) else 0.0


def _try_import_cv2() -> Any | None:
    try:
        import cv2

        return cv2
    except Exception:
        return None
