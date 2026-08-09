from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

from .calibration import CALIBRATED, UNCALIBRATED, load_grayscale_float, normalize_uncalibrated, reflectance_correction
from .filters import FilterBand, enabled_bands, expected_wavelengths
from .roi import apply_mask_to_image, build_rgb_fruit_mask


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


class FeatureExtractionError(RuntimeError):
    pass


@dataclass
class FeatureRecord:
    sample_id: str
    wavelengths: list[int]
    features: list[float]
    calibrated: bool
    roi_pixel_count: int
    source_dir: str
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def list_images(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS])


def parse_wavelength_from_name(name: str) -> int | None:
    matches = re.findall(r"(?<!\d)(\d{3,4})(?:\s*nm)?(?!\d)", name.lower())
    if not matches:
        return None
    value = int(matches[-1])
    if 300 <= value <= 2500:
        return value
    return None


def map_images_by_wavelength(folder: Path) -> dict[int, Path]:
    mapped: dict[int, Path] = {}
    for path in list_images(folder):
        wavelength = parse_wavelength_from_name(path.stem)
        if wavelength is not None and wavelength not in mapped:
            mapped[wavelength] = path
    return mapped


def inspect_sample_structure(
    sample_dir: str | Path,
    *,
    rgb_dir: str = "rgb",
    spectral_dir: str = "multispectral",
    filters: list[FilterBand] | None = None,
) -> dict:
    root = Path(sample_dir).expanduser()
    expected = expected_wavelengths(filters)
    report = {
        "sample_dir": str(root),
        "rgb_dir": str(root / rgb_dir),
        "multispectral_dir": str(root / spectral_dir),
        "rgb_count": 0,
        "multispectral_count": 0,
        "expected_bands": expected,
        "available_bands": [],
        "missing_bands": expected.copy(),
        "unexpected_bands": [],
        "bad_images": [],
        "calibration_status": "missing",
        "complete": False,
        "valid": False,
        "warnings": [],
    }
    if not root.exists() or not root.is_dir():
        report["warnings"].append("sample directory does not exist")
        return report
    rgb_path = root / rgb_dir
    spectral_path = root / spectral_dir
    rgb_files = list_images(rgb_path)
    spectral_files = list_images(spectral_path)
    report["rgb_count"] = len(rgb_files)
    report["multispectral_count"] = len(spectral_files)
    report["bad_images"] = _bad_images(rgb_files, "RGB") + _bad_images(spectral_files, "L")

    by_band = map_images_by_wavelength(spectral_path)
    available = sorted(by_band)
    report["available_bands"] = available
    report["missing_bands"] = [band for band in expected if band not in by_band]
    report["unexpected_bands"] = [band for band in available if band not in expected]
    report["calibration_status"] = calibration_status(root, expected)

    if not rgb_path.exists():
        report["warnings"].append("missing rgb directory")
    elif not rgb_files:
        report["warnings"].append("no RGB images")
    if not spectral_path.exists():
        report["warnings"].append("missing multispectral directory")
    elif not spectral_files:
        report["warnings"].append("no multispectral images")
    if report["missing_bands"]:
        report["warnings"].append("missing enabled multispectral bands")
    if report["bad_images"]:
        report["warnings"].append("unreadable image files exist")

    report["valid"] = bool(rgb_files and spectral_files and not report["bad_images"])
    report["complete"] = bool(report["valid"] and not report["missing_bands"])
    return report


def calibration_status(root: Path, expected: list[int]) -> str:
    dark = root / "calibration" / "dark"
    white = root / "calibration" / "white"
    if not dark.exists() or not white.exists():
        return "missing"
    dark_bands = set(map_images_by_wavelength(dark))
    white_bands = set(map_images_by_wavelength(white))
    expected_set = set(expected)
    if expected_set and expected_set <= dark_bands and expected_set <= white_bands:
        return "complete"
    if dark_bands or white_bands:
        return "partial"
    return "missing"


def extract_feature_record(
    sample_dir: str | Path,
    *,
    sample_id: str | None = None,
    filters: list[FilterBand] | None = None,
    rgb_dir: str = "rgb",
    spectral_dir: str = "multispectral",
    allow_uncalibrated: bool = True,
    registration_mode: str = "identity",
) -> FeatureRecord:
    root = Path(sample_dir).expanduser()
    bands = enabled_bands(filters)
    report = inspect_sample_structure(root, rgb_dir=rgb_dir, spectral_dir=spectral_dir, filters=filters)
    if not report["valid"]:
        raise FeatureExtractionError("; ".join(report["warnings"]) or "invalid sample data")
    if report["missing_bands"]:
        missing = ", ".join(f"{band} nm" for band in report["missing_bands"])
        raise FeatureExtractionError(f"MODEL_INPUT_MISMATCH: Missing wavelength: {missing}")

    rgb_files = list_images(root / rgb_dir)
    with Image.open(rgb_files[0]) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    mask = build_rgb_fruit_mask(rgb)
    if np.count_nonzero(mask) == 0:
        raise FeatureExtractionError("empty ROI")

    spectral_map = map_images_by_wavelength(root / spectral_dir)
    dark_map = map_images_by_wavelength(root / "calibration" / "dark")
    white_map = map_images_by_wavelength(root / "calibration" / "white")

    values: list[float] = []
    wavelengths: list[int] = []
    warnings = list(report["warnings"])
    calibrated = True
    roi_pixel_count = 0
    for band in sorted(bands, key=lambda item: item.wavelength_nm):
        wavelength = band.wavelength_nm
        sample = load_grayscale_float(spectral_map[wavelength])
        if wavelength in dark_map and wavelength in white_map:
            image = reflectance_correction(sample, load_grayscale_float(dark_map[wavelength]), load_grayscale_float(white_map[wavelength]))
        else:
            calibrated = False
            if not allow_uncalibrated:
                raise FeatureExtractionError(f"CALIBRATION_MISSING: {wavelength} nm")
            image = normalize_uncalibrated(sample)
        pixels, band_mask = apply_mask_to_image(image, mask, registration_mode=registration_mode)
        if pixels.size == 0:
            raise FeatureExtractionError(f"empty ROI for {wavelength} nm")
        roi_pixel_count = max(roi_pixel_count, int(np.count_nonzero(band_mask)))
        values.append(float(np.mean(pixels.astype(np.float32))))
        wavelengths.append(wavelength)

    if not calibrated:
        warnings.append(UNCALIBRATED)
    return FeatureRecord(
        sample_id=sample_id or root.name,
        wavelengths=wavelengths,
        features=values,
        calibrated=calibrated,
        roi_pixel_count=roi_pixel_count,
        source_dir=str(root),
        warnings=warnings,
    )


def _bad_images(files: list[Path], mode: str) -> list[str]:
    bad = []
    for path in files:
        try:
            with Image.open(path) as image:
                image.convert(mode).load()
        except Exception:
            bad.append(path.name)
    return bad

