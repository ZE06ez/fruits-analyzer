from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from quality_algorithm.background_reference import create_background_reference
from quality_algorithm.background_segmenter import (
    BackgroundReferenceSegmenter,
    BackgroundSegmentationConfig,
    clean_binary_mask,
    compute_difference_map,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual background-reference fruit segmentation test.")
    parser.add_argument("--background-image", required=True)
    parser.add_argument("--sample-image", required=True)
    parser.add_argument("--output-dir", default="manual_background_segmentation_output")
    parser.add_argument("--threshold", type=float, default=26.0)
    parser.add_argument("--min-area-ratio", type=float, default=0.01)
    parser.add_argument("--open-kernel", type=int, default=3)
    parser.add_argument("--close-kernel", type=int, default=7)
    args = parser.parse_args()

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    background = load_rgb(args.background_image)
    sample = load_rgb(args.sample_image)
    config = BackgroundSegmentationConfig(
        threshold=args.threshold,
        minAreaRatio=args.min_area_ratio,
        openKernel=args.open_kernel,
        closeKernel=args.close_kernel,
    )
    reference = create_background_reference(args.background_image, reference_id="manual_background_reference")
    result = BackgroundReferenceSegmenter(config).segment(sample, background_reference=reference, background_image=background)
    difference = compute_difference_map(sample, background, config)
    raw_mask = difference >= config.threshold
    clean_mask = clean_binary_mask(raw_mask, config)

    Image.fromarray(background).save(output / "background.png")
    Image.fromarray(sample).save(output / "sample.png")
    Image.fromarray(normalize_to_uint8(difference)).save(output / "difference_map.png")
    Image.fromarray(raw_mask.astype(np.uint8) * 255, mode="L").save(output / "raw_mask.png")
    Image.fromarray(clean_mask.astype(np.uint8) * 255, mode="L").save(output / "clean_mask.png")
    Image.fromarray(overlay(sample, result.mask if result.mask is not None else clean_mask)).save(output / "overlay.png")
    (output / "diagnostics.json").write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"background segmentation {'OK' if result.valid else 'FAILED'}: {result.errorCode or result.foregroundPixelCount}")
    return 0 if result.valid else 2


def load_rgb(path: str | Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def normalize_to_uint8(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float32)
    high = float(np.max(arr))
    if high <= 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    return np.clip(arr / high * 255.0, 0, 255).astype(np.uint8)


def overlay(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    result = image.copy()
    bool_mask = np.asarray(mask).astype(bool)
    result[bool_mask] = (0.55 * result[bool_mask] + 0.45 * np.array([0, 255, 120])).astype(np.uint8)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
