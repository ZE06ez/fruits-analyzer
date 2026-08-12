from __future__ import annotations

import json
import mimetypes
import os
import shutil
import socket
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from device_manager import CameraIntegrationRequired, DeviceManager
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
        self.current_capture_dir: str = ""
        self.analysis_data_dir: str = ""

    def set_current_capture_dir(self, value: str | Path) -> None:
        with self._lock:
            self.current_capture_dir = str(value)

    def set_analysis_data_dir(self, value: str | Path) -> None:
        with self._lock:
            self.analysis_data_dir = str(value)

    def snapshot(self) -> dict:
        with self._lock:
            current = self.current_capture_dir
            analysis = self.analysis_data_dir
        current_path = Path(current).expanduser() if current else None
        current_valid = bool(current_path and current_path.exists() and current_path.is_dir())
        if current and not current_valid:
            current = ""
            with self._lock:
                self.current_capture_dir = ""
        return {
            "currentCaptureDir": current if current_valid else "",
            "currentCaptureValid": current_valid,
            "analysisDataDir": analysis,
            "currentCaptureMessage": "" if current_valid else "暂无本次拍摄数据",
        }


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def default_sample_dataset(app_dir: Path) -> str:
    return ""


def create_handler(
    static_dir: Path,
    outputs_dir: Path,
    app_dir: Path,
    store: JobStore,
    session: SessionState,
    device_manager: DeviceManager | None = None,
):
    static_dir = static_dir.resolve()
    outputs_dir = outputs_dir.resolve()
    app_dir = app_dir.resolve()

    device_manager = device_manager or DeviceManager()

    class Handler(BaseHTTPRequestHandler):
        server_version = "FruitTasteAnalyzer/1.0"

        def log_message(self, format, *args):  # noqa: A003
            return

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
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
                    **session_info,
                })
                return
            if path == "/api/sample-folder":
                self.handle_sample_folder(parsed.query)
                return
            if path == "/api/select-dataset":
                self.handle_select_dataset()
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
            if path.startswith("/outputs/"):
                self.serve_file(outputs_dir, path.removeprefix("/outputs/"))
                return
            self.serve_file(static_dir, "index.html" if path in ("/", "") else path.lstrip("/"))

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/upload-dataset":
                self.handle_upload_dataset()
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

        def handle_select_dataset(self) -> None:
            try:
                import tkinter as tk
                from tkinter import filedialog

                root = tk.Tk()
                root.withdraw()
                selected = filedialog.askdirectory(title="选择样品文件夹")
                root.destroy()
                if not selected:
                    self.json_response({"ok": False, "cancelled": True, "error": "用户取消选择"})
                    return
                session.set_analysis_data_dir(selected)
                self.json_response({"ok": True, "datasetDir": selected})
            except Exception as exc:
                self.json_response({"ok": False, "error": f"打开目录选择器失败: {exc}"}, status=500)

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

                report = inspect_sample_folder(resolve_user_path(dataset_dir, app_dir) if dataset_dir else "", color_dir, depth_dir)
                if report.get("valid"):
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
            payload = self.read_json()
            sample_id = str(payload.get("sampleId") or "").strip()
            try:
                capture_dir = create_offline_capture_dataset(app_dir, sample_id)
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
            dataset_dir = payload.get("datasetDir") or session.snapshot().get("analysisDataDir", "")
            if not dataset_dir:
                self.json_response({"ok": False, "error": "请先在形态分析页面加载当前样品数据。"}, status=400)
                return None
            color_dir = payload.get("colorDir") or None
            depth_dir = payload.get("depthDir") or None
            sample_id = str(payload.get("sampleId") or "").strip()
            try:
                from quality_prediction import build_sample_session

                sample_data, report = build_sample_session(
                    resolve_user_path(dataset_dir, app_dir),
                    sample_id=sample_id,
                    rgb_dir=color_dir,
                    spectral_dir=depth_dir,
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


def create_offline_capture_dataset(app_dir: Path, sample_id: str = "") -> Path:
    """Create a small current-capture folder for offline UI verification.

    Real camera integration should replace this function's image-writing block with
    camera frame saves, while keeping the returned sample root directory contract.
    """

    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe_sample = "".join(ch for ch in sample_id if ch.isalnum() or ch in "-_")[:24]
    folder_name = f"{stamp}_{safe_sample}" if safe_sample else stamp
    capture_root = app_dir.parent / "Data" / folder_name
    rgb_dir = capture_root / "rgb"
    spectral_dir = capture_root / "multispectral"
    rgb_dir.mkdir(parents=True, exist_ok=True)
    spectral_dir.mkdir(parents=True, exist_ok=True)

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

    expected = [rgb_dir / "rgb_001.png", spectral_dir / "450.png"]
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
