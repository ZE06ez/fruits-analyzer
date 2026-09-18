from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
import sqlite3

from pointcloud_service import inspect_sample_folder, list_images
from quality_algorithm.analysis_pipeline import FeaturePipelineConfig, run_feature_pipeline
from quality_algorithm.model_io import MODEL_INPUT_CONTRACT_MISSING, ModelInputMismatch, load_model_bundle, predict_feature_record
from quality_algorithm.spectral_features import FeatureExtractionError
from runtime_support import runtime_data_dir


APP_DIR = Path(__file__).resolve().parent
# Kept as a test/development override; packaged runtime data is resolved below.
MODEL_ROOT = APP_DIR / "trained_models"


def model_root() -> Path:
    runtime_root = runtime_data_dir(APP_DIR)
    return MODEL_ROOT if runtime_root == APP_DIR else runtime_root / "trained_models"


def model_registry_path() -> Path:
    runtime_root = runtime_data_dir(APP_DIR)
    return MODEL_ROOT.parent / "model_studio" / "database" / "model_studio.sqlite" if runtime_root == APP_DIR else runtime_root / "database" / "model_studio.sqlite"


@dataclass
class PredictionResult:
    """Structured result returned by quality prediction models."""

    value: float | None
    unit: str
    confidence: float | None
    model_name: str
    model_version: str
    sample_count: int
    elapsed_time: float
    status: str
    model_id: str = ""
    model_type: str = ""
    preprocessing: str = ""
    error_message: str = ""
    target: str = ""
    pipeline_signature: str = ""
    model_pipeline_signature: str = ""
    model_input_contract: dict | None = None
    feature_pipeline: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SampleSession:
    """Current sample data shared by morphology, surface, SSC, TA and pH modules."""

    sample_id: str
    sample_name: str
    analysis_data_dir: str
    rgb_files: list[str]
    multispectral_files: list[str]
    capture_time: str = ""
    fruit_type: str = ""
    variety: str = "generic"
    selected_ssc_model_id: str = ""
    selected_ta_model_id: str = ""
    selected_ph_model_id: str = ""
    ssc_result: dict | None = None
    ta_result: dict | None = None
    ph_result: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def build_sample_session(
    dataset_dir: str | Path,
    *,
    sample_id: str = "",
    sample_name: str = "",
    rgb_dir: str | None = None,
    spectral_dir: str | None = None,
    capture_time: str = "",
    fruit_type: str = "",
    variety: str = "generic",
    selected_ssc_model_id: str = "",
    selected_ta_model_id: str = "",
    selected_ph_model_id: str = "",
) -> tuple[SampleSession, dict]:
    """Build a reusable sample session from the unified analysis_data_dir."""

    report = inspect_sample_folder(dataset_dir, rgb_dir, spectral_dir)
    root = Path(report.get("datasetDir") or dataset_dir).expanduser()
    rgb_files: list[str] = []
    spectral_files: list[str] = []

    color_dir = report.get("colorDir") or ""
    spectral_dir = report.get("multispectralDir") or report.get("depthDir") or ""
    if color_dir:
        rgb_files = [str(path) for path in list_images(Path(color_dir))]
    if spectral_dir:
        spectral_files = [str(path) for path in list_images(Path(spectral_dir))]

    session = SampleSession(
        sample_id=sample_id or root.name or "unnamed_sample",
        sample_name=sample_name or sample_id or root.name or "unnamed_sample",
        analysis_data_dir=str(root) if str(root) != "." else "",
        rgb_files=rgb_files,
        multispectral_files=spectral_files,
        capture_time=capture_time,
        fruit_type=fruit_type,
        variety=variety or "generic",
        selected_ssc_model_id=selected_ssc_model_id,
        selected_ta_model_id=selected_ta_model_id,
        selected_ph_model_id=selected_ph_model_id,
    )
    return session, report


def predict_ssc(sample_data: SampleSession) -> PredictionResult:
    return _predict_target(sample_data, target="ssc", unit="°Brix", display_name="SSC prediction model")


def predict_ta(sample_data: SampleSession) -> PredictionResult:
    return _predict_target(sample_data, target="ta", unit="%", display_name="TA prediction model")


def predict_ph(sample_data: SampleSession) -> PredictionResult:
    return _predict_target(sample_data, target="ph", unit="pH", display_name="pH prediction model")


