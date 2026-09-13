from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .background_reference import BackgroundReference
from .segmentation import FruitSegmentationResult


FRUIT_NOT_DETECTED = "FRUIT_NOT_DETECTED"
FRUIT_MASK_TOO_SMALL = "FRUIT_MASK_TOO_SMALL"
FRUIT_MASK_TOO_LARGE = "FRUIT_MASK_TOO_LARGE"
FRUIT_TOUCHES_BORDER = "FRUIT_TOUCHES_BORDER"
MULTIPLE_LARGE_FOREGROUNDS = "MULTIPLE_LARGE_FOREGROUNDS"
BACKGROUND_DIFFERENCE_TOO_LOW = "BACKGROUND_DIFFERENCE_TOO_LOW"


@dataclass
class BackgroundSegmentationConfig:
    rgbWeight: float = 0.45
    labWeight: float = 0.55
    threshold: float = 26.0
    minAreaRatio: float = 0.01
    maxAreaRatio: float = 0.75
    minDifferenceP95: float = 8.0
    openKernel: int = 3
    closeKernel: int = 7
    fillHoles: bool = True
    borderMarginPx: int = 2
    largeComponentRatio: float = 0.08


class BackgroundReferenceSegmenter:
    algorithm = "background_reference"

    def __init__(self, config: BackgroundSegmentationConfig | None = None) -> None:
        self.config = config or BackgroundSegmentationConfig()

    def segment(
        self,
        image: np.ndarray,
        *,
        background_reference: BackgroundReference | None = None,
        background_image: np.ndarray | None = None,
        camera_metadata: dict | None = None,
    ) -> FruitSegmentationResult:
        started = time.perf_counter()
        sample = _normalize_rgb(image)
        reference_id = background_reference.referenceId if background_reference else ""
        if background_image is None and background_reference is not None:
            background_image = _load_rgb(background_reference.imagePath)
        if background_image is None:
            return self._error("BACKGROUND_REFERENCE_MISSING", sample.shape[:2], started, reference_id)
        background = _normalize_rgb(background_image)
        if background.shape != sample.shape:
            return self._error("BACKGROUND_REFERENCE_RESOLUTION_MISMATCH", sample.shape[:2], started, reference_id)

        diff_started = time.perf_counter()
        difference = compute_difference_map(sample, background, self.config)
        raw_mask = difference >= float(self.config.threshold)
        clean_mask = clean_binary_mask(raw_mask, self.config)
        components = component_stats(clean_mask)
        selected, multiple_large = select_foreground_component(components, clean_mask.shape, self.config)
        selected_mask = np.zeros(clean_mask.shape, dtype=bool)
        if selected is not None:
            selected_mask[components["labels"] == selected["label"]] = True
        stats = {
            "mean": round(float(np.mean(difference)), 4),
            "p95": round(float(np.percentile(difference, 95)), 4),
            "max": round(float(np.max(difference)), 4),
            "threshold": float(self.config.threshold),
        }
        flags: list[str] = []
        warnings: list[str] = []
        error = ""
        total = int(selected_mask.size)
        area = int(np.count_nonzero(selected_mask))
        area_ratio = float(area / total) if total else 0.0
        touches = _touches_border(selected_mask, self.config.borderMarginPx)
        if stats["p95"] < self.config.minDifferenceP95:
            flags.append(BACKGROUND_DIFFERENCE_TOO_LOW)
            warnings.append(BACKGROUND_DIFFERENCE_TOO_LOW)
        if area == 0:
            error = FRUIT_NOT_DETECTED
        elif area_ratio < self.config.minAreaRatio:
            error = FRUIT_MASK_TOO_SMALL
        elif area_ratio > self.config.maxAreaRatio:
            error = FRUIT_MASK_TOO_LARGE
        elif touches:
            error = FRUIT_TOUCHES_BORDER
        elif multiple_large:
            error = MULTIPLE_LARGE_FOREGROUNDS
        if error:
            flags.append(error)
        component_areas = [int(item["area"]) for item in components["components"]]
        return FruitSegmentationResult(
            valid=not error,
            algorithm=self.algorithm,
            mask=selected_mask,
            maskShape=tuple(int(v) for v in selected_mask.shape),
            foregroundPixelCount=area,
            foregroundRatio=area_ratio,
            componentCount=len(component_areas),
            componentAreas=component_areas,
            selectedComponentArea=area,
            selectedComponentAreaRatio=area_ratio,
            boundingBox=_bounding_box(selected_mask),
            centroid=_centroid(selected_mask),
            touchesImageBorder=touches,
            differenceStats=stats,
            warnings=warnings,
            qualityFlags=flags,
            errorCode=error,
            timingMs={
                "difference": round((time.perf_counter() - diff_started) * 1000.0, 3),
                "total": round((time.perf_counter() - started) * 1000.0, 3),
            },
            referenceId=reference_id,
            modelInfo={"backend": "background_reference", "cameraMetadataProvided": bool(camera_metadata)},
        )

    def _error(self, code: str, shape: tuple[int, int], started: float, reference_id: str) -> FruitSegmentationResult:
        return FruitSegmentationResult(
            valid=False,
            algorithm=self.algorithm,
            mask=np.zeros(shape, dtype=bool),
            maskShape=shape,
            errorCode=code,
            qualityFlags=[code],
            timingMs={"total": round((time.perf_counter() - started) * 1000.0, 3)},
            referenceId=reference_id,
        )


