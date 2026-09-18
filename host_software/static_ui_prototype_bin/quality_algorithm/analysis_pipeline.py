from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .background_reference import BackgroundReference, create_background_reference, load_background_reference
from .background_segmenter import BackgroundSegmentationConfig
from .filters import FilterBand, enabled_bands
from .registered_roi import RegisteredRoiConfig
from .registration import CameraRegistrationEndpoint, RegistrationProfile
from .spectral_features import FeatureExtractionError, FeatureRecord, extract_feature_record, inspect_sample_structure


PIPELINE_MODE_PRODUCTION = "production"
PIPELINE_MODE_DEVELOPMENT = "development"
PIPELINE_MODE_LEGACY = "legacy"

SEGMENTATION_BACKGROUND_REFERENCE = "background_reference"
SEGMENTATION_LEGACY_COLOR = "legacy_color"
REGISTRATION_CALIBRATED = "calibrated"
REGISTRATION_IDENTITY = "identity"

CALIBRATION_MISSING = "CALIBRATION_MISSING"
BACKGROUND_REFERENCE_MISSING = "BACKGROUND_REFERENCE_MISSING"
REGISTRATION_PROFILE_MISSING = "REGISTRATION_PROFILE_MISSING"
REGISTRATION_ENDPOINT_MISSING = "REGISTRATION_ENDPOINT_MISSING"

MODEL_INPUT_CONTRACT_SCHEMA_VERSION = 1
FEATURE_PIPELINE_VERSION = "p1e_analysis_pipeline_v1"
FEATURE_SCHEMA_VERSION = 1
FEATURE_EXTRACTOR_VERSION = "spectral_mean_roi_v1"
CALIBRATION_ALGORITHM_VERSION = "dark_white_reflectance_v1"
UNCALIBRATED_ALGORITHM_VERSION = "uncalibrated_normalized_intensity_v1"
SEGMENTATION_BACKGROUND_REFERENCE_ALGORITHM_VERSION = "background_reference_difference_v1"
SEGMENTATION_LEGACY_COLOR_ALGORITHM_VERSION = "legacy_color_mask_v1"
REGISTRATION_CALIBRATED_ALGORITHM_VERSION = "rgb_to_dvp2_planar_homography_v1"
REGISTRATION_IDENTITY_ALGORITHM_VERSION = "identity_roi_v1"
REGISTERED_ROI_ALGORITHM_VERSION = "registered_multispectral_conservative_roi_v1"
IDENTITY_ROI_ALGORITHM_VERSION = "identity_mask_roi_v1"
FILTER_BAND_MAPPING_VERSION = "filter_band_mapping_v1"


@dataclass(frozen=True)
class ModelInputContract:
    schema_version: int
    pipeline_version: str
    mode: str
    input_modality: dict[str, Any]
    calibration: dict[str, Any]
    segmentation: dict[str, Any]
    registration: dict[str, Any]
    wavelengths: dict[str, Any]
    roi: dict[str, Any]
    feature_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ModelInputContract":
        return cls(
            schema_version=int(payload.get("schema_version") or payload.get("schemaVersion") or MODEL_INPUT_CONTRACT_SCHEMA_VERSION),
            pipeline_version=str(payload.get("pipeline_version") or payload.get("pipelineVersion") or ""),
            mode=str(payload.get("mode") or ""),
            input_modality=dict(payload.get("input_modality") or payload.get("inputModality") or {}),
            calibration=dict(payload.get("calibration") or {}),
            segmentation=dict(payload.get("segmentation") or {}),
            registration=dict(payload.get("registration") or {}),
            wavelengths=dict(payload.get("wavelengths") or {}),
            roi=dict(payload.get("roi") or {}),
            feature_schema=dict(payload.get("feature_schema") or payload.get("featureSchema") or {}),
        )


