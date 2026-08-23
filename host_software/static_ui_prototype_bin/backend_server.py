from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image, ImageDraw

class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def create(self) -> tuple[str, threading.Event]:
        job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        cancel = threading.Event()
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "status": "waiting",
                "step": "waiting",
                "progress": 0,
                "message": "等待执行",
                "logs": [],
                "result": None,
                "error": None,
                "startedAt": time.time(),
                "_cancel": cancel,
            }
        return job_id, cancel

    def update(self, job_id: str, **changes) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(changes)

    def append_log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            stamp = time.strftime("%H:%M:%S")
            job["logs"].append(f"[{stamp}] {message}")

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return {k: v for k, v in job.items() if not k.startswith("_")}

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job["_cancel"].set()
            job["status"] = "cancelling"
            job["message"] = "正在取消任务"
            return True


class SessionState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sample_id: str = ""
        self.sample_name: str = ""
        self.created_at: str = ""
        self.save_root_dir: str = ""
        self.current_capture_dir: str = ""
        self.analysis_data_dir: str = ""
        self.capture_started: bool = False
        self.fruit_type: str = ""
        self.variety: str = "generic"
        self.selected_ssc_model_id: str = ""
        self.selected_ta_model_id: str = ""
        self.selected_ph_model_id: str = ""

    def set_current_capture_dir(self, value: str | Path) -> None:
        with self._lock:
            self.current_capture_dir = str(value)

    def set_analysis_data_dir(self, value: str | Path) -> None:
        with self._lock:
            self.analysis_data_dir = str(value)

    def create_sample(self, payload: dict, model_resolver=None) -> dict:
        sample_name = str(payload.get("sampleName") or payload.get("sample_name") or "").strip()
        if not sample_name:
            raise ValueError("sample_name is required")
        fruit_type = str(payload.get("fruitType") or payload.get("fruit_type") or "").strip()
        if not fruit_type:
            raise ValueError("fruit_type is required")
        save_root_dir = str(payload.get("saveRootDir") or payload.get("save_root_dir") or "").strip()
        if not save_root_dir:
            raise ValueError("save_root_dir is required")
        capture_dir = str(payload.get("captureDir") or payload.get("capture_dir") or "").strip()
        if not capture_dir:
            raise ValueError("capture_dir is required")
        variety = str(payload.get("variety") or "generic").strip() or "generic"
        selected_ssc = str(payload.get("selectedSscModelId") or payload.get("selected_ssc_model_id") or "")
        selected_ta = str(payload.get("selectedTaModelId") or payload.get("selected_ta_model_id") or "")
        selected_ph = str(payload.get("selectedPhModelId") or payload.get("selected_ph_model_id") or "")
        if model_resolver:
            selected_ssc = model_resolver(fruit_type, variety, "ssc", selected_ssc) if selected_ssc else ""
            selected_ta = model_resolver(fruit_type, variety, "ta", selected_ta) if selected_ta else ""
            selected_ph = model_resolver(fruit_type, variety, "ph", selected_ph) if selected_ph else ""
        stamp = time.strftime("%Y%m%d_%H%M%S")
        sample_id = f"S{stamp}_{uuid.uuid4().hex[:8]}"
        created_at = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self.sample_id = sample_id
            self.sample_name = sample_name
            self.created_at = created_at
            self.save_root_dir = save_root_dir
            self.fruit_type = fruit_type
            self.variety = variety
            self.selected_ssc_model_id = selected_ssc
            self.selected_ta_model_id = selected_ta
            self.selected_ph_model_id = selected_ph
            self.current_capture_dir = capture_dir
            self.analysis_data_dir = ""
            self.capture_started = False
        return self.snapshot()

    def update_model_selection(self, payload: dict) -> None:
        with self._lock:
            self.fruit_type = str(payload.get("fruitType") or payload.get("fruit_type") or self.fruit_type or "").strip()
            self.variety = str(payload.get("variety") or self.variety or "generic").strip() or "generic"
            self.selected_ssc_model_id = str(payload.get("selectedSscModelId") or payload.get("selected_ssc_model_id") or self.selected_ssc_model_id or "")
            self.selected_ta_model_id = str(payload.get("selectedTaModelId") or payload.get("selected_ta_model_id") or self.selected_ta_model_id or "")
            self.selected_ph_model_id = str(payload.get("selectedPhModelId") or payload.get("selected_ph_model_id") or self.selected_ph_model_id or "")

    def apply_sample_metadata(self, metadata: dict) -> None:
        with self._lock:
            fruit_type = str(metadata.get("fruit_type") or metadata.get("fruitType") or "").strip()
            variety = str(metadata.get("variety") or "").strip()
            if fruit_type:
                self.fruit_type = fruit_type
            if variety:
                self.variety = variety
            if not self.sample_name:
                self.sample_name = str(metadata.get("sample_name") or metadata.get("sampleName") or "").strip()
            if not self.sample_id:
                self.sample_id = str(metadata.get("sample_id") or metadata.get("sampleId") or "").strip()

    def snapshot(self) -> dict:
        with self._lock:
            sample_id = self.sample_id
            sample_name = self.sample_name
            created_at = self.created_at
            save_root_dir = self.save_root_dir
            current = self.current_capture_dir
            analysis = self.analysis_data_dir
            capture_started = self.capture_started
            fruit_type = self.fruit_type
            variety = self.variety
            selected_ssc_model_id = self.selected_ssc_model_id
            selected_ta_model_id = self.selected_ta_model_id
            selected_ph_model_id = self.selected_ph_model_id
        current_path = Path(current).expanduser() if current else None
        current_valid = bool(current_path and current_path.exists() and current_path.is_dir())
        if current and not current_valid:
            current = ""
            with self._lock:
                self.current_capture_dir = ""
        return {
            "hasSample": bool(sample_id),
            "sampleId": sample_id,
            "sampleName": sample_name,
            "createdAt": created_at,
            "saveRootDir": save_root_dir,
            "currentCaptureDir": current if current_valid else "",
            "currentCaptureValid": current_valid,
            "analysisDataDir": analysis,
            "captureStarted": capture_started,
            "fruitType": fruit_type,
            "variety": variety,
            "selectedSscModelId": selected_ssc_model_id,
            "selectedTaModelId": selected_ta_model_id,
            "selectedPhModelId": selected_ph_model_id,
            "currentCaptureMessage": "" if current_valid else "暂无本次拍摄数据",
        }

    def set_capture_started(self, value: bool = True) -> None:
        with self._lock:
            self.capture_started = value


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def default_sample_dataset(app_dir: Path) -> str:
    return ""


def default_save_root(app_dir: Path) -> str:
    return str(app_dir.parent / "Data")