def build_prediction_feature_record(sample_data: SampleSession, model_metadata: dict | None = None):
    pipeline_config = _pipeline_config_for_model_metadata(model_metadata or {})
    return run_feature_pipeline(sample_data.analysis_data_dir, sample_id=sample_data.sample_id, config=pipeline_config)


def _predict_target(sample_data: SampleSession, *, target: str, unit: str, display_name: str) -> PredictionResult:
    started = time.perf_counter()
    selected_id = {
        "ssc": sample_data.selected_ssc_model_id,
        "ta": sample_data.selected_ta_model_id,
        "ph": sample_data.selected_ph_model_id,
    }.get(target, "")
    registry_model = _select_registry_model(
        target=target,
        fruit_type=sample_data.fruit_type,
        variety=sample_data.variety,
        selected_model_id=selected_id,
    )
    if registry_model is None and sample_data.fruit_type and _registry_has_published_models(target):
        return PredictionResult(
            value=None,
            unit=unit,
            confidence=None,
            model_name=display_name,
            model_version="not_connected",
            model_id=selected_id,
            model_type="",
            preprocessing="",
            sample_count=_effective_sample_count(sample_data),
            elapsed_time=round(time.perf_counter() - started, 3),
            status="model_missing",
            error_message="No published model matches the current fruit type or variety.",
            target=target,
        )
    model_dir = Path(registry_model["model_dir"]) if registry_model else model_root() / target
    model_path = model_dir / "model.joblib"
    metadata_path = model_dir / "metadata.json"
    sample_count = _effective_sample_count(sample_data)
    if not model_path.exists() or not metadata_path.exists():
        return PredictionResult(
            value=None,
            unit=unit,
            confidence=None,
            model_name=display_name,
            model_version="not_connected",
            model_id=selected_id,
            model_type="",
            preprocessing="",
            sample_count=sample_count,
            elapsed_time=round(time.perf_counter() - started, 3),
            status="model_missing",
            error_message=f"{display_name} is not connected.",
            target=target,
        )

    try:
        bundle = load_model_bundle(model_dir)
        record = build_prediction_feature_record(sample_data, bundle.metadata)
        value = predict_feature_record(
            bundle,
            record,
            allow_legacy_missing_contract=_allows_legacy_missing_contract(bundle.metadata),
        )
        resolved_name = bundle.metadata.get("display_name") or (registry_model.get("display_name") if registry_model else "") or bundle.metadata.get("model_type") or display_name
        return PredictionResult(
            value=round(value, 4),
            unit=unit,
            confidence=None,
            model_name=str(resolved_name),
            model_version=str(bundle.metadata.get("model_version") or ""),
            model_id=str(bundle.metadata.get("model_id") or (registry_model.get("model_id") if registry_model else "")),
            model_type=str(bundle.metadata.get("model_type") or (registry_model.get("model_type") if registry_model else "")),
            preprocessing=str(bundle.metadata.get("preprocessing") or (registry_model.get("preprocessing") if registry_model else "")),
            sample_count=sample_count,
            elapsed_time=round(time.perf_counter() - started, 3),
            status="success",
            error_message="",
            target=target,
            pipeline_signature=record.pipeline_signature,
            model_pipeline_signature=str(bundle.metadata.get("pipeline_signature") or ""),
            model_input_contract=record.model_input_contract,
            feature_pipeline=bundle.metadata.get("feature_pipeline") if isinstance(bundle.metadata.get("feature_pipeline"), dict) else None,
        )
    except ModelInputMismatch as exc:
        return _error_result(
            unit, display_name, sample_count, started, "model_input_mismatch", str(exc), target=target,
            model_id=str(bundle.metadata.get("model_id") if "bundle" in locals() else ""),
            model_version=str(bundle.metadata.get("model_version") if "bundle" in locals() else ""),
            model_type=str(bundle.metadata.get("model_type") if "bundle" in locals() else ""),
            preprocessing=str(bundle.metadata.get("preprocessing") if "bundle" in locals() else ""),
            model_pipeline_signature=str(bundle.metadata.get("pipeline_signature") if "bundle" in locals() else ""),
            model_input_contract=bundle.metadata.get("model_input_contract") if "bundle" in locals() and isinstance(bundle.metadata.get("model_input_contract"), dict) else None,
            feature_pipeline=bundle.metadata.get("feature_pipeline") if "bundle" in locals() and isinstance(bundle.metadata.get("feature_pipeline"), dict) else None,
        )
    except FeatureExtractionError as exc:
        return _error_result(unit, display_name, sample_count, started, "feature_error", str(exc), target=target)
    except ImportError as exc:
        return _error_result(unit, display_name, sample_count, started, "dependency_missing", str(exc), target=target)
    except Exception as exc:
        return _error_result(unit, display_name, sample_count, started, "model_error", str(exc), target=target)