@dataclass
class FeaturePipelineConfig:
    mode: str = PIPELINE_MODE_PRODUCTION
    require_calibration: bool = True
    segmentation_mode: str = SEGMENTATION_BACKGROUND_REFERENCE
    registration_mode: str = REGISTRATION_CALIBRATED
    background_reference: BackgroundReference | str | Path | dict | None = None
    camera_metadata: dict[str, Any] = field(default_factory=dict)
    registration_profile: RegistrationProfile | str | Path | dict | None = None
    registration_rgb_endpoint: CameraRegistrationEndpoint | dict | None = None
    registration_multispectral_endpoint: CameraRegistrationEndpoint | dict | None = None
    filters: list[FilterBand] | None = None
    rgb_dir: str = "rgb"
    spectral_dir: str = "multispectral"
    registered_roi_config: RegisteredRoiConfig | None = None

    @classmethod
    def production(cls, **overrides: Any) -> "FeaturePipelineConfig":
        data = {
            "mode": PIPELINE_MODE_PRODUCTION,
            "require_calibration": True,
            "segmentation_mode": SEGMENTATION_BACKGROUND_REFERENCE,
            "registration_mode": REGISTRATION_CALIBRATED,
        }
        data.update(overrides)
        return cls(**data)

    @classmethod
    def legacy(cls, **overrides: Any) -> "FeaturePipelineConfig":
        data = {
            "mode": PIPELINE_MODE_LEGACY,
            "require_calibration": False,
            "segmentation_mode": SEGMENTATION_LEGACY_COLOR,
            "registration_mode": REGISTRATION_IDENTITY,
        }
        data.update(overrides)
        return cls(**data)

    @classmethod
    def development(cls, **overrides: Any) -> "FeaturePipelineConfig":
        data = {
            "mode": PIPELINE_MODE_DEVELOPMENT,
            "require_calibration": False,
            "segmentation_mode": SEGMENTATION_LEGACY_COLOR,
            "registration_mode": REGISTRATION_IDENTITY,
        }
        data.update(overrides)
        return cls(**data)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "FeaturePipelineConfig":
        payload = dict(payload or {})
        mode = str(payload.get("mode") or PIPELINE_MODE_PRODUCTION)
        if mode == PIPELINE_MODE_LEGACY:
            base = cls.legacy()
        elif mode == PIPELINE_MODE_DEVELOPMENT:
            base = cls.development()
        else:
            base = cls.production()
        base.require_calibration = bool(payload.get("require_calibration", base.require_calibration))
        base.segmentation_mode = str(payload.get("segmentation_mode") or base.segmentation_mode)
        base.registration_mode = str(payload.get("registration_mode") or base.registration_mode)
        base.background_reference = payload.get("background_reference") or payload.get("backgroundReference")
        base.camera_metadata = dict(payload.get("camera_metadata") or payload.get("cameraMetadata") or {})
        base.registration_profile = payload.get("registration_profile") or payload.get("registrationProfile")
        base.registration_rgb_endpoint = payload.get("registration_rgb_endpoint") or payload.get("registrationRgbEndpoint")
        base.registration_multispectral_endpoint = payload.get("registration_multispectral_endpoint") or payload.get("registrationMultispectralEndpoint")
        filters = payload.get("filters")
        if isinstance(filters, list):
            base.filters = [FilterBand.from_dict(item) for item in filters if isinstance(item, dict)]
        roi = payload.get("registered_roi_config") or payload.get("registeredRoiConfig")
        if isinstance(roi, dict):
            base.registered_roi_config = RegisteredRoiConfig(
                erosionPx=int(roi.get("erosionPx", RegisteredRoiConfig.erosionPx)),
                minRetainedPixelCount=int(roi.get("minRetainedPixelCount", RegisteredRoiConfig.minRetainedPixelCount)),
                minRetainedRatio=float(roi.get("minRetainedRatio", RegisteredRoiConfig.minRetainedRatio)),
            )
        base.rgb_dir = str(payload.get("rgb_dir") or payload.get("rgbDir") or base.rgb_dir)
        base.spectral_dir = str(payload.get("spectral_dir") or payload.get("spectralDir") or base.spectral_dir)
        return base

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["background_reference"] = _serializable_dependency(self.background_reference)
        data["registration_profile"] = _serializable_dependency(self.registration_profile)
        data["registration_rgb_endpoint"] = _serializable_dependency(self.registration_rgb_endpoint)
        data["registration_multispectral_endpoint"] = _serializable_dependency(self.registration_multispectral_endpoint)
        data["filters"] = [band.to_dict() for band in self.filters] if self.filters else None
        data["registered_roi_config"] = asdict(self.registered_roi_config) if self.registered_roi_config else None
        return data

    @property
    def is_production(self) -> bool:
        return self.mode == PIPELINE_MODE_PRODUCTION


