from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from quality_algorithm.analysis_pipeline import FeaturePipelineConfig
from quality_algorithm.background_reference import create_background_reference, save_background_reference
from quality_algorithm.filters import FilterBand
from quality_algorithm.registration import (
    CameraRegistrationEndpoint,
    CheckerboardTarget,
    RegistrationMetrics,
    RegistrationProfile,
    REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1,
)


SYNTHETIC_SHAPE = (32, 32)
SYNTHETIC_FILTERS = [
    FilterBand(filter_position=1, wavelength_nm=450, bandwidth_nm=10.0),
    FilterBand(filter_position=2, wavelength_nm=560, bandwidth_nm=10.0),
    FilterBand(filter_position=3, wavelength_nm=670, bandwidth_nm=10.0),
]


def build_production_resources(root: Path) -> tuple[Path, Path, FeaturePipelineConfig]:
    resources = root / "production_resources"
    resources.mkdir(parents=True, exist_ok=True)
    background_path = resources / "background.png"
    Image.fromarray(np.full((*SYNTHETIC_SHAPE, 3), 20, dtype=np.uint8)).save(background_path)
    reference = create_background_reference(background_path, reference_id="synthetic_background")
    reference.capturedAt = "2026-01-01T00:00:00Z"
    background_json = resources / "background_reference.json"
    save_background_reference(reference, background_json)

    endpoint_rgb = CameraRegistrationEndpoint(stableId="synthetic-rgb", width=32, height=32)
    endpoint_ms = CameraRegistrationEndpoint(stableId="synthetic-ms", width=32, height=32)
    profile = RegistrationProfile(
        schemaVersion=1,
        method=REGISTRATION_METHOD_PLANAR_HOMOGRAPHY_V1,
        rgb=endpoint_rgb,
        multispectral=endpoint_ms,
        referenceBandNm=560,
        target=CheckerboardTarget(innerCornersCols=3, innerCornersRows=3, squareSizeMm=10.0),
        matrixRgbToMultispectral=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        calibrationPlane="synthetic_plane",
        metrics=RegistrationMetrics(pointCount=9, rmsePx=0.0, meanErrorPx=0.0, maxErrorPx=0.0, p95ErrorPx=0.0),
        createdAt="2026-01-01T00:00:00Z",
        sourcePairs=[{"capturedAt": "runtime-only"}],
        valid=True,
    )
    profile_path = resources / "registration_profile.json"
    profile.save_json(profile_path)
    config = FeaturePipelineConfig.production(
        filters=SYNTHETIC_FILTERS,
        background_reference=background_json,
        registration_profile=profile_path,
        registration_rgb_endpoint=endpoint_rgb,
        registration_multispectral_endpoint=endpoint_ms,
    )
    return background_json, profile_path, config


def build_synthetic_sample(root: Path, sample_id: str, index: int, *, include_calibration: bool = True) -> Path:
    sample = root / sample_id
    rgb_dir = sample / "rgb"
    spectral_dir = sample / "multispectral"
    rgb_dir.mkdir(parents=True, exist_ok=True)
    spectral_dir.mkdir(parents=True, exist_ok=True)
    if include_calibration:
        (sample / "calibration" / "dark").mkdir(parents=True, exist_ok=True)
        (sample / "calibration" / "white").mkdir(parents=True, exist_ok=True)

    rgb = np.full((*SYNTHETIC_SHAPE, 3), 20, dtype=np.uint8)
    rgb[8:24, 8:24] = [150 + index * 3, 100, 80]
    Image.fromarray(rgb).save(rgb_dir / "rgb_001.png")
    for band_index, band in enumerate(SYNTHETIC_FILTERS):
        image = np.full(SYNTHETIC_SHAPE, 30, dtype=np.uint8)
        image[8:24, 8:24] = 70 + band_index * 35 + index * 4
        Image.fromarray(image, mode="L").save(spectral_dir / f"{band.wavelength_nm}.png")
        if include_calibration:
            Image.fromarray(np.zeros(SYNTHETIC_SHAPE, dtype=np.uint8), mode="L").save(sample / "calibration" / "dark" / f"{band.wavelength_nm}.png")
            Image.fromarray(np.full(SYNTHETIC_SHAPE, 255, dtype=np.uint8), mode="L").save(sample / "calibration" / "white" / f"{band.wavelength_nm}.png")

    metadata = {
        "sample_id": sample_id,
        "sample_name": f"Synthetic training {index + 1}",
        "sample_mode": "training_capture",
        "fruit_type": "blueberry",
        "variety": "Duke",
        "analysis_pipeline": {},
    }
    (sample / "metadata.json").write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    return sample
