from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
import sqlite3

from pointcloud_service import inspect_sample_folder, list_images
from quality_algorithm.model_io import ModelInputMismatch, load_model_bundle, predict_feature_record
from quality_algorithm.spectral_features import FeatureExtractionError, extract_feature_record


MODEL_ROOT = Path(__file__).resolve().parent / "trained_models"


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
    depth_dir = report.get("depthDir") or ""
    if color_dir:
        rgb_files = [str(path) for path in list_images(Path(color_dir))]
    if depth_dir:
        spectral_files = [str(path) for path in list_images(Path(depth_dir))]

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
    model_dir = Path(registry_model["model_dir"]) if registry_model else MODEL_ROOT / target
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
        )

    try:
        bundle = load_model_bundle(model_dir)
        record = extract_feature_record(sample_data.analysis_data_dir, sample_id=sample_data.sample_id, allow_uncalibrated=True)
        value = predict_feature_record(bundle, record)
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
        )
    except ModelInputMismatch as exc:
        return _error_result(unit, display_name, sample_count, started, "model_input_mismatch", str(exc))
    except FeatureExtractionError as exc:
        return _error_result(unit, display_name, sample_count, started, "feature_error", str(exc))
    except ImportError as exc:
        return _error_result(unit, display_name, sample_count, started, "dependency_missing", str(exc))
    except Exception as exc:
        return _error_result(unit, display_name, sample_count, started, "model_error", str(exc))


def _error_result(unit: str, name: str, sample_count: int, started: float, status: str, message: str) -> PredictionResult:
    return PredictionResult(
        value=None,
        unit=unit,
        confidence=None,
        model_name=name,
        model_version="",
        model_id="",
        model_type="",
        preprocessing="",
        sample_count=sample_count,
        elapsed_time=round(time.perf_counter() - started, 3),
        status=status,
        error_message=message,
    )


def _effective_sample_count(sample_data: SampleSession) -> int:
    if sample_data.multispectral_files:
        return 1
    return 1 if sample_data.rgb_files else 0


def _select_registry_model(*, target: str, fruit_type: str, variety: str, selected_model_id: str = "") -> dict | None:
    database = MODEL_ROOT.parent / "model_studio" / "database" / "model_studio.sqlite"
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
