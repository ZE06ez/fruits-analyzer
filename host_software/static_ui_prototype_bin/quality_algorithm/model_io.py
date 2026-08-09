from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .preprocessing import PreprocessorState, transform_preprocessor
from .spectral_features import FeatureRecord


class ModelInputMismatch(RuntimeError):
    pass


@dataclass
class ModelBundle:
    model: object
    metadata: dict


def save_model_bundle(model, metadata: dict, target_dir: str | Path) -> None:
    import joblib

    folder = Path(target_dir)
    folder.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, folder / "model.joblib")
    with (folder / "metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)


def load_model_bundle(target_dir: str | Path) -> ModelBundle:
    import joblib

    folder = Path(target_dir)
    model_path = folder / "model.joblib"
    metadata_path = folder / "metadata.json"
    if not model_path.exists() or not metadata_path.exists():
        raise FileNotFoundError(f"model bundle not found: {folder}")
    with metadata_path.open("r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    return ModelBundle(model=joblib.load(model_path), metadata=metadata)


def validate_feature_record(metadata: dict, record: FeatureRecord) -> None:
    expected = [int(v) for v in metadata.get("wavelengths_nm", [])]
    actual = [int(v) for v in record.wavelengths]
    if expected != actual:
        missing = [w for w in expected if w not in actual]
        extra = [w for w in actual if w not in expected]
        parts = []
        if missing:
            parts.append("Missing wavelength: " + ", ".join(f"{w} nm" for w in missing))
        if extra:
            parts.append("Unexpected wavelength: " + ", ".join(f"{w} nm" for w in extra))
        raise ModelInputMismatch("MODEL_INPUT_MISMATCH: " + "; ".join(parts))
    if bool(metadata.get("calibration_required")) and not record.calibrated:
        raise ModelInputMismatch("CALIBRATION_REQUIRED: current sample is uncalibrated")


def predict_feature_record(bundle: ModelBundle, record: FeatureRecord) -> float:
    validate_feature_record(bundle.metadata, record)
    x = np.asarray(record.features, dtype=np.float32).reshape(1, -1)
    state = PreprocessorState.from_dict(bundle.metadata.get("preprocessing_state"))
    processed = transform_preprocessor(x, state)
    value = bundle.model.predict(processed)
    return float(np.asarray(value).reshape(-1)[0])