def _error_result(
    unit: str,
    name: str,
    sample_count: int,
    started: float,
    status: str,
    message: str,
    *,
    target: str = "",
    model_id: str = "",
    model_version: str = "",
    model_type: str = "",
    preprocessing: str = "",
    model_pipeline_signature: str = "",
    model_input_contract: dict | None = None,
    feature_pipeline: dict | None = None,
) -> PredictionResult:
    return PredictionResult(
        value=None,
        unit=unit,
        confidence=None,
        model_name=name,
        model_id=model_id,
        model_version=model_version,
        model_type=model_type,
        preprocessing=preprocessing,
        sample_count=sample_count,
        elapsed_time=round(time.perf_counter() - started, 3),
        status=status,
        error_message=message,
        target=target,
        model_pipeline_signature=model_pipeline_signature,
        model_input_contract=model_input_contract,
        feature_pipeline=feature_pipeline,
    )


def _effective_sample_count(sample_data: SampleSession) -> int:
    if sample_data.multispectral_files:
        return 1
    return 1 if sample_data.rgb_files else 0


def _pipeline_config_for_model_metadata(metadata: dict) -> FeaturePipelineConfig:
    feature_pipeline = metadata.get("feature_pipeline")
    if isinstance(feature_pipeline, dict):
        return FeaturePipelineConfig.from_dict(feature_pipeline)
    if _allows_legacy_missing_contract(metadata):
        return FeaturePipelineConfig.legacy()
    raise ModelInputMismatch(f"{MODEL_INPUT_CONTRACT_MISSING}: model metadata missing feature_pipeline")


def _allows_legacy_missing_contract(metadata: dict) -> bool:
    if str(metadata.get("model_input_contract_policy") or "") == "legacy_compatibility":
        return True
    feature_pipeline = metadata.get("feature_pipeline")
    if not isinstance(feature_pipeline, dict):
        return False
    mode = str(feature_pipeline.get("mode") or "")
    return mode in {"legacy", "development"}


def _select_registry_model(*, target: str, fruit_type: str, variety: str, selected_model_id: str = "") -> dict | None:
    database = model_registry_path()
    if not database.exists():
        return None
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        if selected_model_id:
            row = conn.execute(
                "SELECT * FROM models WHERE model_id=? AND target=? AND status IN ('Published','Default','Production')",
                (selected_model_id, target),
            ).fetchone()
            if not row:
                return None
            model = dict(row)
            if not _model_matches_scope(model, fruit_type, variety):
                raise ModelInputMismatch("MODEL_SCOPE_MISMATCH: selected model does not match current fruit type or variety")
            return model
        if fruit_type:
            exact = conn.execute(
                """
                SELECT * FROM models
                WHERE target=? AND lower(fruit_type)=lower(?) AND lower(variety)=lower(?) AND (status='Default' OR is_default=1)
                ORDER BY published_at DESC LIMIT 1
                """,
                (target, fruit_type, variety or "generic"),
            ).fetchone()
            if exact:
                return dict(exact)
            generic = conn.execute(
                """
                SELECT * FROM models
                WHERE target=? AND lower(fruit_type)=lower(?) AND lower(variety)='generic' AND (status='Default' OR is_default=1)
                ORDER BY published_at DESC LIMIT 1
                """,
                (target, fruit_type),
            ).fetchone()
            if generic:
                return dict(generic)
        return None
    finally:
        conn.close()


def _registry_has_published_models(target: str) -> bool:
    database = model_registry_path()
    if not database.exists():
        return False
    conn = sqlite3.connect(database)
    try:
        row = conn.execute(
            "SELECT 1 FROM models WHERE target=? AND status IN ('Published','Default','Production') LIMIT 1",
            (target,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _model_matches_scope(model: dict, fruit_type: str, variety: str) -> bool:
    model_fruit = str(model.get("fruit_type") or "").lower()
    model_variety = str(model.get("variety") or "generic").lower()
    sample_fruit = str(fruit_type or "").lower()
    sample_variety = str(variety or "generic").lower()
    if sample_fruit and model_fruit and model_fruit != sample_fruit:
        return False
    if model_variety not in {"", "generic"} and sample_variety and model_variety != sample_variety:
        return False
    return True