def read_sample_metadata(dataset_dir: str | Path) -> dict:
    root = Path(dataset_dir).expanduser()
    metadata_path = root / "metadata.json"
    if not metadata_path.exists() or not metadata_path.is_file():
        return {}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def create_handler(static_dir: Path, outputs_dir: Path, app_dir: Path, store: JobStore, session: SessionState):
    static_dir = static_dir.resolve()
    outputs_dir = outputs_dir.resolve()
    app_dir = app_dir.resolve()
    model_studio_static = app_dir / "model_studio" / "static"
    try:
        from model_studio.service import ModelStudioService

        model_studio = ModelStudioService(app_dir)
    except Exception:
        model_studio = None

    class Handler(BaseHTTPRequestHandler):
        server_version = "FruitTasteAnalyzer/1.0"

        def log_message(self, format, *args):  # noqa: A003
            return

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/model-studio":
                self.serve_file(model_studio_static, "index.html")
                return
            if path.startswith("/model-studio/"):
                self.serve_file(model_studio_static, path.removeprefix("/model-studio/") or "index.html")
                return
            if path == "/api/status":
                try:
                    from pointcloud_service import dependency_status

                    dependencies = dependency_status()
                except Exception as exc:
                    dependencies = {"error": str(exc)}
                session_info = session.snapshot()
                self.json_response({
                    "ok": True,
                    "dependencies": dependencies,
                    "sampleDataset": default_sample_dataset(app_dir),
                    "sampleDatasets": {},
                    "defaultSaveRoot": default_save_root(app_dir),
                    **session_info,
                })
                return
            if path == "/api/sample-folder":
                self.handle_sample_folder(parsed.query)
                return
            if path == "/api/select-folder":
                self.handle_select_folder(parsed.query)
                return
            if path == "/api/select-file":
                self.handle_select_file(parsed.query)
                return
            if path == "/api/select-dataset":
                self.handle_select_dataset()
                return
            if path == "/api/select-save-root":
                self.handle_select_save_root()
                return
            if path == "/api/quality-models":
                self.handle_quality_models(parsed.query)
                return
            if path == "/api/dataset-images":
                self.handle_dataset_images(parsed.query)
                return
            if path == "/api/local-image":
                self.handle_local_image(parsed.query)
                return
            if path.startswith("/api/jobs/"):
                job_id = path.rsplit("/", 1)[-1]
                job = store.get(job_id)
                if not job:
                    self.json_response({"ok": False, "error": "任务不存在"}, status=404)
                    return
                self.json_response({"ok": True, "job": job})
                return
            if path.startswith("/api/model-studio"):
                self.handle_model_studio_get(parsed)
                return
            if path.startswith("/outputs/"):
                self.serve_file(outputs_dir, path.removeprefix("/outputs/"))
                return
            self.serve_file(static_dir, "index.html" if path in ("/", "") else path.lstrip("/"))

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/upload-dataset":
                self.handle_upload_dataset()
                return
            if parsed.path == "/api/new-sample":
                self.handle_new_sample()
                return
            if parsed.path == "/api/complete-capture":
                self.handle_complete_capture()
                return
            if parsed.path == "/api/analyze-shape":
                self.handle_analyze_shape()
                return
            if parsed.path == "/api/predict-ssc":
                self.handle_predict_ssc()
                return
            if parsed.path == "/api/predict-acid":
                self.handle_predict_acid()
                return
            if parsed.path == "/api/model-selection":
                self.handle_model_selection()
                return
            if parsed.path == "/api/open-folder":
                self.handle_open_folder()
                return
            if parsed.path.startswith("/api/model-studio"):
                self.handle_model_studio_post(parsed)
                return
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
                job_id = parsed.path.split("/")[-2]
                self.json_response({"ok": store.cancel(job_id)})
                return
            if parsed.path == "/api/shutdown":
                setattr(self.server, "should_exit", True)
                self.json_response({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.json_response({"ok": False, "error": "未知 API"}, status=404)

        def require_model_studio(self):
            if model_studio is None:
                self.json_response({"ok": False, "error": "Model Studio backend is not available."}, status=500)
                return None
            return model_studio

        def require_current_sample(self) -> dict | None:
            info = session.snapshot()
            if not info.get("hasSample"):
                self.json_response({"ok": False, "code": "SAMPLE_REQUIRED", "error": "请先创建当前样品。"}, status=400)
                return None
            return info

        def resolve_model_id(self, fruit_type: str, variety: str, target: str, selected_id: str = "") -> str:
            studio = self.require_model_studio()
            if studio is None:
                return ""
            if selected_id:
                model = studio.get_model(selected_id)
                if model.get("status") not in {"Published", "Default", "Production"}:
                    raise ValueError("只能选择已发布、默认或生产模型。")
                if str(model.get("target") or "").lower() != target:
                    raise ValueError(f"{target.upper()} 不能选择其他指标模型。")
                if str(model.get("fruit_type") or "").lower() != str(fruit_type or "").lower():
                    raise ValueError("模型水果类型与当前样品不匹配。")
                model_variety = str(model.get("variety") or "generic").lower()
                sample_variety = str(variety or "generic").lower()
                if model_variety not in {"", "generic", sample_variety}:
                    raise ValueError("模型品种与当前样品不匹配。")
                return selected_id
            catalog = studio.model_catalog(fruit_type=fruit_type, variety=variety)
            model = (catalog.get("defaults") or {}).get(target)
            return model.get("model_id") if model else ""

        def resolve_creation_model_id(self, fruit_type: str, variety: str, target: str, selected_id: str = "") -> str:
            try:
                return self.resolve_model_id(fruit_type, variety, target, selected_id)
            except Exception:
                return self.resolve_model_id(fruit_type, variety, target, "")

        def handle_model_studio_get(self, parsed) -> None:
            studio = self.require_model_studio()
            if studio is None:
                return
            params = parse_qs(parsed.query)
            path = parsed.path.removeprefix("/api/model-studio").strip("/")
            try:
                if path in {"", "dashboard"}:
                    self.json_response({"ok": True, "dashboard": studio.dashboard()})
                    return
                if path == "datasets":
                    self.json_response({"ok": True, "datasets": studio.list_datasets()})
                    return
                if path == "dataset-versions":
                    dataset_id = params.get("datasetId", params.get("dataset_id", [""]))[0]
                    self.json_response({"ok": True, "versions": studio.list_dataset_versions(dataset_id)})
                    return
                if path == "samples":
                    dataset_id = params.get("datasetId", params.get("dataset_id", [""]))[0]
                    sample_id = params.get("sampleId", params.get("sample_id", [""]))[0]
                    if sample_id:
                        self.json_response({"ok": True, "sample": studio.get_sample(dataset_id, sample_id)})
                        return
                    self.json_response({
                        "ok": True,
                        "samples": studio.list_samples(
                            dataset_id,
                            limit=int(params.get("limit", ["50"])[0]),
                            offset=int(params.get("offset", ["0"])[0]),
                            query=params.get("query", [""])[0],
                        ),
                    })
                    return
                if path == "select-sample-folder":
                    initial = params.get("initial", [""])[0] or default_save_root(app_dir)
                    selected = select_directory_dialog("选择要导入 Model Studio 的样品文件夹", initial)
                    if not selected:
                        self.json_response({"ok": False, "cancelled": True, "error": "用户取消选择"})
                        return
                    self.json_response({"ok": True, "sourcePath": selected})
                    return
                if path == "quality":
                    dataset_id = params.get("datasetId", params.get("dataset_id", [""]))[0]
                    self.json_response({"ok": True, "quality": studio.quality_report(dataset_id)})
                    return
                if path == "experiments":
                    self.json_response({"ok": True, "experiments": studio.list_experiments()})
                    return
                if path == "jobs":
                    self.json_response({"ok": True, "jobs": studio.list_jobs()})
                    return
                if path.startswith("jobs/"):
                    self.json_response({"ok": True, "job": studio.get_job(path.split("/")[-1])})
                    return
                if path == "models":
                    self.json_response({"ok": True, "models": studio.list_models()})
                    return
                if path == "published-models":
                    self.json_response({"ok": True, "models": studio.list_published_models(
                        fruit_type=params.get("fruitType", params.get("fruit_type", [""]))[0],
                        variety=params.get("variety", [""])[0],
                        target=params.get("target", [""])[0],
                    )})
                    return
                if path == "logs":
                    self.json_response({"ok": True, "logs": studio.logs()})
                    return
                self.json_response({"ok": False, "error": "Unknown Model Studio API."}, status=404)
            except Exception as exc:
                self.json_response({"ok": False, "error": str(exc)}, status=400)

        def handle_model_studio_post(self, parsed) -> None:
            studio = self.require_model_studio()
            if studio is None:
                return
            path = parsed.path.removeprefix("/api/model-studio").strip("/")
            payload = self.read_json()
            try:
                if path == "datasets":
                    storage_path = payload.get("storagePath") or payload.get("storage_path")
                    if storage_path:
                        payload["storage_path"] = str(resolve_user_path(storage_path, app_dir))
                    self.json_response({"ok": True, "dataset": studio.create_dataset(payload)})
                    return
                if path == "samples/import":
                    dataset_id = payload.get("datasetId") or payload.get("dataset_id")
                    source_path = payload.get("sourcePath") or payload.get("source_path")
                    if source_path:
                        source_path = resolve_user_path(source_path, app_dir)
                    self.json_response({"ok": True, "result": studio.import_samples(
                        dataset_id,
                        source_path,
                        payload.get("duplicatePolicy") or payload.get("duplicate_policy") or "skip",
                    )})
                    return
                if path == "samples/validate":
                    source_path = payload.get("sourcePath") or payload.get("source_path")
                    self.json_response({"ok": True, "validation": studio.validate_sample_folder(resolve_user_path(source_path, app_dir))})
                    return
                if path == "samples/status":
                    self.json_response({"ok": True, "sample": studio.update_sample_status(
                        payload.get("datasetId") or payload.get("dataset_id"),
                        payload.get("sampleId") or payload.get("sample_id"),
                        payload.get("includeStatus") or payload.get("include_status") or "Included",
                        payload.get("reason") or payload.get("excludeReason") or "",
                    )})
                    return
                if path == "samples/delete":
                    self.json_response({"ok": True, "result": studio.delete_sample(
                        payload.get("datasetId") or payload.get("dataset_id"),
                        payload.get("sampleId") or payload.get("sample_id"),
                        delete_local_copy=bool(payload.get("deleteLocalCopy") or payload.get("delete_local_copy")),
                    )})
                    return
                if path == "labels/import":
                    dataset_id = payload.get("datasetId") or payload.get("dataset_id")
                    labels_path = payload.get("labelsCsvPath") or payload.get("labels_csv")
                    self.json_response({"ok": True, "result": studio.import_labels(dataset_id, resolve_user_path(labels_path, app_dir))})
                    return
                if path == "labels/save":
                    dataset_id = payload.get("datasetId") or payload.get("dataset_id")
                    sample_id = payload.get("sampleId") or payload.get("sample_id")
                    self.json_response({"ok": True, "sample": studio.save_sample_label(dataset_id, sample_id, payload)})
                    return
                if path == "dataset-versions":
                    dataset_id = payload.get("datasetId") or payload.get("dataset_id")
                    self.json_response({"ok": True, "version": studio.create_dataset_version(dataset_id, payload.get("description") or "")})
                    return
                if path == "features":
                    dataset_id = payload.get("datasetId") or payload.get("dataset_id")
                    self.json_response({"ok": True, "features": studio.generate_features(dataset_id, payload.get("datasetVersionId") or payload.get("dataset_version_id"))})
                    return
                if path == "experiments":
                    self.json_response({"ok": True, "experiment": studio.create_experiment(payload)})
                    return
                if path == "experiments/clone":
                    self.json_response({"ok": True, "experiment": studio.clone_experiment(
                        payload.get("experimentId") or payload.get("experiment_id"),
                        payload.get("experimentName") or payload.get("name"),
                    )})
                    return
                if path == "experiments/retrain":
                    self.json_response({"ok": True, "experiment": studio.retrain_from_model(
                        payload.get("modelId") or payload.get("model_id"),
                        payload.get("datasetVersionId") or payload.get("dataset_version_id"),
                        payload.get("experimentName") or payload.get("name"),
                    )})
                    return
                if path == "jobs":
                    experiment_id = payload.get("experimentId") or payload.get("experiment_id")
                    self.json_response({"ok": True, "job": studio.create_training_job(experiment_id)})
                    return
                if path.startswith("jobs/") and path.endswith("/cancel"):
                    job_id = path.split("/")[-2]
                    self.json_response({"ok": True, "job": studio.cancel_job(job_id)})
                    return
                if path == "models/publish":
                    model_id = payload.get("modelId") or payload.get("model_id")
                    self.json_response({"ok": True, "model": studio.publish_model(model_id, payload)})
                    return
                if path == "models/validate":
                    model_id = payload.get("modelId") or payload.get("model_id")
                    self.json_response({"ok": True, "model": studio.validate_model(model_id, payload)})
                    return
                if path == "models/default":
                    model_id = payload.get("modelId") or payload.get("model_id")
                    self.json_response({"ok": True, "model": studio.set_default_model(model_id)})
                    return
                if path == "models/export":
                    model_id = payload.get("modelId") or payload.get("model_id")
                    self.json_response({"ok": True, "bundle": studio.export_model_bundle(model_id)})
                    return
                if path == "models/archive":
                    model_id = payload.get("modelId") or payload.get("model_id")
                    self.json_response({"ok": True, "model": studio.archive_model(model_id)})
                    return
                self.json_response({"ok": False, "error": "Unknown Model Studio API."}, status=404)
            except Exception as exc:
                self.json_response({"ok": False, "error": str(exc)}, status=400)

        def handle_quality_models(self, query: str) -> None:
            studio = self.require_model_studio()
            if studio is None:
                return
            params = parse_qs(query)
            fruit_type = params.get("fruitType", params.get("fruit_type", [""]))[0]
            variety = params.get("variety", ["generic"])[0] or "generic"
            try:
                catalog = studio.model_catalog(fruit_type=fruit_type, variety=variety)
                self.json_response({
                    "ok": True,
                    "fruitType": fruit_type,
                    "variety": variety,
                    "fruitTypes": catalog["fruitTypes"],
                    "varieties": catalog["varieties"],
                    "defaults": catalog["defaults"],
                    "ssc": catalog["compatible"]["ssc"],
                    "ta": catalog["compatible"]["ta"],
                    "ph": catalog["compatible"]["ph"],
                })
            except Exception as exc:
                self.json_response({"ok": False, "error": str(exc)}, status=400)

        def handle_new_sample(self) -> None:
            payload = self.read_json()
            try:
                save_root = resolve_user_path(str(payload.get("saveRootDir") or ""), app_dir)
                if not str(payload.get("saveRootDir") or "").strip():
                    raise ValueError("save_root_dir is required")
                save_root.mkdir(parents=True, exist_ok=True)
                sample_name = str(payload.get("sampleName") or payload.get("sample_name") or "").strip()
                capture_dir = create_unique_sample_folder(save_root, sample_name)
                payload["saveRootDir"] = str(save_root)
                payload["captureDir"] = str(capture_dir)
                sample = session.create_sample(payload, self.resolve_model_id)
                ensure_sample_capture_folder(capture_dir, sample)
                self.json_response({"ok": True, "sample": sample})
            except Exception as exc:
                self.json_response({"ok": False, "error": str(exc)}, status=400)

        def handle_model_selection(self) -> None:
            payload = self.read_json()
            current = session.snapshot()
            if not current.get("hasSample"):
                self.json_response({"ok": False, "code": "SAMPLE_REQUIRED", "error": "请先创建当前样品。"}, status=400)
                return
            try:
                fruit_type = str(payload.get("fruitType") or payload.get("fruit_type") or current.get("fruitType") or "").strip()
                variety = str(payload.get("variety") or current.get("variety") or "generic").strip() or "generic"
                payload["selectedSscModelId"] = self.resolve_model_id(fruit_type, variety, "ssc", str(payload.get("selectedSscModelId") or ""))
                payload["selectedTaModelId"] = self.resolve_model_id(fruit_type, variety, "ta", str(payload.get("selectedTaModelId") or ""))
                payload["selectedPhModelId"] = self.resolve_model_id(fruit_type, variety, "ph", str(payload.get("selectedPhModelId") or ""))
                session.update_model_selection(payload)
                self.json_response({"ok": True, "session": session.snapshot()})
            except Exception as exc:
                self.json_response({"ok": False, "error": str(exc)}, status=400)

        def handle_select_dataset(self) -> None:
            try:
                info = session.snapshot()
                initial = info.get("analysisDataDir") or info.get("currentCaptureDir") or info.get("saveRootDir") or default_save_root(app_dir)
                selected = select_directory_dialog("选择样品文件夹", initial)
                if not selected:
                    self.json_response({"ok": False, "cancelled": True, "error": "用户取消选择"})
                    return
                status = validate_folder_path(selected, purpose="sample", app_dir=app_dir)
                if not status["exists"] or not status["isDirectory"] or not status["readable"]:
                    self.json_response({"ok": False, "error": status["message"], "pathStatus": status}, status=400)
                    return
                if info.get("hasSample"):
                    session.set_analysis_data_dir(selected)
                self.json_response({"ok": True, "datasetDir": selected, "pathStatus": status})
            except Exception as exc:
                self.json_response({"ok": False, "error": f"打开目录选择器失败: {exc}"}, status=500)

        def handle_select_save_root(self) -> None:
            try:
                selected = select_directory_dialog("选择样品保存位置", default_save_root(app_dir))
                if not selected:
                    self.json_response({"ok": False, "cancelled": True, "error": "用户取消选择"})
                    return
                status = validate_folder_path(selected, purpose="save", app_dir=app_dir)
                if not status["writable"]:
                    self.json_response({"ok": False, "error": status["message"], "pathStatus": status}, status=400)
                    return
                self.json_response({"ok": True, "saveRootDir": selected, "pathStatus": status})
            except Exception as exc:
                self.json_response({"ok": False, "error": f"打开保存位置选择器失败: {exc}"}, status=500)

        def handle_select_folder(self, query: str) -> None:
            params = parse_qs(query)
            purpose = params.get("purpose", ["folder"])[0] or "folder"
            initial = params.get("initial", [""])[0] or default_save_root(app_dir)
            title = folder_picker_title(purpose)
            try:
                selected = select_directory_dialog(title, initial)
                if not selected:
                    self.json_response({"ok": False, "cancelled": True, "error": "用户取消选择"})
                    return
                status = validate_folder_path(selected, purpose=purpose, app_dir=app_dir)
                if not status["exists"] or not status["isDirectory"] or not status["readable"]:
                    self.json_response({"ok": False, "error": status["message"], "pathStatus": status}, status=400)
                    return
                if purpose in {"save", "export"} and not status["writable"]:
                    self.json_response({"ok": False, "error": status["message"], "pathStatus": status}, status=400)
                    return
                self.json_response({"ok": True, "path": selected, "purpose": purpose, "pathStatus": status})
            except Exception as exc:
                self.json_response({"ok": False, "error": f"打开文件夹选择器失败: {exc}"}, status=500)

        def handle_select_file(self, query: str) -> None:
            params = parse_qs(query)
            purpose = params.get("purpose", ["file"])[0] or "file"
            initial = params.get("initial", [""])[0] or default_save_root(app_dir)
            try:
                selected = select_file_dialog(file_picker_title(purpose), initial, purpose=purpose)
                if not selected:
                    self.json_response({"ok": False, "cancelled": True, "error": "用户取消选择"})
                    return
                status = validate_file_path(selected, purpose=purpose, app_dir=app_dir)
                if not status["exists"] or not status["isFile"] or not status["readable"] or status["state"] != "已选择":
                    self.json_response({"ok": False, "error": status["message"], "pathStatus": status}, status=400)
                    return
                self.json_response({"ok": True, "path": selected, "purpose": purpose, "pathStatus": status})
            except Exception as exc:
                self.json_response({"ok": False, "error": f"打开文件选择器失败: {exc}"}, status=500)

        def handle_open_folder(self) -> None:
            info = self.require_current_sample()
            if info is None:
                return
            payload = self.read_json()
            target = resolve_user_path(str(payload.get("path") or info.get("currentCaptureDir") or ""), app_dir)
            if not target.exists() or not target.is_dir():
                self.json_response({"ok": False, "error": "样品文件夹不存在。"}, status=400)
                return
            try:
                open_folder_in_explorer(target)
                self.json_response({"ok": True, "path": str(target)})
            except Exception as exc:
                self.json_response({"ok": False, "error": f"打开文件夹失败: {exc}"}, status=500)

        def handle_sample_folder(self, query: str) -> None:
            params = parse_qs(query)
            dataset_dir = params.get("datasetDir", [""])[0]
            color_dir = params.get("colorDir", [""])[0] or None
            depth_dir = params.get("depthDir", [""])[0] or None
            source = params.get("source", [""])[0]
            if source == "current":
                current = session.snapshot().get("currentCaptureDir", "")
                if current and not dataset_dir:
                    dataset_dir = current
                if not current:
                    self.json_response({
                        "ok": True,
                        "valid": False,
                        "complete": False,
                        "status": "missing",
                        "datasetDir": "",
                        "colorDir": "",
                        "depthDir": "",
                        "rgbCount": 0,
                        "spectralCount": 0,
                        "pairCount": 0,
                        "missing": ["本次采集目录已不存在，请重新采集或选择其他文件夹。"],
                        "badImages": [],
                        "message": "本次采集目录已不存在，请重新采集或选择其他文件夹。",
                    })
                    return
            try:
                from pointcloud_service import inspect_sample_folder

                resolved_dataset = resolve_user_path(dataset_dir, app_dir) if dataset_dir else ""
                report = inspect_sample_folder(resolved_dataset, color_dir, depth_dir)
                metadata = read_sample_metadata(report.get("datasetDir") or resolved_dataset) if report.get("datasetDir") or resolved_dataset else {}
                if metadata:
                    report["sampleMetadata"] = metadata
                    if session.snapshot().get("hasSample"):
                        session.apply_sample_metadata(metadata)
                if report.get("valid") and session.snapshot().get("hasSample"):
                    session.set_analysis_data_dir(report["datasetDir"])
                self.json_response(report)
            except Exception as exc:
                self.json_response({
                    "ok": True,
                    "valid": False,
                    "complete": False,
                    "status": "invalid",
                    "datasetDir": dataset_dir,
                    "colorDir": "",
                    "depthDir": "",
                    "rgbCount": 0,
                    "spectralCount": 0,
                    "pairCount": 0,
                    "missing": [str(exc)],
                    "badImages": [],
                    "message": str(exc),
                })

        def handle_dataset_images(self, query: str) -> None:
            params = parse_qs(query)
            dataset_dir = params.get("datasetDir", [""])[0] or str(default_sample_dataset(app_dir))
            color_dir = params.get("colorDir", [""])[0] or None
            depth_dir = params.get("depthDir", [""])[0] or None
            if not dataset_dir:
                self.json_response({
                    "ok": True,
                    "colorDir": "",
                    "depthDir": "",
                    "images": [],
                })
                return
            try:
                from pointcloud_service import AnalysisError, list_images, resolve_image_analysis_dirs

                color_path, depth_path = resolve_image_analysis_dirs(resolve_user_path(dataset_dir, app_dir), color_dir, depth_dir)
                color_files = list_images(color_path)
                spectral_files = list_images(depth_path) if depth_path else []
                pair_count = min(max(len(color_files), len(spectral_files)), 60) if spectral_files else min(len(color_files), 60)

                def row(path: Path) -> dict:
                    return {
                        "name": path.name,
                        "url": f"/api/local-image?path={quote(str(path), safe='')}",
                    }

                self.json_response({
                    "ok": True,
                    "colorDir": str(color_path),
                    "depthDir": str(depth_path) if depth_path else "",
                    "images": [
                        {
                            "index": index,
                            "color": row(color_files[index % len(color_files)]),
                            "depth": row(spectral_files[index % len(spectral_files)]) if spectral_files else None,
                        }
                        for index in range(pair_count)
                    ],
                })
            except Exception as exc:
                code = getattr(exc, "code", "NO_IMAGES")
                self.json_response({"ok": False, "code": code, "error": str(exc)}, status=400)

        def handle_local_image(self, query: str) -> None:
            params = parse_qs(query)
            raw_path = params.get("path", [""])[0]
            target = Path(raw_path).expanduser()
            if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
                self.send_error(403)
                return
            if not target.exists() or not target.is_file():
                self.send_error(404)
                return
            mime = mimetypes.guess_type(str(target))[0] or "image/png"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def handle_upload_dataset(self) -> None:
            if self.require_current_sample() is None:
                return
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self.json_response({"ok": False, "error": "请选择样品图像文件夹。"}, status=400)
                return

            try:
                import cgi

                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ={
                        "REQUEST_METHOD": "POST",
                        "CONTENT_TYPE": content_type,
                        "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
                    },
                )
                files = form["files"] if "files" in form else []
                if not isinstance(files, list):
                    files = [files]

                upload_root = outputs_dir.parent / "uploads" / (time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6])
                upload_root.mkdir(parents=True, exist_ok=True)
                saved = 0
                top_dirs: list[str] = []
                for item in files:
                    filename = getattr(item, "filename", "") or ""
                    relative = safe_upload_relative(filename)
                    if relative is None:
                        continue
                    target = upload_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("wb") as handle:
                        shutil.copyfileobj(item.file, handle)
                    saved += 1
                    if len(relative.parts) > 1:
                        top_dirs.append(relative.parts[0])

                if saved == 0:
                    self.json_response({"ok": False, "error": "未读取到可用文件。"}, status=400)
                    return

                dataset_dir = upload_root
                unique_top = sorted(set(top_dirs))
                if len(unique_top) == 1 and (upload_root / unique_top[0]).is_dir():
                    dataset_dir = upload_root / unique_top[0]
                session.set_analysis_data_dir(dataset_dir)
                self.json_response({"ok": True, "datasetDir": str(dataset_dir), "fileCount": saved})
            except Exception as exc:
                self.json_response({"ok": False, "error": f"上传数据集失败: {exc}"}, status=500)

        def handle_complete_capture(self) -> None:
            info = self.require_current_sample()
            if info is None:
                return
            payload = self.read_json()
            capture_dir = str(info.get("currentCaptureDir") or "").strip()
            if not capture_dir:
                self.json_response({"ok": False, "error": "当前样品保存目录不存在，请重新创建样品。"}, status=400)
                return
            sample_id = str(info.get("sampleName") or info.get("sampleId") or payload.get("sampleId") or "").strip()
            try:
                session.set_capture_started(True)
                capture_dir = create_offline_capture_dataset(app_dir, sample_id, capture_dir=resolve_user_path(capture_dir, app_dir), metadata=info)
                session.set_current_capture_dir(capture_dir)
                session.set_analysis_data_dir(capture_dir)
                self.json_response({
                    "ok": True,
                    "currentCaptureDir": str(capture_dir),
                    "analysisDataDir": str(capture_dir),
                    "message": "本次拍摄数据已保存",
                })
            except Exception as exc:
                self.json_response({"ok": False, "error": f"保存本次拍摄数据失败: {exc}"}, status=500)

        def handle_analyze_shape(self) -> None:
            if self.require_current_sample() is None:
                return
            payload = self.read_json()
            dataset_dir = payload.get("datasetDir") or str(default_sample_dataset(app_dir))
            color_dir = payload.get("colorDir") or None
            depth_dir = payload.get("depthDir") or None
            if not dataset_dir:
                self.json_response({"ok": False, "error": "请先选择本次拍摄的样品文件夹。"}, status=400)
                return
            session.set_analysis_data_dir(dataset_dir)
            density = _float(payload.get("densityGCm3"), 1.08)
            voxel = _float(payload.get("voxelSizeMm"), 2.0)
            max_pairs = int(_float(payload.get("maxPairs"), 10))

            job_id, cancel = store.create()
            output_dir = outputs_dir / job_id

            def run_job() -> None:
                store.update(job_id, status="running")

                def progress(step: str, percent: int, message: str) -> None:
                    store.update(job_id, step=step, progress=percent, message=message)
                    store.append_log(job_id, message)

                try:
                    from pointcloud_service import AnalysisError, AnalysisOptions, CameraIntrinsics, analyze_rgbd_dataset

                    options = AnalysisOptions(
                        camera=CameraIntrinsics(
                            fx=_float(payload.get("fx"), 652.77),
                            fy=_float(payload.get("fy"), 652.77),
                            cx=_float(payload.get("cx"), 631.75),
                            cy=_float(payload.get("cy"), 364.95),
                        ),
                        density_g_cm3=density,
                        voxel_size_mm=voxel,
                        max_pairs=max_pairs,
                    )
                    result = analyze_rgbd_dataset(
                        dataset_dir,
                        output_dir,
                        color_dir=color_dir,
                        depth_dir=depth_dir,
                        options=options,
                        progress=progress,
                        cancel_flag=cancel.is_set,
                    )
                    store.update(job_id, status="done", progress=100, message="分析成功", result=result)
                except Exception as exc:
                    code = getattr(exc, "code", "ALGORITHM_FAILED")
                    if code != "ALGORITHM_FAILED":
                        status = "cancelled" if code == "CANCELLED" else "failed"
                        store.update(job_id, status=status, error={"code": code, "message": str(exc)}, message=str(exc))
                        store.append_log(job_id, str(exc))
                        return
                    store.update(
                        job_id,
                        status="failed",
                        error={"code": code, "message": str(exc), "traceback": traceback.format_exc()},
                        message=f"算法执行失败: {exc}",
                    )
                    store.append_log(job_id, f"算法执行失败: {exc}")

            threading.Thread(target=run_job, daemon=True).start()
            self.json_response({"ok": True, "jobId": job_id})

        def handle_predict_ssc(self) -> None:
            payload = self.read_json()
            quality = self.build_quality_session(payload)
            if quality is None:
                return
            sample_data, report = quality
            try:
                from quality_prediction import predict_ssc

                result = predict_ssc(sample_data)
                sample_data.ssc_result = result.to_dict()
                self.json_response({
                    "ok": True,
                    "sample": sample_data.to_dict(),
                    "dataCheck": report,
                    "result": result.to_dict(),
                })
            except Exception as exc:
                self.json_response({"ok": False, "error": f"SSC 预测接口执行失败: {exc}"}, status=500)

        def handle_predict_acid(self) -> None:
            payload = self.read_json()
            quality = self.build_quality_session(payload)
            if quality is None:
                return
            sample_data, report = quality
            try:
                from quality_prediction import predict_ph, predict_ta

                ta_result = predict_ta(sample_data)
                ph_result = predict_ph(sample_data)
                sample_data.ta_result = ta_result.to_dict()
                sample_data.ph_result = ph_result.to_dict()
                self.json_response({
                    "ok": True,
                    "sample": sample_data.to_dict(),
                    "dataCheck": report,
                    "taResult": ta_result.to_dict(),
                    "phResult": ph_result.to_dict(),
                })
            except Exception as exc:
                self.json_response({"ok": False, "error": f"酸度预测接口执行失败: {exc}"}, status=500)

        def build_quality_session(self, payload: dict):
            if self.require_current_sample() is None:
                return None
            session.update_model_selection(payload)
            session_info = session.snapshot()
            dataset_dir = payload.get("datasetDir") or session_info.get("analysisDataDir", "")
            if not dataset_dir:
                self.json_response({"ok": False, "error": "请先在形态分析页面加载当前样品数据。"}, status=400)
                return None
            color_dir = payload.get("colorDir") or None
            depth_dir = payload.get("depthDir") or None
            try:
                resolved_dataset = resolve_user_path(dataset_dir, app_dir)
                metadata = read_sample_metadata(resolved_dataset)
                sample_id = str(session_info.get("sampleId") or payload.get("sampleId") or metadata.get("sample_id") or metadata.get("sampleId") or "").strip()
                fruit_type = session_info.get("fruitType") or payload.get("fruitType") or metadata.get("fruit_type") or metadata.get("fruitType") or ""
                variety = session_info.get("variety") or payload.get("variety") or metadata.get("variety") or "generic"
                from quality_prediction import build_sample_session

                sample_data, report = build_sample_session(
                    resolved_dataset,
                    sample_id=sample_id,
                    sample_name=session_info.get("sampleName") or metadata.get("sample_name") or metadata.get("sampleName") or "",
                    rgb_dir=color_dir,
                    spectral_dir=depth_dir,
                    capture_time=metadata.get("captured_at") or metadata.get("capturedAt") or metadata.get("created_at") or "",
                    fruit_type=fruit_type,
                    variety=variety,
                    selected_ssc_model_id=session_info.get("selectedSscModelId") or payload.get("selectedSscModelId") or "",
                    selected_ta_model_id=session_info.get("selectedTaModelId") or payload.get("selectedTaModelId") or "",
                    selected_ph_model_id=session_info.get("selectedPhModelId") or payload.get("selectedPhModelId") or "",
                )
                if report.get("valid"):
                    session.set_analysis_data_dir(sample_data.analysis_data_dir)
                return sample_data, report
            except Exception as exc:
                self.json_response({"ok": False, "error": f"当前样品数据检查失败: {exc}"}, status=400)
                return None

        def read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {}

        def json_response(self, payload: dict, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def serve_file(self, root: Path, relative: str) -> None:
            relative = unquote(relative).replace("\\", "/")
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                self.send_error(403)
                return
            if not target.exists() or not target.is_file():
                self.send_error(404)
                return
            mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _float(value, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def resolve_user_path(value: str | Path, app_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return app_dir / path


def folder_picker_title(purpose: str) -> str:
    titles = {
        "save": "选择样品保存位置",
        "sample": "选择样品文件夹",
        "dataset": "选择数据来源目录",
        "model-studio-source": "选择 Model Studio 数据来源目录",
        "model-studio-sample": "选择要导入 Model Studio 的样品文件夹",
        "export": "选择导出目录",
    }
    return titles.get(purpose, "选择文件夹")


def file_picker_title(purpose: str) -> str:
    titles = {
        "labels-csv": "选择 labels.csv",
        "csv": "选择 CSV 文件",
        "json": "选择 JSON 文件",
        "joblib": "选择模型文件",
        "model-bundle": "选择模型 Bundle 文件",
    }
    return titles.get(purpose, "选择文件")


def validate_folder_path(value: str | Path, *, purpose: str = "folder", app_dir: Path) -> dict:
    raw = str(value or "").strip()
    if not raw:
        return {
            "path": "",
            "state": "未选择",
            "exists": False,
            "isDirectory": False,
            "readable": False,
            "writable": False,
            "message": "未选择目录",
        }
    path = resolve_user_path(raw, app_dir)
    exists = path.exists()
    is_dir = exists and path.is_dir()
    readable = bool(is_dir and os.access(path, os.R_OK))
    writable = bool(is_dir and os.access(path, os.W_OK))
    if is_dir and purpose in {"save", "export"}:
        writable = writable and _can_write_directory(path)
    state = "已选择"
    message = "目录已选择"
    if not exists:
        state = "无效"
        message = "所选目录不存在"
    elif not is_dir:
        state = "无效"
        message = "所选路径不是文件夹"
    elif not readable:
        state = "不可读"
        message = "当前目录没有读取权限"
    elif purpose in {"save", "export"} and not writable:
        state = "不可写"
        message = "当前目录没有写入权限"
    return {
        "path": str(path),
        "state": state,
        "exists": exists,
        "isDirectory": is_dir,
        "readable": readable,
        "writable": writable,
        "message": message,
    }


def validate_file_path(value: str | Path, *, purpose: str = "file", app_dir: Path) -> dict:
    raw = str(value or "").strip()
    if not raw:
        return {
            "path": "",
            "state": "未选择",
            "exists": False,
            "isFile": False,
            "readable": False,
            "message": "未选择文件",
        }
    path = resolve_user_path(raw, app_dir)
    exists = path.exists()
    is_file = exists and path.is_file()
    readable = bool(is_file and os.access(path, os.R_OK))
    message = "文件已选择"
    state = "已选择"
    if not exists:
        state = "无效"
        message = "所选文件不存在"
    elif not is_file:
        state = "无效"
        message = "所选路径不是文件"
    elif not readable:
        state = "不可读"
        message = "当前文件没有读取权限"
    elif purpose in {"labels-csv", "csv"} and path.suffix.lower() != ".csv":
        state = "无效"
        message = "请选择 .csv 文件"
    elif purpose == "json" and path.suffix.lower() != ".json":
        state = "无效"
        message = "请选择 .json 文件"
    elif purpose == "joblib" and path.suffix.lower() not in {".joblib", ".pkl"}:
        state = "无效"
        message = "请选择 .joblib 或 .pkl 模型文件"
    return {
        "path": str(path),
        "state": state,
        "exists": exists,
        "isFile": is_file,
        "readable": readable,
        "message": message,
    }


def _can_write_directory(path: Path) -> bool:
    probe = path / f".fruit_analyzer_write_test_{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        return True
    except Exception:
        return False
    finally:
        try:
            if probe.exists():
                probe.unlink()
        except Exception:
            pass


def select_directory_dialog(title: str, initial_dir: str | Path | None = None) -> str:
    initial_path = Path(initial_dir).expanduser() if initial_dir else None
    initial = str(initial_path) if initial_path and initial_path.exists() else ""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        root.lift()
        root.focus_force()
        selected = filedialog.askdirectory(parent=root, title=title, initialdir=initial or None, mustexist=True)
        root.destroy()
        return selected or ""
    except Exception:
        return select_directory_with_powershell(title, initial)


def select_directory_with_powershell(title: str, initial_dir: str = "") -> str:
    if os.name != "nt":
        return ""
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$dialog.Description = {json.dumps(title, ensure_ascii=False)};"
        "$dialog.ShowNewFolderButton = $true;"
    )
    if initial_dir:
        script += f"$dialog.SelectedPath = {json.dumps(initial_dir, ensure_ascii=False)};"
    script += "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.SelectedPath }"
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""


def select_file_dialog(title: str, initial_dir: str | Path | None = None, *, purpose: str = "file") -> str:
    initial_path = Path(initial_dir).expanduser() if initial_dir else None
    if initial_path and initial_path.is_file():
        initial = str(initial_path.parent)
    else:
        initial = str(initial_path) if initial_path and initial_path.exists() else ""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        root.lift()
        root.focus_force()
        selected = filedialog.askopenfilename(
            parent=root,
            title=title,
            initialdir=initial or None,
            filetypes=file_dialog_filters(purpose),
        )
        root.destroy()
        return selected or ""
    except Exception:
        return select_file_with_powershell(title, initial, purpose)


def file_dialog_filters(purpose: str):
    if purpose in {"labels-csv", "csv"}:
        return [("CSV files", "*.csv"), ("All files", "*.*")]
    if purpose == "json":
        return [("JSON files", "*.json"), ("All files", "*.*")]
    if purpose == "joblib":
        return [("Model files", "*.joblib *.pkl"), ("All files", "*.*")]
    if purpose == "model-bundle":
        return [("Model bundles", "*.zip *.joblib *.pkl"), ("All files", "*.*")]
    return [("All files", "*.*")]


def select_file_with_powershell(title: str, initial_dir: str = "", purpose: str = "file") -> str:
    if os.name != "nt":
        return ""
    filter_text = {
        "labels-csv": "CSV files (*.csv)|*.csv|All files (*.*)|*.*",
        "csv": "CSV files (*.csv)|*.csv|All files (*.*)|*.*",
        "json": "JSON files (*.json)|*.json|All files (*.*)|*.*",
        "joblib": "Model files (*.joblib;*.pkl)|*.joblib;*.pkl|All files (*.*)|*.*",
        "model-bundle": "Model bundles (*.zip;*.joblib;*.pkl)|*.zip;*.joblib;*.pkl|All files (*.*)|*.*",
    }.get(purpose, "All files (*.*)|*.*")
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$dialog = New-Object System.Windows.Forms.OpenFileDialog;"
        f"$dialog.Title = {json.dumps(title, ensure_ascii=False)};"
        f"$dialog.Filter = {json.dumps(filter_text, ensure_ascii=False)};"
    )
    if initial_dir:
        script += f"$dialog.InitialDirectory = {json.dumps(initial_dir, ensure_ascii=False)};"
    script += "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.FileName }"
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""


def open_folder_in_explorer(target: Path) -> None:
    if os.name == "nt":
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
            return
        except Exception:
            subprocess.Popen(["explorer.exe", str(target)])
            return
    subprocess.Popen(["xdg-open", str(target)])


def safe_upload_relative(filename: str) -> Path | None:
    parts = []
    for part in filename.replace("\\", "/").split("/"):
        clean = part.strip()
        if not clean or clean in {".", ".."}:
            continue
        parts.append(clean)
    if not parts:
        return None
    return Path(*parts)


WINDOWS_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def sanitize_windows_name(value: str, fallback: str = "sample") -> str:
    clean = WINDOWS_INVALID_CHARS.sub("_", str(value or "").strip())
    clean = re.sub(r"\s+", "_", clean).strip(" ._")
    return (clean or fallback)[:48]


def create_unique_sample_folder(save_root: Path, sample_name: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe_name = sanitize_windows_name(sample_name)
    base = f"{stamp}_{safe_name}"
    candidate = save_root / base
    index = 2
    while candidate.exists():
        candidate = save_root / f"{base}_{index:02d}"
        index += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def ensure_sample_capture_folder(capture_root: Path, metadata: dict | None = None) -> None:
    (capture_root / "rgb").mkdir(parents=True, exist_ok=True)
    (capture_root / "multispectral").mkdir(parents=True, exist_ok=True)
    (capture_root / "calibration" / "dark").mkdir(parents=True, exist_ok=True)
    (capture_root / "calibration" / "white").mkdir(parents=True, exist_ok=True)
    if metadata is not None:
        meta = {
            "sample_id": metadata.get("sampleId") or metadata.get("sample_id") or "",
            "sample_name": metadata.get("sampleName") or metadata.get("sample_name") or "",
            "fruit_type": metadata.get("fruitType") or metadata.get("fruit_type") or "",
            "variety": metadata.get("variety") or "generic",
            "selected_ssc_model_id": metadata.get("selectedSscModelId") or "",
            "selected_ta_model_id": metadata.get("selectedTaModelId") or "",
            "selected_ph_model_id": metadata.get("selectedPhModelId") or "",
            "save_root_dir": metadata.get("saveRootDir") or "",
            "created_at": metadata.get("createdAt") or "",
            "captured_at": metadata.get("capturedAt") or metadata.get("captured_at") or "",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (capture_root / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def create_offline_capture_dataset(app_dir: Path, sample_id: str = "", capture_dir: str | Path | None = None, metadata: dict | None = None) -> Path:
    """Create a small current-capture folder for offline UI verification.

    Real camera integration should replace this function's image-writing block with
    camera frame saves, while keeping the returned sample root directory contract.
    """

    if capture_dir:
        capture_root = Path(capture_dir)
    else:
        default_root = app_dir.parent / "Data"
        default_root.mkdir(parents=True, exist_ok=True)
        capture_root = create_unique_sample_folder(default_root, sample_id)
    ensure_sample_capture_folder(capture_root, metadata)
    rgb_dir = capture_root / "rgb"
    spectral_dir = capture_root / "multispectral"
    dark_dir = capture_root / "calibration" / "dark"
    white_dir = capture_root / "calibration" / "white"

    for index in range(3):
        rgb_image = Image.new("RGB", (640, 420), (22, 32, 48))
        draw = ImageDraw.Draw(rgb_image)
        draw.rectangle((0, 330, 640, 420), fill=(36, 48, 64))
        offset = index * 10
        draw.ellipse((250 + offset, 120, 390 + offset, 270), fill=(94, 142, 63), outline=(146, 190, 95), width=4)
        draw.line((320 + offset, 118, 318 + offset, 82), fill=(84, 68, 44), width=5)
        draw.ellipse((326 + offset, 86, 365 + offset, 108), fill=(68, 130, 74))
        draw.text((22, 22), f"Offline Capture RGB {index + 1}", fill=(203, 213, 225))
        rgb_path = rgb_dir / f"rgb_{index + 1:03d}.png"
        rgb_image.save(rgb_path)

        spectral_image = Image.new("L", (640, 420), 16)
        sdraw = ImageDraw.Draw(spectral_image)
        band = 450 + index * 110
        shade = 96 + index * 36
        sdraw.ellipse((250 + offset, 120, 390 + offset, 270), fill=shade, outline=min(240, shade + 48), width=4)
        sdraw.text((22, 22), f"{band}nm", fill=220)
        spectral_path = spectral_dir / f"{band}.png"
        spectral_image.save(spectral_path)

        Image.new("L", (640, 420), 6).save(dark_dir / f"dark_{index + 1:03d}.png")
        Image.new("L", (640, 420), 235).save(white_dir / f"white_{index + 1:03d}.png")

    if metadata is not None:
        metadata = {**metadata, "capturedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
        ensure_sample_capture_folder(capture_root, metadata)

    expected = [rgb_dir / "rgb_001.png", spectral_dir / "450.png", dark_dir / "dark_001.png", white_dir / "white_001.png"]
    if not all(path.exists() for path in expected):
        raise RuntimeError("采集文件写入校验失败")
    return capture_root


def start_backend(static_dir: Path, outputs_dir: Path, app_dir: Path, port: int | None = None) -> tuple[ThreadingHTTPServer, int]:
    store = JobStore()
    session = SessionState()
    outputs_dir.mkdir(parents=True, exist_ok=True)
    selected_port = port or free_port()
    handler = create_handler(static_dir, outputs_dir, app_dir, store, session)
    server = ThreadingHTTPServer(("127.0.0.1", selected_port), handler)
    setattr(server, "should_exit", False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, selected_port