def compute_difference_map(sample_rgb: np.ndarray, background_rgb: np.ndarray, config: BackgroundSegmentationConfig) -> np.ndarray:
    sample = sample_rgb.astype(np.float32)
    background = background_rgb.astype(np.float32)
    rgb_diff = np.mean(np.abs(sample - background), axis=2)
    lab_diff = _lab_distance(sample_rgb, background_rgb)
    total_weight = max(1e-6, float(config.rgbWeight) + float(config.labWeight))
    return ((float(config.rgbWeight) * rgb_diff) + (float(config.labWeight) * lab_diff)) / total_weight


def clean_binary_mask(mask: np.ndarray, config: BackgroundSegmentationConfig) -> np.ndarray:
    result = np.asarray(mask).astype(bool)
    result = _morphology(result, "open", config.openKernel)
    result = _morphology(result, "close", config.closeKernel)
    if config.fillHoles:
        result = _fill_holes(result)
    return result


def component_stats(mask: np.ndarray) -> dict:
    try:
        import cv2

        count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
        components = []
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            components.append({
                "label": label,
                "area": area,
                "bbox": [
                    int(stats[label, cv2.CC_STAT_LEFT]),
                    int(stats[label, cv2.CC_STAT_TOP]),
                    int(stats[label, cv2.CC_STAT_LEFT] + stats[label, cv2.CC_STAT_WIDTH] - 1),
                    int(stats[label, cv2.CC_STAT_TOP] + stats[label, cv2.CC_STAT_HEIGHT] - 1),
                ],
                "centroid": [float(centroids[label][0]), float(centroids[label][1])],
            })
        return {"labels": labels, "components": components}
    except Exception:
        return _component_stats_numpy(mask)


def select_foreground_component(components: dict, shape: tuple[int, int], config: BackgroundSegmentationConfig) -> tuple[dict | None, bool]:
    total = int(shape[0] * shape[1])
    min_area = max(1, int(total * config.minAreaRatio))
    max_area = max(1, int(total * config.maxAreaRatio))
    large_area = max(1, int(total * config.largeComponentRatio))
    all_components = sorted(components["components"], key=lambda item: item["area"], reverse=True)
    reasonable = [item for item in all_components if min_area <= int(item["area"]) <= max_area]
    multiple_large = len([item for item in all_components if int(item["area"]) >= large_area]) > 1
    if reasonable:
        return reasonable[0], multiple_large
    return (all_components[0], multiple_large) if all_components else (None, multiple_large)


def _normalize_rgb(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 2:
        arr = np.repeat(arr[:, :, None], 3, axis=2)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError("image must be RGB-like")
    return arr[:, :, :3].astype(np.uint8, copy=False)


def _load_rgb(path: str | Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def _lab_distance(sample_rgb: np.ndarray, background_rgb: np.ndarray) -> np.ndarray:
    try:
        import cv2

        sample_lab = cv2.cvtColor(sample_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        background_lab = cv2.cvtColor(background_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        return np.linalg.norm(sample_lab - background_lab, axis=2)
    except Exception:
        return np.linalg.norm(sample_rgb.astype(np.float32) - background_rgb.astype(np.float32), axis=2)


def _morphology(mask: np.ndarray, operation: str, kernel_size: int) -> np.ndarray:
    size = int(kernel_size or 0)
    if size <= 1:
        return mask.astype(bool)
    if size % 2 == 0:
        size += 1
    try:
        import cv2

        kernel = np.ones((size, size), dtype=np.uint8)
        op = cv2.MORPH_OPEN if operation == "open" else cv2.MORPH_CLOSE
        return cv2.morphologyEx(mask.astype(np.uint8), op, kernel).astype(bool)
    except Exception:
        return mask.astype(bool)


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    try:
        import cv2

        inverted = (~mask).astype(np.uint8)
        h, w = mask.shape
        flood = inverted.copy()
        cv2.floodFill(flood, np.zeros((h + 2, w + 2), dtype=np.uint8), (0, 0), 2)
        holes = flood == 1
        return mask | holes
    except Exception:
        return mask


def _component_stats_numpy(mask: np.ndarray) -> dict:
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    components = []
    label = 0
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or labels[y, x]:
                continue
            label += 1
            stack = [(y, x)]
            labels[y, x] = label
            xs = []
            ys = []
            while stack:
                cy, cx = stack.pop()
                xs.append(cx)
                ys.append(cy)
                for ny in range(max(0, cy - 1), min(h, cy + 2)):
                    for nx in range(max(0, cx - 1), min(w, cx + 2)):
                        if mask[ny, nx] and not labels[ny, nx]:
                            labels[ny, nx] = label
                            stack.append((ny, nx))
            components.append({
                "label": label,
                "area": len(xs),
                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                "centroid": [float(np.mean(xs)), float(np.mean(ys))],
            })
    return {"labels": labels, "components": components}


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
    h, w = mask.shape
    return bool(
        np.any(mask[: margin + 1, :])
        or np.any(mask[max(0, h - margin - 1) :, :])
        or np.any(mask[:, : margin + 1])
        or np.any(mask[:, max(0, w - margin - 1) :])
    )
