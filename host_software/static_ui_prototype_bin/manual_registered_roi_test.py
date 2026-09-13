from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from quality_algorithm.registered_roi import RegisteredRoiConfig, build_registered_multispectral_roi
from quality_algorithm.registration import CameraRegistrationEndpoint, RegistrationProfile, warp_mask_rgb_to_multispectral


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and visualize a registered DVP2 ROI from an RGB binary mask.")
    parser.add_argument("--rgb-mask", required=True, help="RGB binary mask image path")
    parser.add_argument("--multispectral-image", required=True, help="DVP2 grayscale image used for overlay diagnostics")
    parser.add_argument("--registration-profile", required=True, help="RegistrationProfile JSON path")
    parser.add_argument("--output-dir", default="manual_registered_roi_output", help="Output directory")
    parser.add_argument("--erosion-px", type=int, default=2, help="DVP2-space erosion radius in pixels")
    parser.add_argument("--min-retained-pixel-count", type=int, default=16)
    parser.add_argument("--min-retained-ratio", type=float, default=0.25)
    parser.add_argument("--rgb-stable-id", default="")
    parser.add_argument("--rgb-device-index", type=int, default=None)
    parser.add_argument("--dvp2-stable-id", default="")
    parser.add_argument("--dvp2-device-index", type=int, default=None)
    parser.add_argument("--dvp2-serial", default="")
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    profile = RegistrationProfile.from_json_file(args.registration_profile)
    rgb_mask = _load_mask(args.rgb_mask)
    ms = _load_gray(args.multispectral_image)
    rgb_endpoint = CameraRegistrationEndpoint(
        stableId=args.rgb_stable_id,
        deviceIndex=args.rgb_device_index,
        width=rgb_mask.shape[1],
        height=rgb_mask.shape[0],
    )
    ms_endpoint = CameraRegistrationEndpoint(
        stableId=args.dvp2_stable_id,
        deviceIndex=args.dvp2_device_index,
        serial=args.dvp2_serial,
        width=ms.shape[1],
        height=ms.shape[0],
    )
    config = RegisteredRoiConfig(
        erosionPx=args.erosion_px,
        minRetainedPixelCount=args.min_retained_pixel_count,
        minRetainedRatio=args.min_retained_ratio,
    )
    warped = warp_mask_rgb_to_multispectral(rgb_mask, profile, ms.shape[:2], rgb=rgb_endpoint, multispectral=ms_endpoint)
    result = build_registered_multispectral_roi(
        rgb_mask=rgb_mask,
        registration_profile=profile,
        rgb_endpoint=rgb_endpoint,
        multispectral_endpoint=ms_endpoint,
        target_shape=ms.shape[:2],
        config=config,
    )
    eroded = result.mask if result.mask is not None else np.zeros(ms.shape[:2], dtype=bool)

    _save_mask(out / "rgb_mask.png", rgb_mask)
    _save_mask(out / "warped_mask.png", warped)
    _save_mask(out / "eroded_mask.png", eroded)
    Image.fromarray(_uint8_gray(ms)).save(out / "multispectral.png")
    _save_overlay(out / "overlay_warped.png", ms, warped, color=(255, 0, 0))
    _save_overlay(out / "overlay_eroded.png", ms, eroded, color=(0, 255, 80))
    (out / "diagnostics.json").write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"valid": result.valid, "errorCode": result.errorCode, "outputDir": str(out)}, ensure_ascii=False))
    return 0 if result.valid else 2


def _load_mask(path: str | Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("L")) > 0


def _load_gray(path: str | Path) -> np.ndarray:
    with Image.open(path) as image:
        arr = np.asarray(image)
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    return arr


def _uint8_gray(image: np.ndarray) -> np.ndarray:
    arr = image.astype(np.float32, copy=False)
    if arr.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    lo = float(np.nanmin(arr))
    hi = float(np.nanmax(arr))
    if hi <= lo:
        return np.zeros(arr.shape, dtype=np.uint8)
    return np.clip((arr - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)


def _save_mask(path: Path, mask: np.ndarray) -> None:
    Image.fromarray(np.asarray(mask).astype(np.uint8) * 255).save(path)


def _save_overlay(path: Path, gray: np.ndarray, mask: np.ndarray, *, color: tuple[int, int, int]) -> None:
    base = np.repeat(_uint8_gray(gray)[:, :, None], 3, axis=2)
    contour = _contour(mask)
    overlay = base.copy()
    overlay[contour] = np.asarray(color, dtype=np.uint8)
    Image.fromarray(overlay).save(path)


def _contour(mask: np.ndarray) -> np.ndarray:
    bool_mask = np.asarray(mask).astype(bool)
    padded = np.pad(bool_mask, 1, mode="constant", constant_values=False)
    interior = (
        padded[1:-1, 1:-1]
        & padded[:-2, 1:-1]
        & padded[2:, 1:-1]
        & padded[1:-1, :-2]
        & padded[1:-1, 2:]
    )
    return bool_mask & ~interior


if __name__ == "__main__":
    raise SystemExit(main())
