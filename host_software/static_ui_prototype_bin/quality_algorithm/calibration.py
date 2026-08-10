from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError


UNCALIBRATED = "UNCALIBRATED"
CALIBRATED = "CALIBRATED"
CALIBRATION_MISSING = "CALIBRATION_MISSING"


class CalibrationError(RuntimeError):
    pass


def load_grayscale_float(path: str | Path) -> np.ndarray:
    try:
        with Image.open(path) as image:
            arr = np.asarray(image)
    except (OSError, UnidentifiedImageError) as exc:
        raise CalibrationError(f"cannot read image: {path}") from exc
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    return arr.astype(np.float32, copy=False)


def reflectance_correction(
    sample: np.ndarray,
    dark: np.ndarray,
    white: np.ndarray,
    *,
    eps: float = 1e-6,
    clip_range: tuple[float, float] = (0.0, 2.0),
) -> np.ndarray:
    sample_f = sample.astype(np.float32, copy=False)
    dark_f = dark.astype(np.float32, copy=False)
    white_f = white.astype(np.float32, copy=False)
    denom = white_f - dark_f
    safe = np.where(np.abs(denom) < eps, np.nan, denom)
    corrected = (sample_f - dark_f) / safe
    corrected = np.nan_to_num(corrected, nan=0.0, posinf=clip_range[1], neginf=clip_range[0])
    return np.clip(corrected, clip_range[0], clip_range[1]).astype(np.float32)


def normalize_uncalibrated(image: np.ndarray) -> np.ndarray:
    arr = image.astype(np.float32, copy=False)
    max_value = float(np.nanmax(arr)) if arr.size else 0.0
    if max_value <= 0:
        return arr
    return np.clip(arr / max_value, 0.0, 1.0).astype(np.float32)

