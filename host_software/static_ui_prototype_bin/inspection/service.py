from __future__ import annotations

import json
import html
import threading
from pathlib import Path
from typing import Any

from quality_algorithm.spectral_features import list_images
from runtime_support import APP_VERSION, RuntimePaths

from .repository import InspectionRepository, utc_timestamp


SOFTWARE_VERSION = APP_VERSION
TARGETS = {"ssc", "ta", "ph"}


class InspectionService:
    """Build immutable runtime snapshots and persist prediction results."""

    def __init__(self, app_dir: str | Path, *, software_version: str = SOFTWARE_VERSION) -> None:
        self.app_dir = Path(app_dir).resolve()
        self.runtime_paths = RuntimePaths.for_app(self.app_dir)
        self.database_path = (
            self.app_dir / "inspection" / "inspection.sqlite"
            if self.runtime_paths.root == self.app_dir
            else self.runtime_paths.database_dir / "inspection.sqlite"
        )
        self.repository = InspectionRepository(self.database_path)
        self.software_version = software_version
        self._lock = threading.RLock()

    def record_prediction(self, sample_data: Any, result: Any, inspection_id: str = "") -> dict[str, Any]:
        return self.record_predictions(sample_data, [result], inspection_id)

    def record_predictions(self, sample_data: Any, results: list[Any], inspection_id: str = "") -> dict[str, Any]:
        if not results:
            raise ValueError("at least one prediction result is required")
        with self._lock:
            snapshot = self._provenance(sample_data, results[0])
            current = self.repository.get(inspection_id) if inspection_id else None
            if current is None:
                inspection_id = self.repository.create(snapshot)
                current = self.repository.get(inspection_id)
            existing_results = {item["target"]: item for item in (current or {}).get("results", [])}
            normalized_results = [self._result(result) for result in results]
            for result in normalized_results:
                existing_results[result["target"]] = result
            statuses = [str(item.get("status") or "failed") for item in existing_results.values()]
            if len(existing_results) >= len(TARGETS):
                if all(status in {"success", "ok"} for status in statuses):
                    status, completed_at = "COMPLETED", utc_timestamp()
                elif any(status in {"success", "ok"} for status in statuses):
                    status, completed_at = "PARTIAL", utc_timestamp()
                else:
                    status, completed_at = "FAILED", utc_timestamp()
            elif any(status not in {"success", "ok"} for status in statuses):
                status, completed_at = "PARTIAL", None
            else:
                status, completed_at = "RUNNING", None
            self.repository.save_results(
                inspection_id,
                inspection_updates={
                    "status": status,
                    "completed_at": completed_at,
                    "pipeline_signature": snapshot.get("pipeline_signature") or (current or {}).get("pipelineSignature") or "",
                    "pipeline_contract": snapshot.get("pipeline_contract") or (current or {}).get("pipelineContract") or {},
                    "feature_pipeline": snapshot.get("feature_pipeline") or (current or {}).get("featurePipeline") or {},
                    "calibration": snapshot.get("calibration") or (current or {}).get("calibration") or {},
                    "background_reference": snapshot.get("background_reference") or (current or {}).get("backgroundReference") or {},
                    "registration": snapshot.get("registration") or (current or {}).get("registration") or {},
                },
                results=normalized_results,
            )
            return self.repository.get(inspection_id) or {}

    def list_inspections(self, **filters: Any) -> dict[str, Any]:
        return self.repository.list(**filters)

    def get_inspection(self, inspection_id: str) -> dict[str, Any] | None:
        return self.repository.get(inspection_id)

    def archive_inspection(self, inspection_id: str) -> bool:
        return self.repository.archive(inspection_id)

    def save_result_snapshot(self, inspection_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        # Snapshot values are supplied only by the completed current inspection;
        # absent fields stay absent rather than being synthesised for old records.
        self.repository.save_result_snapshot(inspection_id, dict(snapshot or {}))
        return self.get_inspection(inspection_id) or {}

    def export_text_report(self, inspection_id: str) -> tuple[str, str]:
        """Render only the immutable persisted inspection snapshot."""
        inspection = self.get_inspection(inspection_id)
        if inspection is None:
            raise KeyError("inspection not found")
        sample = inspection.get("sample") or {}
        lines = ["果实口感多光谱无损检测报告", f"检测记录: {inspection_id}",
                 f"样品: {sample.get('sampleName') or '--'} ({sample.get('sampleId') or '--'})",
                 f"种类/品种: {sample.get('fruitType') or '--'} / {sample.get('variety') or '--'}",
                 f"模式: {sample.get('sampleMode') or '--'}", f"状态: {inspection.get('status') or '--'}",
                 f"时间: {inspection.get('completedAt') or inspection.get('startedAt') or '--'}"]
        for result in inspection.get("results") or []:
            value = "缺失" if result.get("value") is None else str(result.get("value"))
            lines.append(f"{str(result.get('target') or '').upper()}: {value} {result.get('unit') or ''} · {result.get('status') or '--'} · 来源: {result.get('modelId') or '未提供'}")
            if result.get("errorMessage"):
                lines.append(f"  错误: {result['errorMessage']}")
        snapshot = inspection.get("resultSnapshot") or {}
        lines.append(f"二维形态: {json.dumps(snapshot.get('shapeMetrics'), ensure_ascii=False) if snapshot.get('shapeMetrics') else '未记录'}")
        lines.append(f"糖酸比: {snapshot.get('sugarAcidRatio', '未记录')} · 等级: {snapshot.get('grade', '未记录')} · 规则: {snapshot.get('gradeRuleVersion', '未记录')}")
        lines += [f"Pipeline Signature: {inspection.get('pipelineSignature') or '--'}", f"数据目录: {sample.get('samplePath') or '--'}"]
        token = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(sample.get("sampleId") or inspection_id))[:48]
        return "\n".join(lines) + "\n", f"inspection_{token}_{inspection_id[:12]}.txt"

    def export_html_report(self, inspection_id: str) -> tuple[str, str]:
        text, filename = self.export_text_report(inspection_id)
        return f"<!doctype html><meta charset='utf-8'><title>检测报告</title><pre>{html.escape(text)}</pre>", filename[:-4] + ".html"

    def _provenance(self, sample_data: Any, result: Any) -> dict[str, Any]:
        root = Path(str(getattr(sample_data, "analysis_data_dir", "") or "")).expanduser()
        metadata = self._read_metadata(root)
        contract = dict(getattr(result, "model_input_contract", None) or {})
        pipeline = dict(getattr(result, "feature_pipeline", None) or {})
        background = metadata.get("background_reference") or metadata.get("backgroundReference") or {}
        return {
            "sample_id": str(getattr(sample_data, "sample_id", "") or ""),
            "sample_name": str(getattr(sample_data, "sample_name", "") or ""),
            "fruit_type": str(getattr(sample_data, "fruit_type", "") or metadata.get("fruit_type") or ""),
            "variety": str(getattr(sample_data, "variety", "generic") or metadata.get("variety") or "generic"),
            "sample_mode": str(metadata.get("sample_mode") or "inspection"),
            "sample_path": str(root),
            "source_files": self._source_files(sample_data, root),
            "started_at": str(getattr(sample_data, "capture_time", "") or utc_timestamp()),
            "software_version": self.software_version,
            "pipeline_signature": str(getattr(result, "pipeline_signature", "") or getattr(result, "model_pipeline_signature", "") or ""),
            "pipeline_contract": contract,
            "feature_pipeline": pipeline,
            "calibration": self._calibration_provenance(root, metadata, contract),
            "background_reference": background if isinstance(background, dict) else {"value": background},
            "registration": self._registration_provenance(metadata, contract),
        }

    @staticmethod
    def _result(result: Any) -> dict[str, Any]:
        raw_status = str(getattr(result, "status", "failed") or "failed").lower()
        status = raw_status if raw_status in {"success", "ok"} else "failed"
        message = str(getattr(result, "error_message", "") or "")
        return {
            "target": str(getattr(result, "target", "") or InspectionService._target_from_unit(result)),
            "value": getattr(result, "value", None),
            "unit": getattr(result, "unit", ""),
            "status": status,
            "error_code": InspectionService._error_code(status, message),
            "error_message": message,
            "model_id": getattr(result, "model_id", ""),
            "model_name": getattr(result, "model_name", ""),
            "model_version": getattr(result, "model_version", ""),
            "model_type": getattr(result, "model_type", ""),
            "preprocessing": getattr(result, "preprocessing", ""),
            "model_pipeline_signature": getattr(result, "model_pipeline_signature", ""),
            "model_input_contract": getattr(result, "model_input_contract", None) or {},
            "elapsed_time": getattr(result, "elapsed_time", None),
        }

    @staticmethod
    def _target_from_unit(result: Any) -> str:
        name = str(getattr(result, "model_name", "") or "").lower()
        if "ph" in name:
            return "ph"
        if "ta" in name or "acid" in name:
            return "ta"
        return "ssc"

    @staticmethod
    def _error_code(status: str, message: str) -> str:
        for code in ("MODEL_INPUT_MISMATCH", "MODEL_INPUT_CONTRACT_MISSING", "CALIBRATION_MISSING", "BACKGROUND_REFERENCE_MISSING", "REGISTRATION_PROFILE_MISSING"):
            if code in message:
                return code
        return "" if status in {"success", "ok"} else status.upper()

    @staticmethod
    def _read_metadata(root: Path) -> dict[str, Any]:
        try:
            value = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _source_files(sample_data: Any, root: Path) -> list[dict[str, str]]:
        files: list[dict[str, str]] = []
        for role, values in (("rgb", getattr(sample_data, "rgb_files", [])), ("multispectral", getattr(sample_data, "multispectral_files", []))):
            for value in values or []:
                files.append({"role": role, "path": str(Path(value).resolve())})
        for role, folder in (("dark", root / "calibration" / "dark"), ("white", root / "calibration" / "white")):
            for value in list_images(folder):
                files.append({"role": role, "path": str(value.resolve())})
        metadata_path = root / "metadata.json"
        if metadata_path.exists():
            files.append({"role": "metadata", "path": str(metadata_path.resolve())})
        return files

    @staticmethod
    def _calibration_provenance(root: Path, metadata: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
        value = metadata.get("calibrationSet") or metadata.get("calibration_set") or metadata.get("calibration")
        if isinstance(value, dict):
            return dict(value)
        calibration_files = sorted((root / "calibration").glob("calibration_set_*.json"))
        if calibration_files:
            try:
                value = json.loads(calibration_files[0].read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    return {"calibrationSet": value, "referencePath": str(calibration_files[0].resolve())}
            except (OSError, ValueError):
                pass
        return {
            "calibrationId": metadata.get("calibrationId") or metadata.get("calibration_id") or "",
            "status": "complete" if (root / "calibration" / "dark").exists() and (root / "calibration" / "white").exists() else "missing",
            "mode": (contract.get("calibration") or {}).get("mode", ""),
            "darkPath": str(root / "calibration" / "dark"),
            "whitePath": str(root / "calibration" / "white"),
        }

    @staticmethod
    def _registration_provenance(metadata: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
        value = metadata.get("registration") or metadata.get("registration_profile") or metadata.get("registrationProfile")
        if isinstance(value, dict):
            return dict(value)
        analysis = metadata.get("analysis_pipeline") or metadata.get("analysisPipeline") or {}
        profile_reference = analysis.get("registration_profile") or analysis.get("registrationProfile") if isinstance(analysis, dict) else ""
        registration = contract.get("registration") or {}
        return {
            "profileReference": profile_reference or "",
            "profileDigest": registration.get("profile_content_digest", ""),
            "profileVersion": registration.get("profile_schema_version"),
            "method": registration.get("profile_method", ""),
            "referenceBandNm": registration.get("reference_band_nm"),
        }
