from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from runtime_support import atomic_write_json
from .preprocessing import PreprocessorState, transform_preprocessor
from .spectral_features import FeatureRecord
from .analysis_pipeline import canonical_contract_json, pipeline_signature


class ModelInputMismatch(RuntimeError):
    pass


MODEL_INPUT_CONTRACT_MISSING = "MODEL_INPUT_CONTRACT_MISSING"
MODEL_INPUT_CONTRACT_NOT_PRODUCTION = "MODEL_INPUT_CONTRACT_NOT_PRODUCTION"


@dataclass
class ModelBundle:
    model: object
    metadata: dict


def save_model_bundle(model, metadata: dict, target_dir: str | Path) -> None:
    import joblib

    folder = Path(target_dir)
    folder.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, folder / "model.joblib")
    atomic_write_json(folder / "metadata.json", metadata)


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


def validate_model_metadata_contract(metadata: dict) -> None:
    contract = metadata.get("model_input_contract")
    signature = str(metadata.get("pipeline_signature") or "")
    if not isinstance(contract, dict) or not signature:
        raise ModelInputMismatch(f"{MODEL_INPUT_CONTRACT_MISSING}: model metadata missing model_input_contract or pipeline_signature")
    expected = pipeline_signature(contract)
    if signature != expected:
        raise ModelInputMismatch("MODEL_INPUT_MISMATCH: model metadata pipeline_signature does not match model_input_contract")


def validate_production_model_metadata_contract(metadata: dict) -> None:
    validate_model_metadata_contract(metadata)
    contract = metadata.get("model_input_contract") or {}
    if str(contract.get("mode") or "") != "production":
        raise ModelInputMismatch(
            f"{MODEL_INPUT_CONTRACT_NOT_PRODUCTION}: Published/Default models require production model_input_contract"
        )


def validate_model_input_contract(
    metadata: dict,
    record: FeatureRecord,
    *,
    allow_legacy_missing_contract: bool = False,
) -> None:
    expected_contract = metadata.get("model_input_contract")
    expected_signature = str(metadata.get("pipeline_signature") or "")
    actual_contract = record.model_input_contract
    actual_signature = str(record.pipeline_signature or "")
    if not isinstance(expected_contract, dict) or not expected_signature:
        if allow_legacy_missing_contract:
            return
        raise ModelInputMismatch(f"{MODEL_INPUT_CONTRACT_MISSING}: model metadata missing model_input_contract or pipeline_signature")
    if not isinstance(actual_contract, dict) or not actual_signature:
        raise ModelInputMismatch(f"{MODEL_INPUT_CONTRACT_MISSING}: current FeatureRecord missing model_input_contract or pipeline_signature")
    if pipeline_signature(expected_contract) != expected_signature:
        raise ModelInputMismatch("MODEL_INPUT_MISMATCH: model metadata pipeline_signature does not match model_input_contract")
    if pipeline_signature(actual_contract) != actual_signature:
        raise ModelInputMismatch("MODEL_INPUT_MISMATCH: current FeatureRecord pipeline_signature does not match model_input_contract")
    if expected_signature == actual_signature and canonical_contract_json(expected_contract) == canonical_contract_json(actual_contract):
        return
    parts = _contract_mismatch_parts(expected_contract, actual_contract)
    if not parts:
        parts = ["pipeline signature mismatch"]
    raise ModelInputMismatch("MODEL_INPUT_MISMATCH: " + "; ".join(parts))


def predict_feature_record(
    bundle: ModelBundle,
    record: FeatureRecord,
    *,
    allow_legacy_missing_contract: bool = False,
) -> float:
    validate_model_input_contract(
        bundle.metadata,
        record,
        allow_legacy_missing_contract=allow_legacy_missing_contract,
    )
    validate_feature_record(bundle.metadata, record)
    x = np.asarray(record.features, dtype=np.float32).reshape(1, -1)
    state = PreprocessorState.from_dict(bundle.metadata.get("preprocessing_state"))
    processed = transform_preprocessor(x, state)
    value = bundle.model.predict(processed)
    return float(np.asarray(value).reshape(-1)[0])


def _contract_mismatch_parts(expected: dict, actual: dict) -> list[str]:
    checks = [
        ("pipeline version mismatch", ("pipeline_version",)),
        ("input modality mismatch", ("input_modality",)),
        ("calibration mismatch", ("calibration",)),
        ("segmentation mismatch", ("segmentation",)),
        ("registration mismatch", ("registration",)),
        ("wavelength/filter mapping mismatch", ("wavelengths",)),
        ("ROI config mismatch", ("roi",)),
        ("feature schema mismatch", ("feature_schema",)),
    ]
    parts = []
    for label, path in checks:
        if _get_path(expected, path) != _get_path(actual, path):
            parts.append(label)
    return parts


def _get_path(payload: dict, path: tuple[str, ...]):
    value = payload
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value

