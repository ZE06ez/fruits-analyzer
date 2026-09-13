from __future__ import annotations

import numpy as np


def compute_mask_iou(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    a = np.asarray(mask_a).astype(bool)
    b = np.asarray(mask_b).astype(bool)
    if a.shape != b.shape:
        raise ValueError("mask shapes must match")
    union = int(np.count_nonzero(a | b))
    if union == 0:
        return 1.0
    return float(np.count_nonzero(a & b) / union)


def compute_mask_dice(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    a = np.asarray(mask_a).astype(bool)
    b = np.asarray(mask_b).astype(bool)
    if a.shape != b.shape:
        raise ValueError("mask shapes must match")
    denom = int(np.count_nonzero(a) + np.count_nonzero(b))
    if denom == 0:
        return 1.0
    return float((2 * np.count_nonzero(a & b)) / denom)
