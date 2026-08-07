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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def default_sample_dataset(app_dir: Path) -> Path:
    return app_dir / "sample_data" / "legacy_pointcloud_model"


def builtin_sample_datasets(app_dir: Path) -> dict:
    return {
        "legacyPointcloud": {
            "label": "示例点云模型",
            "path": str(app_dir / "sample_data" / "legacy_pointcloud_model"),
        },
        "sampleObject": {
            "label": "样品对象数据集",
            "path": str(app_dir / "sample_data" / "rgbd_sample_object"),
        },
        "rawScene": {
            "label": "原始 RGB-D 场景",
            "path": str(app_dir / "sample_data" / "rgbd_grape"),
        },
    }


def create_handler(static_dir: Path, outputs_dir: Path, app_dir: Path, store: JobStore):
    static_dir = static_dir.resolve()
    outputs_dir = outputs_dir.resolve()
    app_dir = app_dir.resolve()

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
                self.json_response({
                    "ok": True,
                    "dependencies": dependencies,
                    "sampleDataset": str(default_sample_dataset(app_dir)),
                    "sampleDatasets": builtin_sample_datasets(app_dir),
                })
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
            if parsed.path == "/api/analyze-shape":
                self.handle_analyze_shape()
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
                selected = filedialog.askdirectory(title="选择 RGB-D 数据集目录")
                root.destroy()
                if not selected:
                    self.json_response({"ok": False, "cancelled": True, "error": "用户取消选择"})
                    return
                self.json_response({"ok": True, "datasetDir": selected})
            except Exception as exc:
                self.json_response({"ok": False, "error": f"打开目录选择器失败: {exc}"}, status=500)

        def handle_dataset_images(self, query: str) -> None:
            params = parse_qs(query)
            dataset_dir = params.get("datasetDir", [""])[0] or str(default_sample_dataset(app_dir))
            color_dir = params.get("colorDir", [""])[0] or None
            depth_dir = params.get("depthDir", [""])[0] or None
            try:
                from pointcloud_service import AnalysisError, list_images, resolve_dataset_dirs

                color_path, depth_path = resolve_dataset_dirs(resolve_user_path(dataset_dir, app_dir), color_dir, depth_dir)
                color_files = list_images(color_path)
                depth_files = list_images(depth_path)
                pair_count = min(len(color_files), len(depth_files), 60)

                def row(path: Path) -> dict:
                    return {
                        "name": path.name,
                        "url": f"/api/local-image?path={quote(str(path), safe='')}",
                    }

                self.json_response({
                    "ok": True,
                    "colorDir": str(color_path),
                    "depthDir": str(depth_path),
                    "images": [
                        {"index": index, "color": row(color_files[index]), "depth": row(depth_files[index])}
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
                self.json_response({"ok": False, "error": "请选择 RGB-D 数据集文件夹。"}, status=400)
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
                self.json_response({"ok": True, "datasetDir": str(dataset_dir), "fileCount": saved})
            except Exception as exc:
                self.json_response({"ok": False, "error": f"上传数据集失败: {exc}"}, status=500)

        def handle_analyze_shape(self) -> None:
            payload = self.read_json()
            dataset_dir = payload.get("datasetDir") or str(default_sample_dataset(app_dir))
            color_dir = payload.get("colorDir") or None
            depth_dir = payload.get("depthDir") or None
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


def start_backend(static_dir: Path, outputs_dir: Path, app_dir: Path, port: int | None = None) -> tuple[ThreadingHTTPServer, int]:
    store = JobStore()
    outputs_dir.mkdir(parents=True, exist_ok=True)
    selected_port = port or free_port()
    handler = create_handler(static_dir, outputs_dir, app_dir, store)
    server = ThreadingHTTPServer(("127.0.0.1", selected_port), handler)
    setattr(server, "should_exit", False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, selected_port
