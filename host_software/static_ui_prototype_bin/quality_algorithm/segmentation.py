from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Protocol

import numpy as np

from .roi import build_rgb_fruit_mask


@dataclass
class FruitSegmentationResult:
    valid: bool
    algorithm: str
    mask: np.ndarray | None = None
    maskShape: tuple[int, int] | None = None
    foregroundPixelCount: int = 0
    foregroundRatio: float = 0.0
    componentCount: int = 0
    componentAreas: list[int] = field(default_factory=list)
    selectedComponentArea: int = 0
    selectedComponentAreaRatio: float = 0.0
    boundingBox: list[int] | None = None
    centroid: list[float] | None = None
    touchesImageBorder: bool = False
    differenceStats: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    qualityFlags: list[str] = field(default_factory=list)
    errorCode: str = ""
    timingMs: dict[str, float] = field(default_factory=dict)
    referenceId: str = ""
    modelInfo: dict = field(default_factory=dict)

    def to_dict(self, *, include_mask: bool = False) -> dict:
        data = asdict(self)
        if include_mask and self.mask is not None:
            data["mask"] = self.mask.astype(bool).tolist()
        else:
            data.pop("mask", None)
        return data


class FruitSegmenter(Protocol):
    def segment(self, image: np.ndarray, **kwargs) -> FruitSegmentationResult:
        ...


class LegacyColorSegmenter:
    algorithm = "legacy_color"

    def segment(self, image: np.ndarray, **kwargs) -> FruitSegmentationResult:
        started = time.perf_counter()
        mask = build_rgb_fruit_mask(image)
        result = build_result_from_mask(
            mask,
            algorithm=self.algorithm,
            timing_ms={"total": _elapsed_ms(started)},
            model_info={"backend": "legacy_color", "device": "cpu"},
        )
        result.warnings.append("LEGACY_COLOR_THRESHOLD")
        return result


def build_result_from_mask(
    mask: np.ndarray,
    *,
    algorithm: str,
    timing_ms: dict[str, float] | None = None,
    model_info: dict | None = None,
    warnings: list[str] | None = None,
    quality_flags: list[str] | None = None,
    error_code: str | None = None,
    reference_id: str = "",
    difference_stats: dict[str, float] | None = None,
) -> FruitSegmentationResult:
    bool_mask = np.asarray(mask).astype(bool)
    count = int(np.count_nonzero(bool_mask))
    return FruitSegmentationResult(
        valid=count > 0,
        algorithm=algorithm,
        mask=bool_mask,
        maskShape=tuple(int(v) for v in bool_mask.shape),
        foregroundPixelCount=count,
        foregroundRatio=float(count / bool_mask.size) if bool_mask.size else 0.0,
        componentCount=1 if count else 0,
        componentAreas=[count] if count else [],
        selectedComponentArea=count,
        selectedComponentAreaRatio=float(count / bool_mask.size) if bool_mask.size else 0.0,
        boundingBox=_bounding_box(bool_mask),
        centroid=_centroid(bool_mask),
        touchesImageBorder=_touches_border(bool_mask, 0),
        differenceStats=difference_stats or {},
        warnings=list(warnings or []),
        qualityFlags=list(quality_flags or []),
        errorCode=error_code if error_code is not None else ("" if count else "FRUIT_NOT_DETECTED"),
        timingMs=timing_ms or {},
        referenceId=reference_id,
        modelInfo=model_info or {},
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


def _touches_border(mask: np.ndarray, margin_px: int) -> bool:
    if mask.size == 0 or not np.any(mask):
        return False
    margin = max(0, int(margin_px))
    h, w = mask.shape[:2]
    return bool(
        np.any(mask[: margin + 1, :])
        or np.any(mask[max(0, h - margin - 1) :, :])
        or np.any(mask[:, : margin + 1])
        or np.any(mask[:, max(0, w - margin - 1) :])
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)