def run_feature_pipeline(
    sample_dir: str | Path,
    *,
    sample_id: str | None = None,
    config: FeaturePipelineConfig | dict | None = None,
) -> FeatureRecord:
    cfg = config if isinstance(config, FeaturePipelineConfig) else FeaturePipelineConfig.from_dict(config)
    root = Path(sample_dir).expanduser()
    metadata = _load_sample_metadata(root)
    cfg = _with_sample_metadata_defaults(cfg, root, metadata)
    _validate_pipeline_requirements(root, cfg)
    record = extract_feature_record(
        root,
        sample_id=sample_id,
        filters=cfg.filters,
        rgb_dir=cfg.rgb_dir,
        spectral_dir=cfg.spectral_dir,
        allow_uncalibrated=not cfg.require_calibration,
        registration_mode=cfg.registration_mode,
        segmentation_mode=cfg.segmentation_mode,
        background_reference=_resolve_background_reference_dependency(cfg.background_reference),
        camera_metadata=cfg.camera_metadata,
        registration_profile=_resolve_registration_profile_dependency(cfg.registration_profile),
        registration_rgb_endpoint=cfg.registration_rgb_endpoint,
        registration_multispectral_endpoint=cfg.registration_multispectral_endpoint,
        registered_roi_config=cfg.registered_roi_config,
    )
    contract = build_model_input_contract(
        config=cfg,
        wavelengths=record.wavelengths,
        calibrated=record.calibrated,
        feature_names=[f"R{wavelength}" for wavelength in record.wavelengths],
    )
    record.model_input_contract = contract.to_dict()
    record.pipeline_signature = pipeline_signature(contract)
    return record


def build_model_input_contract(
    *,
    config: FeaturePipelineConfig,
    wavelengths: list[int],
    calibrated: bool,
    feature_names: list[str],
) -> ModelInputContract:
    bands = [_band_contract(band) for band in enabled_bands(config.filters)]
    band_mapping = {
        "version": FILTER_BAND_MAPPING_VERSION,
        "ordered_wavelengths_nm": [int(wavelength) for wavelength in wavelengths],
        "enabled_bands": bands,
        "content_digest": _digest({"version": FILTER_BAND_MAPPING_VERSION, "enabled_bands": bands}),
    }
    return ModelInputContract(
        schema_version=MODEL_INPUT_CONTRACT_SCHEMA_VERSION,
        pipeline_version=FEATURE_PIPELINE_VERSION,
        mode=config.mode,
        input_modality={
            "schema_version": 1,
            "sample_layout": "sample_dir_rgb_multispectral_calibration",
            "rgb_dir_role": "rgb",
            "spectral_dir_role": "multispectral",
            "multispectral_input": "ordered_grayscale_band_images",
            "feature_input": "registered_multispectral_roi_mean_reflectance",
        },
        calibration=_calibration_contract(config, calibrated),
        segmentation=_segmentation_contract(config),
        registration=_registration_contract(config),
        wavelengths=band_mapping,
        roi=_roi_contract(config),
        feature_schema={
            "schema_version": FEATURE_SCHEMA_VERSION,
            "extractor_version": FEATURE_EXTRACTOR_VERSION,
            "feature_names": list(feature_names),
            "feature_count": len(feature_names),
            "value": "mean_roi_intensity",
            "order": "matches_ordered_wavelengths_nm",
        },
    )


