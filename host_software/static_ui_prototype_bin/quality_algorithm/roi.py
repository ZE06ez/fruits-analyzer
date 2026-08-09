from __future__ import annotations

import numpy as np
from PIL import Image


def build_rgb_fruit_mask(rgb: np.ndarray) -> np.ndarray:
    rgb_f = rgb.astype(np.float32)
    r = rgb_f[:, :, 0]
    g = rgb_f[:, :, 1]
    b = rgb_f[:, :, 2]
    maxc = rgb_f.max(axis=2)
    minc = rgb_f.min(axis=2)
    chroma = maxc - minc
    mask = (
        ((g > 35) & (g > r * 0.65) & (g > b * 0.75))
        | ((r > 45) & (g > 35) & (b < 190))
        | ((chroma > 18) & (maxc > 32))
    )
    if int(np.count_nonzero(mask)) < 16:
        yy, xx = np.indices(rgb.shape[:2])
        cy, cx = np.array(rgb.shape[:2]) / 2.0
        mask = ((yy - cy) / (rgb.shape[0] * 0.38)) ** 2 + ((xx - cx) / (rgb.shape[1] * 0.34)) ** 2 <= 1.0
    return _keep_largest_component(mask.astype(bool))


def apply_mask_to_image(image: np.ndarray, mask: np.ndarray, *, registration_mode: str = "identity") -> tuple[np.ndarray, np.ndarray]:
    if registration_mode not in {"identity", "calibrated"}:
        raise ValueError(f"unsupported registration_mode: {registration_mode}")
    if registration_mode == "calibrated":
        raise NotImplementedError("calibrated RGB-to-multispectral registration is not connected yet")
    target_mask = mask
    if mask.shape != image.shape[:2]:
        pil = Image.fromarray(mask.astype(np.uint8) * 255)
        pil = pil.resize((image.shape[1], image.shape[0]), Image.Resampling.NEAREST)
        target_mask = np.asarray(pil) > 0
    return image[target_mask], target_mask


def roi_mean(image: np.ndarray, mask: np.ndarray) -> float:
    pixels, _mask = apply_mask_to_image(image, mask)
    if pixels.size == 0:
        raise ValueError("empty ROI")
    return float(np.mean(pixels.astype(np.float32)))


def _keep_largest_component(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return mask.astype(bool)
    pending = set(zip(ys.tolist(), xs.tolist()))
    best: list[tuple[int, int]] = []
    while pending:
        start = pending.pop()
        stack = [start]
        pixels = [start]
        while stack:
            y, x = stack.pop()
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if (ny, nx) in pending:
                    pending.remove((ny, nx))
                    stack.append((ny, nx))
                    pixels.append((ny, nx))
        if len(pixels) > len(best):
            best = pixels
    result = np.zeros(mask.shape, dtype=bool)
    if best:
        py, px = zip(*best)
        result[np.array(py), np.array(px)] = True
    return result