def canonical_contract_json(contract: ModelInputContract | dict[str, Any]) -> str:
    payload = contract.to_dict() if isinstance(contract, ModelInputContract) else dict(contract)
    return json.dumps(_canonicalize(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def pipeline_signature(contract: ModelInputContract | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_contract_json(contract).encode("utf-8")).hexdigest()


def _validate_pipeline_requirements(root: Path, config: FeaturePipelineConfig) -> None:
    report = inspect_sample_structure(root, rgb_dir=config.rgb_dir, spectral_dir=config.spectral_dir, filters=config.filters)
    if report.get("missing_bands"):
        missing = ", ".join(f"{band} nm" for band in report["missing_bands"])
        raise FeatureExtractionError(f"MODEL_INPUT_MISMATCH: Missing wavelength: {missing}")
    if config.require_calibration and report.get("calibration_status") != "complete":
        raise FeatureExtractionError(f"{CALIBRATION_MISSING}: dark/white calibration is required")
    if config.segmentation_mode == SEGMENTATION_BACKGROUND_REFERENCE and not config.background_reference:
        raise FeatureExtractionError(BACKGROUND_REFERENCE_MISSING)
    if config.registration_mode == REGISTRATION_CALIBRATED:
        if not config.registration_profile:
            raise FeatureExtractionError(REGISTRATION_PROFILE_MISSING)
        if config.is_production and (not config.registration_rgb_endpoint or not config.registration_multispectral_endpoint):
            raise FeatureExtractionError(REGISTRATION_ENDPOINT_MISSING)
    if config.is_production:
        if config.segmentation_mode != SEGMENTATION_BACKGROUND_REFERENCE:
            raise FeatureExtractionError("PRODUCTION_SEGMENTATION_REQUIRES_BACKGROUND_REFERENCE")
        if config.registration_mode != REGISTRATION_CALIBRATED:
            raise FeatureExtractionError("PRODUCTION_REGISTRATION_REQUIRES_CALIBRATED")
        if not config.require_calibration:
            raise FeatureExtractionError("PRODUCTION_CALIBRATION_REQUIRED")


def _with_sample_metadata_defaults(config: FeaturePipelineConfig, root: Path, metadata: dict[str, Any]) -> FeaturePipelineConfig:
    cfg = FeaturePipelineConfig.from_dict(config.to_dict())
    image_dirs = metadata.get("image_directories") if isinstance(metadata.get("image_directories"), dict) else {}
    cfg.rgb_dir = str(image_dirs.get("rgb") or metadata.get("rgbDirName") or cfg.rgb_dir)
    cfg.spectral_dir = str(image_dirs.get("multispectral") or metadata.get("multispectralDirName") or cfg.spectral_dir)
    if cfg.background_reference is None:
        cfg.background_reference = metadata.get("background_reference") or metadata.get("backgroundReference")
    analysis = metadata.get("analysis_pipeline") or metadata.get("analysisPipeline") or {}
    if isinstance(analysis, dict):
        cfg.camera_metadata = cfg.camera_metadata or dict(analysis.get("camera_metadata") or analysis.get("cameraMetadata") or {})
        cfg.registration_profile = cfg.registration_profile or analysis.get("registration_profile") or analysis.get("registrationProfile")
        cfg.registration_rgb_endpoint = cfg.registration_rgb_endpoint or analysis.get("registration_rgb_endpoint") or analysis.get("registrationRgbEndpoint")
        cfg.registration_multispectral_endpoint = cfg.registration_multispectral_endpoint or analysis.get("registration_multispectral_endpoint") or analysis.get("registrationMultispectralEndpoint")
    if isinstance(cfg.background_reference, dict):
        path = cfg.background_reference.get("backgroundReferencePath") or cfg.background_reference.get("managedPath") or cfg.background_reference.get("imagePath")
        if path:
            cfg.background_reference = _resolve_relative(root, path)
    if isinstance(cfg.registration_profile, str):
        cfg.registration_profile = _resolve_relative(root, cfg.registration_profile)
    return cfg


def _resolve_background_reference_dependency(reference: BackgroundReference | str | Path | dict | None) -> BackgroundReference | str | Path | None:
    if reference is None or isinstance(reference, BackgroundReference):
        return reference
    if isinstance(reference, dict):
        image_path = reference.get("imagePath") or reference.get("backgroundReferencePath") or reference.get("managedPath")
        json_path = reference.get("jsonPath") or reference.get("backgroundReferenceJson")
        if json_path:
            return load_background_reference(json_path)
        if image_path:
            return create_background_reference(
                image_path,
                reference_id=str(reference.get("referenceId") or reference.get("backgroundReferenceId") or reference.get("id") or ""),
                device=reference.get("device") if isinstance(reference.get("device"), dict) else None,
                camera_profile=reference.get("cameraProfile") if isinstance(reference.get("cameraProfile"), dict) else None,
                camera_settings=reference.get("cameraSettings") if isinstance(reference.get("cameraSettings"), dict) else None,
                illumination=reference.get("illumination") if isinstance(reference.get("illumination"), dict) else None,
            )
        return None
    path = Path(reference)
    if path.suffix.lower() == ".json":
        return load_background_reference(path)
    return create_background_reference(path)


def _resolve_registration_profile_dependency(profile: RegistrationProfile | str | Path | dict | None) -> RegistrationProfile | str | Path | None:
    if isinstance(profile, dict):
        return RegistrationProfile.from_dict(profile)
    return profile


def _calibration_contract(config: FeaturePipelineConfig, calibrated: bool) -> dict[str, Any]:
    if calibrated:
        mode = "dark_white_reflectance"
        algorithm_version = CALIBRATION_ALGORITHM_VERSION
    else:
        mode = "uncalibrated_normalized"
        algorithm_version = UNCALIBRATED_ALGORITHM_VERSION
    return {
        "required": bool(config.require_calibration),
        "mode": mode,
        "algorithm_version": algorithm_version,
    }


def _segmentation_contract(config: FeaturePipelineConfig) -> dict[str, Any]:
    if config.segmentation_mode == SEGMENTATION_BACKGROUND_REFERENCE:
        params = asdict(BackgroundSegmentationConfig())
        return {
            "mode": SEGMENTATION_BACKGROUND_REFERENCE,
            "algorithm_version": SEGMENTATION_BACKGROUND_REFERENCE_ALGORITHM_VERSION,
            "semantic_params": params,
            "requires_background_reference": True,
            "background_reference": _background_reference_semantic(config.background_reference),
        }
    return {
        "mode": config.segmentation_mode,
        "algorithm_version": SEGMENTATION_LEGACY_COLOR_ALGORITHM_VERSION if config.segmentation_mode == SEGMENTATION_LEGACY_COLOR else "unknown",
        "semantic_params": {},
        "requires_background_reference": False,
        "background_reference": {},
    }


def _registration_contract(config: FeaturePipelineConfig) -> dict[str, Any]:
    if config.registration_mode == REGISTRATION_CALIBRATED:
        profile = _resolve_registration_profile_dependency(config.registration_profile)
        profile_semantic = _registration_profile_semantic(profile)
        return {
            "mode": REGISTRATION_CALIBRATED,
            "algorithm_version": REGISTRATION_CALIBRATED_ALGORITHM_VERSION,
            "profile_schema_version": profile_semantic.get("schemaVersion"),
            "profile_method": profile_semantic.get("method"),
            "reference_band_nm": profile_semantic.get("referenceBandNm"),
            "profile_content_digest": _digest(profile_semantic),
        }
    return {
        "mode": config.registration_mode,
        "algorithm_version": REGISTRATION_IDENTITY_ALGORITHM_VERSION if config.registration_mode == REGISTRATION_IDENTITY else "unknown",
        "profile_schema_version": None,
        "profile_method": None,
        "reference_band_nm": None,
        "profile_content_digest": "",
    }


def _roi_contract(config: FeaturePipelineConfig) -> dict[str, Any]:
    if config.registration_mode == REGISTRATION_CALIBRATED:
        roi_config = config.registered_roi_config or RegisteredRoiConfig()
        return {
            "mode": "registered_multispectral_roi",
            "algorithm_version": REGISTERED_ROI_ALGORITHM_VERSION,
            "erosionPx": int(roi_config.erosionPx),
            "minRetainedPixelCount": int(roi_config.minRetainedPixelCount),
            "minRetainedRatio": float(roi_config.minRetainedRatio),
        }
    return {
        "mode": "identity_rgb_mask_roi",
        "algorithm_version": IDENTITY_ROI_ALGORITHM_VERSION,
        "erosionPx": 0,
        "minRetainedPixelCount": 0,
        "minRetainedRatio": 0.0,
    }


def _band_contract(band: FilterBand) -> dict[str, Any]:
    return {
        "filter_position": int(band.filter_position),
        "wavelength_nm": int(band.wavelength_nm),
        "bandwidth_nm": band.bandwidth_nm,
        "enabled": bool(band.enabled),
    }


def _registration_profile_semantic(profile: RegistrationProfile | str | Path | None) -> dict[str, Any]:
    resolved = _resolve_registration_profile_dependency(profile)
    if isinstance(resolved, (str, Path)):
        try:
            resolved = RegistrationProfile.from_json_file(resolved)
        except Exception:
            return {}
    if resolved is None or not hasattr(resolved, "to_dict"):
        return {}
    data = resolved.to_dict()
    return {
        "schemaVersion": data.get("schemaVersion"),
        "method": data.get("method"),
        "matrixRgbToMultispectral": data.get("matrixRgbToMultispectral"),
        "rgb": _endpoint_semantic(data.get("rgb") or {}),
        "multispectral": _endpoint_semantic(data.get("multispectral") or {}),
        "referenceBandNm": data.get("referenceBandNm"),
        "target": data.get("target") or {},
        "calibrationPlane": data.get("calibrationPlane") or "",
        "valid": data.get("valid"),
    }


def _background_reference_semantic(reference: BackgroundReference | str | Path | dict | None) -> dict[str, Any]:
    try:
        resolved = _resolve_background_reference_dependency(reference)
    except Exception:
        return {}
    if not isinstance(resolved, BackgroundReference):
        return {}
    return {
        "imageSha256": resolved.imageSha256,
        "width": int(resolved.width),
        "height": int(resolved.height),
        "channels": int(resolved.channels),
        "dtype": resolved.dtype,
        "device": resolved.device,
        "cameraProfile": resolved.cameraProfile,
        "cameraSettings": resolved.cameraSettings,
        "illumination": resolved.illumination,
        "stage": resolved.stage,
    }


def _endpoint_semantic(endpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "stableId": endpoint.get("stableId"),
        "serial": endpoint.get("serial"),
        "deviceIndex": endpoint.get("deviceIndex"),
        "width": endpoint.get("width"),
        "height": endpoint.get("height"),
    }


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(_canonicalize(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    if isinstance(value, float):
        return round(value, 10)
    return value


def _load_sample_metadata(root: Path) -> dict[str, Any]:
    path = root / "metadata.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_relative(root: Path, value: Any) -> str:
    path = Path(str(value))
    return str(path if path.is_absolute() else (root / path).resolve())


def _serializable_dependency(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    return str(value)
