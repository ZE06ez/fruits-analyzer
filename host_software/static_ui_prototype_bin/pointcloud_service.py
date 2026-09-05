from __future__ import annotations

import math
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, UnidentifiedImageError


ProgressCallback = Callable[[str, int, str], None]


class AnalysisError(RuntimeError):
    """User-facing morphology analysis failure with a stable error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class CameraIntrinsics:
    fx: float = 652.77
    fy: float = 652.77
    cx: float = 631.75
    cy: float = 364.95


@dataclass
class AnalysisOptions:
    camera: CameraIntrinsics
    density_g_cm3: float = 1.08
    voxel_size_mm: float = 2.0
    max_pairs: int | None = 10


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def dependency_status() -> dict:
    status = {}
    for name in ("PIL", "numpy", "matplotlib", "scipy", "cv2", "open3d"):
        try:
            __import__(name)
            status[name] = True
        except Exception:
            status[name] = False
    return status


def default_sample_dataset(app_dir: Path) -> Path:
    return app_dir / "sample_data" / "sample_project"


def resolve_image_analysis_dirs(
    dataset_dir: Path,
    rgb_dir: str | None = None,
    spectral_dir: str | None = None,
) -> tuple[Path, Path | None]:
    dataset_dir = Path(dataset_dir).expanduser()
    if not dataset_dir.exists():
        raise AnalysisError("NO_DATASET", f"样品文件夹不存在: {dataset_dir}")

    if rgb_dir:
        rgb_path = resolve_child_path(dataset_dir, rgb_dir)
    else:
        rgb_path = _find_child_dir(dataset_dir, ("rgb", "color", "image", "images", "彩色图", "彩图"))

    if spectral_dir:
        spectral_path = resolve_child_path(dataset_dir, spectral_dir)
    else:
        spectral_path = _find_child_dir(dataset_dir, ("multispectral", "spectral", "narrowband", "mono", "gray", "ms", "多光谱", "窄带"))

    if rgb_path is None or not rgb_path.exists():
        raise AnalysisError("MISSING_RGB", "未找到 RGB 图像目录。请选择包含 rgb、color 或 image 子目录的样品文件夹。")
    return rgb_path, spectral_path if spectral_path and spectral_path.exists() else None


def resolve_child_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def resolve_dataset_dirs(dataset_dir: Path, color_dir: str | None = None, depth_dir: str | None = None) -> tuple[Path, Path]:
    dataset_dir = Path(dataset_dir).expanduser()
    if not dataset_dir.exists():
        raise AnalysisError("NO_DATASET", f"数据集目录不存在: {dataset_dir}")

    if color_dir:
        color_path = Path(color_dir).expanduser()
    else:
        color_path = _find_child_dir(dataset_dir, ("color", "rgb", "image", "images", "彩色图", "彩图"))

    if depth_dir:
        depth_path = Path(depth_dir).expanduser()
    else:
        depth_path = _find_child_dir(dataset_dir, ("depth", "depths", "深度图", "深度"))

    if color_path is None or not color_path.exists():
        raise AnalysisError("MISSING_RGB", "未找到 RGB/彩色图目录。请选择包含 color 或 image 子目录的数据集。")
    if depth_path is None or not depth_path.exists():
        raise AnalysisError("MISSING_DEPTH", "未找到深度图目录。请选择包含 depth 子目录的数据集。")
    return color_path, depth_path


def _find_child_dir(root: Path, names: tuple[str, ...]) -> Path | None:
    candidates = {name.lower() for name in names}
    for child in root.iterdir():
        if child.is_dir() and child.name.lower() in candidates:
            return child
    return None


def find_cached_ply(root: Path) -> Path | None:
    preferred = (
        "reconstructed_sfm_fruit_color.ply",
        "reconstructed_sfm_fruit.ply",
        "textured.ply",
        "strawberry.ply",
    )
    for name in preferred:
        candidate = root / name
        if candidate.exists() and candidate.is_file():
            return candidate
    ply_files = sorted(root.glob("*.ply"))
    return ply_files[0] if ply_files else None


def list_images(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS])


def inspect_sample_folder(
    dataset_dir: str | Path,
    rgb_dir: str | None = None,
    spectral_dir: str | None = None,
) -> dict:
    """Inspect an RGB + multispectral sample folder without running analysis."""

    modern_report = _inspect_sample_folder_by_enabled_bands(dataset_dir, rgb_dir, spectral_dir)
    if modern_report is not None:
        return modern_report

    raw_value = str(dataset_dir).strip()
    if not raw_value:
        root = Path("")
    else:
        root = Path(dataset_dir).expanduser()
    report = {
        "ok": True,
        "valid": False,
        "complete": False,
        "status": "empty",
        "datasetDir": str(root) if raw_value else "",
        "colorDir": "",
        "multispectralDir": "",
        "depthDir": "",
        "rgbCount": 0,
        "spectralCount": 0,
        "pairCount": 0,
        "missing": [],
        "badImages": [],
        "message": "",
    }
    if not raw_value:
        report["message"] = "请先选择样品文件夹。"
        return report
    if not root.exists():
        report["status"] = "missing"
        report["message"] = f"样品文件夹不存在: {root}"
        report["missing"].append("根目录不存在")
        return report
    if not root.is_dir():
        report["status"] = "invalid"
        report["message"] = f"样品路径不是文件夹: {root}"
        report["missing"].append("根路径不是文件夹")
        return report

    rgb_path = None
    spectral_path = None
    if rgb_dir:
        rgb_path = resolve_child_path(root, rgb_dir)
        if not rgb_path.exists():
            rgb_path = _find_child_dir(root, ("rgb", "color", "image", "images", "彩色图", "彩图"))
    else:
        rgb_path = _find_child_dir(root, ("rgb", "color", "image", "images", "彩色图", "彩图"))

    if spectral_dir:
        spectral_path = resolve_child_path(root, spectral_dir)
        if not spectral_path.exists():
            spectral_path = _find_child_dir(root, ("multispectral", "spectral", "narrowband", "mono", "gray", "ms", "多光谱", "窄带"))
    else:
        spectral_path = _find_child_dir(root, ("multispectral", "spectral", "narrowband", "mono", "gray", "ms", "多光谱", "窄带"))

    if rgb_path is None or not rgb_path.exists():
        report["missing"].append("缺少 RGB 图像目录")
    else:
        report["colorDir"] = str(rgb_path)
        rgb_files = list_images(rgb_path)
        report["rgbCount"] = len(rgb_files)
        if not rgb_files:
            report["missing"].append("RGB 图像目录没有可用图片")
        report["badImages"].extend(_bad_image_names(rgb_files, mode="RGB"))

    if spectral_path is None or not spectral_path.exists():
        report["missing"].append("缺少 multispectral 多光谱图像目录")
        spectral_files: list[Path] = []
    else:
        report["multispectralDir"] = str(spectral_path)
        report["depthDir"] = str(spectral_path)
        spectral_files = list_images(spectral_path)
        report["spectralCount"] = len(spectral_files)
        if not spectral_files:
            report["missing"].append("多光谱图像目录没有可用图片")
        report["badImages"].extend(_bad_image_names(spectral_files, mode="L"))

    rgb_count = int(report["rgbCount"])
    spectral_count = int(report["spectralCount"])
    report["pairCount"] = min(rgb_count, spectral_count)
    if rgb_count and spectral_count and rgb_count != spectral_count:
        report["missing"].append(f"RGB 与多光谱数量不一致，相差 {abs(rgb_count - spectral_count)} 张")
    if report["badImages"]:
        report["missing"].append("存在无法读取的图片")

    report["valid"] = rgb_count > 0 and spectral_count > 0 and not report["badImages"]
    report["complete"] = bool(report["valid"] and rgb_count == spectral_count)
    if report["complete"]:
        report["status"] = "complete"
        report["message"] = "数据目录有效"
    elif report["valid"]:
        report["status"] = "incomplete"
        report["message"] = "数据不完整"
    else:
        report["status"] = "invalid"
        report["message"] = "数据不完整"
    return report


def _inspect_sample_folder_by_enabled_bands(
    dataset_dir: str | Path,
    rgb_dir: str | None = None,
    spectral_dir: str | None = None,
) -> dict | None:
    """Modern sample validation: one sample, RGB images, and enabled bands."""

    raw_value = str(dataset_dir).strip()
    root = Path(dataset_dir).expanduser() if raw_value else Path("")
    report = {
        "ok": True,
        "valid": False,
        "complete": False,
        "status": "empty",
        "datasetDir": str(root) if raw_value else "",
        "colorDir": "",
        "multispectralDir": "",
        "depthDir": "",
        "rgbCount": 0,
        "spectralCount": 0,
        "pairCount": 0,
        "expectedBands": [],
        "availableBands": [],
        "missingBands": [],
        "unexpectedBands": [],
        "calibrationStatus": "missing",
        "missing": [],
        "badImages": [],
        "message": "",
    }
    if not raw_value:
        report["message"] = "Please select a sample folder first."
        return report
    if not root.exists():
        report["status"] = "missing"
        report["message"] = f"Sample folder does not exist: {root}"
        report["missing"].append("root directory missing")
        return report
    if not root.is_dir():
        report["status"] = "invalid"
        report["message"] = f"Sample path is not a folder: {root}"
        report["missing"].append("root path is not a folder")
        return report

    rgb_path = resolve_child_path(root, rgb_dir) if rgb_dir else _find_child_dir(root, ("rgb", "color", "image", "images"))
    spectral_path = (
        resolve_child_path(root, spectral_dir)
        if spectral_dir
        else _find_child_dir(root, ("multispectral", "spectral", "narrowband", "mono", "gray", "ms"))
    )

    rgb_name = rgb_path.name if rgb_path else (rgb_dir or "rgb")
    spectral_name = spectral_path.name if spectral_path else (spectral_dir or "multispectral")
    try:
        from quality_algorithm.spectral_features import inspect_sample_structure

        structure = inspect_sample_structure(root, rgb_dir=rgb_name, spectral_dir=spectral_name)
    except Exception as exc:
        report["status"] = "invalid"
        report["message"] = f"Data folder check failed: {exc}"
        report["missing"].append(str(exc))
        return report

    report.update(
        {
            "colorDir": str(rgb_path) if rgb_path and rgb_path.exists() else "",
            "multispectralDir": str(spectral_path) if spectral_path and spectral_path.exists() else "",
            "depthDir": str(spectral_path) if spectral_path and spectral_path.exists() else "",
            "rgbCount": int(structure["rgb_count"]),
            "spectralCount": int(structure["multispectral_count"]),
            "pairCount": min(int(structure["rgb_count"]), int(structure["multispectral_count"])),
            "expectedBands": structure["expected_bands"],
            "availableBands": structure["available_bands"],
            "missingBands": structure["missing_bands"],
            "unexpectedBands": structure["unexpected_bands"],
            "calibrationStatus": structure["calibration_status"],
            "badImages": structure["bad_images"],
            "valid": bool(structure["valid"]),
            "complete": bool(structure["complete"]),
        }
    )
    if not report["colorDir"]:
        report["missing"].append("missing RGB image directory")
    if not report["multispectralDir"]:
        report["missing"].append("missing multispectral image directory")
    if report["rgbCount"] <= 0:
        report["missing"].append("no RGB images")
    if report["spectralCount"] <= 0:
        report["missing"].append("no multispectral images")
    for band in report["missingBands"]:
        report["missing"].append(f"missing enabled multispectral band: {band} nm")
    if report["badImages"]:
        report["missing"].append("unreadable image files exist")

    if report["complete"]:
        report["status"] = "complete"
        report["message"] = "Data folder is valid."
    elif report["valid"]:
        report["status"] = "incomplete"
        report["message"] = "Data folder is valid but enabled bands are incomplete."
    else:
        report["status"] = "invalid"
        report["message"] = "Data folder is incomplete."
    return report


def _bad_image_names(files: list[Path], *, mode: str) -> list[str]:
    bad = []
    for path in files:
        try:
            with Image.open(path) as image:
                image.convert(mode).load()
        except Exception:
            bad.append(path.name)
    return bad


def ply_measurements(source_ply: Path, options: AnalysisOptions) -> dict | None:
    try:
        import pipeline_v2

        points, _colors = pipeline_v2.load_ply_points_colors(str(source_ply))
    except Exception:
        return None
    if points is None or points.size == 0:
        return None
    mins = points.min(axis=0)
    maxs = points.max(axis=0)
    ranges = maxs - mins
    volume_mm3 = pointcloud_volume(points, options.voxel_size_mm)
    return {
        "sourcePly": source_ply.name,
        "pointCount": int(len(points)),
        "averageDepthMm": round(float(np.median(points[:, 2])), 2),
        "diameterMm": round(float(max(ranges[0], ranges[1])), 2),
        "heightMm": round(float(ranges[2]), 2),
        "volumeMm3": round(float(volume_mm3), 2),
        "volumeMethod": "voxel_occupancy_estimate",
        "volumeEstimated": True,
        "weightG": round(float(volume_mm3 / 1000.0 * options.density_g_cm3), 2),
        "weightEstimated": True,
        "bboxXMm": round(float(ranges[0]), 2),
        "bboxYMm": round(float(ranges[1]), 2),
        "bboxZMm": round(float(ranges[2]), 2),
    }


def analyze_rgb_multispectral_sample(
    *,
    dataset_dir: Path,
    rgb_path: Path,
    spectral_path: Path | None,
    source_ply: Path | None,
    output_dir: Path,
    options: AnalysisOptions,
    dependencies: dict,
    started: float,
    emit: ProgressCallback,
) -> dict:
    emit("preprocess", 12, "读取 RGB 图像与窄带图像")
    rgb_files = list_images(rgb_path)
    if not rgb_files:
        raise AnalysisError("MISSING_RGB", f"RGB 目录没有可用图像: {rgb_path}")

    first_rgb = read_color_image(rgb_files[0])
    mask = build_rgb_subject_mask(first_rgb)
    if np.count_nonzero(mask) < 40:
        raise AnalysisError("EMPTY_MASK", "RGB 图像中未检测到有效样品区域。")

    emit("filter", 32, "分割样品区域并提取轮廓")
    preview_path = output_dir / "input_preview.png"
    save_input_preview(first_rgb, mask, preview_path)

    morphology = measure_rgb_frame(first_rgb, mask, rgb_files[0].name)

    emit("texture", 56, "分析果粉覆盖、颜色均匀度与纹理")
    texture = analyze_surface_texture(rgb_path, output_dir)
    color_stats = measure_color_statistics(first_rgb, mask)
    spectral_stats = analyze_spectral_folder(spectral_path) if spectral_path else empty_spectral_result("未提供窄带图像目录")

    ply_metrics = None
    if source_ply:
        emit("measure", 76, "读取可选点云模型并计算三维数值")
        ply_metrics = ply_measurements(source_ply, options)
    else:
        emit("measure", 76, "计算二维形态指标")

    area_px = morphology["areaPixels"]
    diameter_px = morphology["diameterPx"]
    height_px = morphology["heightPx"]
    diameter_mm = ply_metrics["diameterMm"] if ply_metrics else None
    height_mm = ply_metrics["heightMm"] if ply_metrics else None
    volume_mm3 = ply_metrics["volumeMm3"] if ply_metrics else 0.0
    weight_g = ply_metrics["weightG"] if ply_metrics else 0.0
    volume_method = ply_metrics["volumeMethod"] if ply_metrics else None
    volume_estimated = ply_metrics["volumeEstimated"] if ply_metrics else False
    weight_estimated = ply_metrics["weightEstimated"] if ply_metrics else False

    elapsed = time.perf_counter() - started
    emit("done", 100, "图像形态分析成功")
    return {
        "ok": True,
        "algorithm": "rgb_multispectral_morphology",
        "datasetDir": str(dataset_dir),
        "colorDir": str(rgb_path),
        "multispectralDir": str(spectral_path) if spectral_path else "",
        "depthDir": str(spectral_path) if spectral_path else "",
        "pairCount": len(rgb_files),
        "pointCount": ply_metrics["pointCount"] if ply_metrics else 0,
        "averageDepthMm": ply_metrics["averageDepthMm"] if ply_metrics else 0.0,
        "diameterMm": diameter_mm,
        "heightMm": height_mm,
        "diameterPx": round(diameter_px, 2),
        "heightPx": round(height_px, 2),
        "volumeMm3": volume_mm3,
        "volumeMethod": volume_method,
        "volumeEstimated": volume_estimated,
        "weightG": weight_g,
        "weightEstimated": weight_estimated,
        "densityGCm3": options.density_g_cm3,
        "voxelSizeMm": options.voxel_size_mm,
        "elapsedSec": round(elapsed, 2),
        "previewUrl": f"/outputs/{output_dir.name}/{preview_path.name}",
        "inputPreviewUrl": f"/outputs/{output_dir.name}/{preview_path.name}",
        "plyUrl": "",
        "texture": texture,
        "colorStats": color_stats,
        "spectralStats": spectral_stats,
        "details": [
            {
                "name": "rgb_morphology",
                "areaPixels": area_px,
                "diameterPx": round(diameter_px, 2),
                "heightPx": round(height_px, 2),
                "perimeterPx": round(morphology["perimeterPx"], 2),
                "rgbSource": rgb_files[0].name,
                "pointcloudModel": ply_metrics["sourcePly"] if ply_metrics else "",
            }
        ],
        "dependencies": dependencies,
    }


def analyze_rgbd_dataset(
    dataset_dir: str | Path,
    output_dir: str | Path,
    *,
    color_dir: str | None = None,
    depth_dir: str | None = None,
    options: AnalysisOptions | None = None,
    progress: ProgressCallback | None = None,
    cancel_flag: Callable[[], bool] | None = None,
) -> dict:
    """Analyze a local sample folder and return morphology/texture results.

    Input: a sample folder containing RGB images and optional multispectral images or
    a PLY point-cloud model. RGB-D folders are still accepted for compatibility, but
    this project uses RGB + narrowband cameras as the primary workflow.
    """

    started = time.perf_counter()
    opts = options or AnalysisOptions(camera=CameraIntrinsics())
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def emit(step: str, percent: int, message: str) -> None:
        if progress:
            progress(step, percent, message)
        if cancel_flag and cancel_flag():
            raise AnalysisError("CANCELLED", "用户取消任务。")

    emit("check", 3, "检查输入文件和运行环境")
    deps = dependency_status()
    if not deps["PIL"] or not deps["numpy"]:
        raise AnalysisError("MISSING_DEPENDENCY", "缺少 Pillow 或 NumPy，无法读取图像。")

    dataset_path = Path(dataset_dir).expanduser()
    cached_ply = find_cached_ply(dataset_path)

    try:
        rgb_path, spectral_path = resolve_image_analysis_dirs(dataset_path, color_dir, depth_dir)
        return analyze_rgb_multispectral_sample(
            dataset_dir=dataset_path,
            rgb_path=rgb_path,
            spectral_path=spectral_path,
            source_ply=cached_ply,
            output_dir=output_dir,
            options=opts,
            dependencies=deps,
            started=started,
            emit=emit,
        )
    except AnalysisError as exc:
        if exc.code not in {"MISSING_RGB", "NO_DATASET"}:
            raise

    if cached_ply and not color_dir and not depth_dir:
        return analyze_cached_pointcloud(
            dataset_dir=dataset_path,
            source_ply=cached_ply,
            output_dir=output_dir,
            options=opts,
            dependencies=deps,
            started=started,
            emit=emit,
        )

    color_path, depth_path = resolve_dataset_dirs(dataset_path, color_dir, depth_dir)
    color_files = list_images(color_path)
    depth_files = list_images(depth_path)
    if not color_files:
        raise AnalysisError("MISSING_RGB", f"彩色图目录没有可用图像: {color_path}")
    if not depth_files:
        raise AnalysisError("MISSING_DEPTH", f"深度图目录没有可用图像: {depth_path}")

    pair_count = min(len(color_files), len(depth_files))
    if opts.max_pairs:
        pair_count = min(pair_count, opts.max_pairs)
    if pair_count <= 0:
        raise AnalysisError("MISSING_PAIR", "RGB 图和深度图数量无法配对。")

    return analyze_with_pipeline_v2(
        dataset_dir=dataset_path,
        color_path=color_path,
        depth_path=depth_path,
        output_dir=output_dir,
        pair_count=pair_count,
        options=opts,
        dependencies=deps,
        started=started,
        emit=emit,
    )


def analyze_cached_pointcloud(
    *,
    dataset_dir: Path,
    source_ply: Path,
    output_dir: Path,
    options: AnalysisOptions,
    dependencies: dict,
    started: float,
    emit: ProgressCallback,
) -> dict:
    try:
        import pipeline_v2
    except Exception as exc:
        raise AnalysisError("MISSING_DEPENDENCY", f"点云模型加载模块失败: {exc}") from exc

    emit("preprocess", 15, "读取示例点云模型")
    points, colors = pipeline_v2.load_ply_points_colors(str(source_ply))
    if points is None or points.size == 0:
        raise AnalysisError("EMPTY_POINT_CLOUD", "示例点云模型为空。")
    if colors is None or len(colors) != len(points):
        colors = np.full((len(points), 3), [0, 180, 60], dtype=np.float32)

    mins = points.min(axis=0)
    maxs = points.max(axis=0)
    ranges = maxs - mins
    average_depth = float(np.median(points[:, 2]))
    diameter = float(max(ranges[0], ranges[1]))
    height = float(ranges[2])

    emit("measure", 78, "根据点云包围盒估算尺寸、体积和重量")
    volume_mm3 = pointcloud_volume(points, options.voxel_size_mm)
    weight_g = volume_mm3 / 1000.0 * options.density_g_cm3

    emit("texture", 86, "检查表面纹理分析输入")
    texture = empty_texture_result("示例点云模型不包含原始 RGB 图片")

    elapsed = time.perf_counter() - started
    emit("done", 100, "示例点云模型加载成功")
    return {
        "ok": True,
        "algorithm": "legacy_cached_ply",
        "datasetDir": str(dataset_dir),
        "colorDir": "",
        "depthDir": "",
        "pairCount": 0,
        "pointCount": int(len(points)),
        "averageDepthMm": round(average_depth, 2),
        "diameterMm": round(diameter, 2),
        "heightMm": round(height, 2),
        "volumeMm3": round(float(volume_mm3), 2),
        "volumeMethod": "voxel_occupancy_estimate",
        "volumeEstimated": True,
        "weightG": round(float(weight_g), 2),
        "weightEstimated": True,
        "densityGCm3": options.density_g_cm3,
        "voxelSizeMm": options.voxel_size_mm,
        "elapsedSec": round(elapsed, 2),
        "previewUrl": "",
        "inputPreviewUrl": "",
        "plyUrl": "",
        "texture": texture,
        "details": [
            {
                "name": "legacy_cached_ply",
                "bboxXMm": round(float(ranges[0]), 2),
                "bboxYMm": round(float(ranges[1]), 2),
                "bboxZMm": round(float(ranges[2]), 2),
                "sourcePly": source_ply.name,
            }
        ],
        "dependencies": dependencies,
    }


def analyze_with_pipeline_v2(
    *,
    dataset_dir: Path,
    color_path: Path,
    depth_path: Path,
    output_dir: Path,
    pair_count: int,
    options: AnalysisOptions,
    dependencies: dict,
    started: float,
    emit: ProgressCallback,
) -> dict:
    if not dependencies.get("cv2"):
        raise AnalysisError("MISSING_DEPENDENCY", "点云重建需要 OpenCV/cv2，请先安装 opencv-python 后再打包。")

    try:
        import pipeline_v2
    except Exception as exc:
        raise AnalysisError("MISSING_DEPENDENCY", f"点云重建模块加载失败: {exc}") from exc

    emit("preprocess", 10, f"读取并配对 {pair_count} 组 RGB-D 图像")
    emit("reconstruct", 35, "SFM 位姿估计与点云重建")
    camera = adjusted_camera_for_dataset(color_path, options.camera)
    try:
        ply_plain, ply_color = pipeline_v2.reconstruct_sfm(
            str(color_path),
            str(depth_path),
            str(output_dir),
            camera.fx,
            camera.fy,
            camera.cx,
            camera.cy,
            max_pairs=pair_count,
        )
    except Exception as exc:
        raise AnalysisError("ALGORITHM_FAILED", f"点云重建执行失败: {exc}") from exc

    emit("filter", 62, "掩膜清理、深度补洞与异常点处理")
    ply_path = Path(ply_color if ply_color and Path(ply_color).exists() else ply_plain)
    points, colors = pipeline_v2.load_ply_points_colors(str(ply_path))
    if points is None or points.size == 0:
        raise AnalysisError("EMPTY_POINT_CLOUD", "点云重建结果为空。")
    if colors is None or len(colors) != len(points):
        colors = np.full((len(points), 3), [96, 210, 255], dtype=np.float32)

    emit("fusion", 72, "点云融合完成，生成可旋转 PLY")
    output_ply = output_dir / "reconstructed_sfm_fruit_color.ply"
    if ply_path.resolve() != output_ply.resolve():
        output_ply.write_text(ply_path.read_text(encoding="utf-8"), encoding="utf-8")

    mins = points.min(axis=0)
    maxs = points.max(axis=0)
    ranges = maxs - mins
    average_depth = float(np.median(points[:, 2]))
    diameter = float(max(ranges[0], ranges[1]))
    height = float(ranges[2])

    emit("measure", 82, "结果测量、体积与重量估算")
    volume_mm3 = pipeline_v2.pointcloud_volume(points, voxel_size=options.voxel_size_mm)
    weight_g = volume_mm3 / 1000.0 * options.density_g_cm3

    emit("texture", 86, "分析果粉覆盖与颜色均匀度")
    texture = analyze_surface_texture(color_path, output_dir)

    emit("preview", 90, "生成点云预览图")
    preview_path = output_dir / "pointcloud_preview.png"
    save_pointcloud_preview(points.astype(np.float32), colors.astype(np.float32), preview_path)
    input_preview = output_dir / "input_preview.png"
    first_rgb = next(iter(list_images(color_path)), None)
    if first_rgb:
        rgb = read_color_image(first_rgb)
        save_input_preview(rgb, np.zeros(rgb.shape[:2], dtype=bool), input_preview)

    elapsed = time.perf_counter() - started
    emit("done", 100, "点云形态分析成功")
    return {
        "ok": True,
        "algorithm": "pipeline_v2",
        "datasetDir": str(dataset_dir),
        "colorDir": str(color_path),
        "depthDir": str(depth_path),
        "pairCount": pair_count,
        "pointCount": int(len(points)),
        "averageDepthMm": round(average_depth, 2),
        "diameterMm": round(diameter, 2),
        "heightMm": round(height, 2),
        "volumeMm3": round(float(volume_mm3), 2),
        "volumeMethod": "voxel_occupancy_estimate",
        "volumeEstimated": True,
        "weightG": round(float(weight_g), 2),
        "weightEstimated": True,
        "densityGCm3": options.density_g_cm3,
        "voxelSizeMm": options.voxel_size_mm,
        "elapsedSec": round(elapsed, 2),
        "previewUrl": f"/outputs/{output_dir.name}/{preview_path.name}",
        "inputPreviewUrl": f"/outputs/{output_dir.name}/{input_preview.name}" if input_preview.exists() else "",
        "plyUrl": f"/outputs/{output_dir.name}/{output_ply.name}",
        "texture": texture,
        "details": [
            {
                "name": "pipeline_v2",
                "bboxXMm": round(float(ranges[0]), 2),
                "bboxYMm": round(float(ranges[1]), 2),
                "bboxZMm": round(float(ranges[2]), 2),
                "cameraCx": round(float(camera.cx), 2),
                "cameraCy": round(float(camera.cy), 2),
                "sourcePly": output_ply.name,
            }
        ],
        "dependencies": dependencies,
    }


def adjusted_camera_for_dataset(color_path: Path, camera: CameraIntrinsics) -> CameraIntrinsics:
    files = list_images(color_path)
    if not files:
        return camera
    try:
        with Image.open(files[0]) as image:
            width, height = image.size
    except Exception:
        return camera
    cx = camera.cx
    cy = camera.cy
    if cx < 0 or cx >= width:
        cx = (width - 1) / 2.0
    if cy < 0 or cy >= height:
        cy = (height - 1) / 2.0
    return CameraIntrinsics(fx=camera.fx, fy=camera.fy, cx=cx, cy=cy)


def read_color_image(path: Path) -> np.ndarray:
    try:
        with Image.open(path) as image:
            return np.asarray(image.convert("RGB"), dtype=np.uint8)
    except (OSError, UnidentifiedImageError) as exc:
        raise AnalysisError("BAD_IMAGE", f"无法读取彩色图: {path.name}") from exc


def read_depth_image(path: Path) -> np.ndarray:
    try:
        with Image.open(path) as image:
            arr = np.asarray(image)
    except (OSError, UnidentifiedImageError) as exc:
        raise AnalysisError("BAD_IMAGE", f"无法读取深度图: {path.name}") from exc
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    return arr.astype(np.float32)


def resize_color_to_depth(rgb: np.ndarray, depth_shape: tuple[int, int]) -> np.ndarray:
    image = Image.fromarray(rgb)
    resized = image.resize((depth_shape[1], depth_shape[0]), Image.Resampling.BILINEAR)
    return np.asarray(resized, dtype=np.uint8)


def build_fruit_mask(rgb: np.ndarray, depth: np.ndarray) -> np.ndarray:
    valid = np.isfinite(depth) & (depth > 0)
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    chroma = maxc - minc
    green = (chroma > 28) & (g > 55) & (g > r * 1.04) & (g > b * 1.12)
    yellow_green = (chroma > 35) & (g > 65) & (r > 45) & (b < 145) & (g >= r * 0.75)
    red = (chroma > 30) & (r > 70) & (r > g * 1.12) & (r > b * 1.18)
    color_mask = (green | yellow_green | red) & valid

    if np.count_nonzero(color_mask) < 80:
        vals = depth[valid]
        if vals.size == 0:
            return np.zeros_like(depth, dtype=bool)
        low, high = np.percentile(vals, [2, 20])
        yy, xx = np.indices(depth.shape)
        cy, cx = np.array(depth.shape) / 2.0
        center_roi = ((yy - cy) / (depth.shape[0] * 0.36)) ** 2 + ((xx - cx) / (depth.shape[1] * 0.32)) ** 2 <= 1.0
        color_mask = valid & center_roi & (depth >= low) & (depth <= high)

    color_mask = binary_opening(color_mask, radius=1, iterations=1)
    color_mask = binary_closing(color_mask, radius=2, iterations=2)
    return keep_center_component(color_mask).astype(bool)


def clean_depth(depth: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    depth_clean = depth.astype(np.float32).copy()
    valid_vals = depth_clean[mask & (depth_clean > 0)]
    if valid_vals.size == 0:
        return depth_clean, np.zeros_like(mask, dtype=bool)
    p5, p95 = np.percentile(valid_vals, [5, 95])
    mask_clean = mask & (depth_clean >= p5) & (depth_clean <= p95)
    depth_clean = median_filter_3x3(depth_clean)
    mask_clean = binary_opening(mask_clean, radius=1, iterations=1)
    depth_clean[~mask_clean] = 0
    return depth_clean, mask_clean.astype(bool)


def binary_opening(mask: np.ndarray, *, radius: int, iterations: int) -> np.ndarray:
    result = mask.astype(bool)
    for _ in range(iterations):
        result = binary_erode(result, radius)
    for _ in range(iterations):
        result = binary_dilate(result, radius)
    return result


def binary_closing(mask: np.ndarray, *, radius: int, iterations: int) -> np.ndarray:
    result = mask.astype(bool)
    for _ in range(iterations):
        result = binary_dilate(result, radius)
    for _ in range(iterations):
        result = binary_erode(result, radius)
    return result


def binary_erode(mask: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(mask.astype(bool), radius, mode="constant", constant_values=False)
    result = np.ones(mask.shape, dtype=bool)
    size = radius * 2 + 1
    for dy in range(size):
        for dx in range(size):
            result &= padded[dy : dy + mask.shape[0], dx : dx + mask.shape[1]]
    return result


def binary_dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(mask.astype(bool), radius, mode="constant", constant_values=False)
    result = np.zeros(mask.shape, dtype=bool)
    size = radius * 2 + 1
    for dy in range(size):
        for dx in range(size):
            result |= padded[dy : dy + mask.shape[0], dx : dx + mask.shape[1]]
    return result


def keep_center_component(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.where(mask)
    if len(xs) < 25:
        return mask.astype(bool)

    height, width = mask.shape
    image_area = mask.size
    center_y = (height - 1) / 2.0
    center_x = (width - 1) / 2.0
    pending = set(zip(ys.tolist(), xs.tolist()))
    best_pixels: list[tuple[int, int]] = []
    best_score = -1.0

    while pending:
        start = pending.pop()
        stack = [start]
        pixels = [start]
        sum_y = float(start[0])
        sum_x = float(start[1])
        while stack:
            y, x = stack.pop()
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if (ny, nx) in pending:
                    pending.remove((ny, nx))
                    stack.append((ny, nx))
                    pixels.append((ny, nx))
                    sum_y += ny
                    sum_x += nx

        area = len(pixels)
        if area < 25:
            continue
        centroid_y = sum_y / area
        centroid_x = sum_x / area
        dist = math.hypot(centroid_y - center_y, centroid_x - center_x)
        size_penalty = 0.25 if area > image_area * 0.18 else 1.0
        score = area * size_penalty / (dist + 1.0)
        if score > best_score:
            best_score = score
            best_pixels = pixels

    if not best_pixels:
        return mask.astype(bool)

    result = np.zeros(mask.shape, dtype=bool)
    py, px = zip(*best_pixels)
    result[np.array(py), np.array(px)] = True
    return result


def median_filter_3x3(values: np.ndarray) -> np.ndarray:
    padded = np.pad(values, 1, mode="edge")
    windows = [
        padded[dy : dy + values.shape[0], dx : dx + values.shape[1]]
        for dy in range(3)
        for dx in range(3)
    ]
    return np.median(np.stack(windows, axis=0), axis=0).astype(values.dtype)


def depth_to_points(
    depth: np.ndarray,
    rgb: np.ndarray,
    mask: np.ndarray,
    camera: CameraIntrinsics,
    *,
    stride: int = 2,
) -> tuple[np.ndarray, np.ndarray]:
    sampled = mask.copy()
    if stride > 1:
        sparse = np.zeros_like(sampled, dtype=bool)
        sparse[::stride, ::stride] = True
        sampled &= sparse
    v, u = np.where(sampled & (depth > 0))
    if len(u) == 0:
        return np.empty((0, 3), dtype=np.float32), np.empty((0, 3), dtype=np.uint8)
    z = depth[v, u].astype(np.float32)
    x = (u.astype(np.float32) - camera.cx) * z / camera.fx
    y = (v.astype(np.float32) - camera.cy) * z / camera.fy
    points = np.column_stack([x, y, z]).astype(np.float32)
    colors = rgb[v, u].astype(np.uint8)
    return points, colors


def align_pointclouds(pointclouds: list[np.ndarray]) -> list[np.ndarray]:
    if not pointclouds:
        return []
    reference = pointclouds[0]
    ref_center = reference.mean(axis=0)
    aligned = [reference]
    for points in pointclouds[1:]:
        if points.size == 0:
            continue
        aligned.append(points + (ref_center - points.mean(axis=0)))
    return aligned


def remove_point_outliers(points: np.ndarray, colors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if len(points) < 32:
        return points, colors
    center = np.median(points, axis=0)
    d = np.linalg.norm(points - center, axis=1)
    keep = d <= np.percentile(d, 97)
    return points[keep], colors[keep]


def voxel_downsample(points: np.ndarray, colors: np.ndarray, voxel_size: float) -> tuple[np.ndarray, np.ndarray]:
    if points.size == 0:
        return points, colors
    coords = np.floor(points / voxel_size).astype(np.int64)
    _, idx = np.unique(coords, axis=0, return_index=True)
    return points[idx], colors[idx]


def pointcloud_volume(points: np.ndarray, voxel_size: float) -> float:
    """Estimate volume from occupied voxel count, not a watertight mesh."""
    if points.size == 0:
        return 0.0
    coords = np.floor(points / voxel_size).astype(np.int64)
    return float(len(np.unique(coords, axis=0))) * (voxel_size**3)


def measure_depth_frame(depth: np.ndarray, mask: np.ndarray, camera: CameraIntrinsics, name: str) -> dict:
    ys, xs = np.where(mask & (depth > 0))
    if len(xs) == 0:
        raise AnalysisError("EMPTY_MASK", f"{name} 无有效深度区域。")
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    patch = depth[max(y0, (y0 + y1) // 2 - 2) : min(depth.shape[0], (y0 + y1) // 2 + 3),
                  max(x0, (x0 + x1) // 2 - 2) : min(depth.shape[1], (x0 + x1) // 2 + 3)]
    patch_vals = patch[np.isfinite(patch) & (patch > 0)]
    z = float(np.median(patch_vals)) if patch_vals.size else float(np.median(depth[ys, xs]))
    w = float(x1 - x0 + 1)
    h = float(y1 - y0 + 1)
    return {
        "name": name,
        "depthMm": z,
        "diameterMm": w * z / camera.fx,
        "heightMm": h * z / camera.fy,
        "maskPixels": int(len(xs)),
    }


def average_measurements(rows: list[dict]) -> dict:
    if not rows:
        raise AnalysisError("NO_MEASUREMENTS", "没有可汇总的形态测量结果。")
    return {
        "depth": float(np.mean([row["depthMm"] for row in rows])),
        "diameter": float(np.mean([row["diameterMm"] for row in rows])),
        "height": float(np.mean([row["heightMm"] for row in rows])),
    }


def measure_rgb_frame(rgb: np.ndarray, mask: np.ndarray, name: str) -> dict:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        raise AnalysisError("EMPTY_MASK", f"{name} 无有效样品区域。")
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    return {
        "name": name,
        "areaPixels": int(len(xs)),
        "diameterPx": float(x1 - x0 + 1),
        "heightPx": float(y1 - y0 + 1),
        "perimeterPx": estimate_mask_perimeter(mask),
    }


def estimate_mask_perimeter(mask: np.ndarray) -> float:
    up = np.zeros_like(mask, dtype=bool)
    up[1:] = mask[:-1]
    down = np.zeros_like(mask, dtype=bool)
    down[:-1] = mask[1:]
    left = np.zeros_like(mask, dtype=bool)
    left[:, 1:] = mask[:, :-1]
    right = np.zeros_like(mask, dtype=bool)
    right[:, :-1] = mask[:, 1:]
    edge = mask & ~(up & down & left & right)
    return float(np.count_nonzero(edge))


def measure_color_statistics(rgb: np.ndarray, mask: np.ndarray) -> dict:
    pixels = rgb[mask].astype(np.float32)
    if pixels.size == 0:
        return {"ok": False}
    means = pixels.mean(axis=0)
    stds = pixels.std(axis=0)
    maxc = pixels.max(axis=1)
    minc = pixels.min(axis=1)
    saturation = (maxc - minc) / np.maximum(maxc, 1.0)
    return {
        "ok": True,
        "meanR": round(float(means[0]), 2),
        "meanG": round(float(means[1]), 2),
        "meanB": round(float(means[2]), 2),
        "stdR": round(float(stds[0]), 2),
        "stdG": round(float(stds[1]), 2),
        "stdB": round(float(stds[2]), 2),
        "meanSaturation": round(float(np.mean(saturation)), 4),
    }


def empty_spectral_result(message: str) -> dict:
    return {"ok": False, "message": message, "bandCount": 0, "meanIntensity": None}


def analyze_spectral_folder(spectral_path: Path | None) -> dict:
    if not spectral_path:
        return empty_spectral_result("未提供窄带图像目录")
    files = list_images(spectral_path)
    if not files:
        return empty_spectral_result("窄带图像目录中没有可用图片")
    means = []
    for path in files[:32]:
        try:
            with Image.open(path) as image:
                arr = np.asarray(image.convert("L"), dtype=np.float32)
            means.append(float(np.mean(arr)))
        except Exception:
            continue
    if not means:
        return empty_spectral_result("窄带图像无法读取")
    return {
        "ok": True,
        "message": "窄带图像读取成功",
        "bandCount": len(files),
        "meanIntensity": round(float(np.mean(means)), 2),
    }


def write_ply(path: Path, points: np.ndarray, colors: np.ndarray | None = None) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write(f"element vertex {len(points)}\n")
        f.write("property float x\nproperty float y\nproperty float z\n")
        if colors is not None:
            f.write("property uchar red\nproperty uchar green\nproperty uchar blue\n")
        f.write("end_header\n")
        for index, point in enumerate(points):
            if colors is None:
                f.write(f"{point[0]} {point[1]} {point[2]}\n")
            else:
                r, g, b = colors[index]
                f.write(f"{point[0]} {point[1]} {point[2]} {int(r)} {int(g)} {int(b)}\n")


def save_pointcloud_preview(points: np.ndarray, colors: np.ndarray, path: Path) -> None:
    max_points = min(len(points), 20000)
    if len(points) > max_points:
        idx = np.linspace(0, len(points) - 1, max_points).astype(np.int64)
        pts = points[idx]
    else:
        pts = points

    if pts.size == 0:
        Image.fromarray(np.full((520, 720, 3), 255, dtype=np.uint8)).save(path)
        return

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(5.6, 4.2), dpi=150)
        ax = fig.add_subplot(111, projection="3d")
        z = pts[:, 2]
        z_low, z_high = np.percentile(z, [2, 98])
        denom = max(float(z_high - z_low), 1.0)
        depth = np.clip((z - z_low) / denom, 0, 1)
        green_colors = np.column_stack([
            0.03 + depth * 0.10,
            0.18 + depth * 0.75,
            0.06 + depth * 0.10,
        ])
        ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], s=0.5, c=green_colors)
        ax.set_axis_off()
        ax.view_init(elev=20, azim=45)
        try:
            ax.set_box_aspect((1.0, 1.0, 1.25))
        except Exception:
            pass
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        plt.tight_layout(pad=0)
        fig.savefig(path, bbox_inches="tight", pad_inches=0, facecolor="white")
        plt.close(fig)
        return
    except Exception:
        pass

    canvas_w, canvas_h = 720, 520
    canvas = np.full((canvas_h, canvas_w, 3), 255, dtype=np.uint8)

    x = pts[:, 0]
    y = pts[:, 1]
    z = pts[:, 2]
    px_src = x + z * 0.08
    py_src = y - z * 0.04

    def normalize(values: np.ndarray, size: int, pad: int) -> np.ndarray:
        low, high = np.percentile(values, [1, 99])
        if not math.isfinite(low) or not math.isfinite(high) or abs(high - low) < 1e-6:
            low, high = float(values.min()), float(values.max() + 1.0)
        scaled = (values - low) / (high - low)
        scaled = np.clip(scaled, 0, 1)
        return (scaled * (size - pad * 2) + pad).astype(np.int32)

    px = normalize(px_src, canvas_w, 44)
    py = normalize(py_src, canvas_h, 44)
    py = canvas_h - py
    order = np.argsort(z)
    z_low, z_high = np.percentile(z, [2, 98])
    z_range = max(float(z_high - z_low), 1.0)
    for index in order:
        x0 = px[index]
        y0 = py[index]
        if 1 <= x0 < canvas_w - 1 and 1 <= y0 < canvas_h - 1:
            depth_mix = float(np.clip((z[index] - z_low) / z_range, 0, 1))
            color = np.array([
                int(8 + depth_mix * 25),
                int(45 + depth_mix * 190),
                int(14 + depth_mix * 28),
            ], dtype=np.uint8)
            canvas[y0 - 1 : y0 + 2, x0 - 1 : x0 + 2] = color
    Image.fromarray(canvas).save(path)


def save_input_preview(rgb: np.ndarray, mask: np.ndarray, path: Path) -> None:
    overlay = rgb.copy()
    overlay[mask] = (overlay[mask] * 0.55 + np.array([134, 239, 172]) * 0.45).astype(np.uint8)
    Image.fromarray(overlay).save(path)


def empty_texture_result(message: str) -> dict:
    return {
        "ok": False,
        "message": message,
        "bloomCoveragePercent": None,
        "colorUniformity": None,
        "previewUrl": "",
    }


def analyze_surface_texture(color_path: Path, output_dir: Path) -> dict:
    files = list_images(color_path)
    if not files:
        return empty_texture_result("未找到可用于表面纹理分析的 RGB 图片")

    coverages: list[float] = []
    uniformities: list[float] = []
    preview_written = False
    preview_path = output_dir / "texture_preview.png"

    for image_path in files[: min(8, len(files))]:
        rgb = read_color_image(image_path)
        mask = build_rgb_subject_mask(rgb)
        if np.count_nonzero(mask) < 40:
            continue

        rgb_f = rgb.astype(np.float32)
        maxc = rgb_f.max(axis=2)
        minc = rgb_f.min(axis=2)
        chroma = maxc - minc
        saturation = chroma / np.maximum(maxc, 1.0)
        brightness = maxc
        bloom = mask & (saturation < 0.30) & (brightness > 72) & (brightness < 245)

        coverages.append(float(np.count_nonzero(bloom) / np.count_nonzero(mask) * 100.0))
        gray = rgb_f.mean(axis=2)
        subject_gray = gray[mask]
        uniformity = 100.0 - float(np.std(subject_gray) / max(np.mean(subject_gray), 1.0) * 100.0)
        uniformities.append(float(np.clip(uniformity, 0, 100)))

        if not preview_written:
            overlay = rgb.copy()
            overlay[mask] = (overlay[mask] * 0.72 + np.array([76, 175, 80]) * 0.28).astype(np.uint8)
            overlay[bloom] = (overlay[bloom] * 0.35 + np.array([226, 232, 240]) * 0.65).astype(np.uint8)
            Image.fromarray(overlay).save(preview_path)
            preview_written = True

    if not coverages:
        return empty_texture_result("RGB 图片中未检测到有效样品区域")

    if not preview_written:
        first = read_color_image(files[0])
        Image.fromarray(first).save(preview_path)

    return {
        "ok": True,
        "message": "表面纹理分析完成",
        "bloomCoveragePercent": round(float(np.mean(coverages)), 2),
        "colorUniformity": round(float(np.mean(uniformities)), 1),
        "previewUrl": f"/outputs/{output_dir.name}/{preview_path.name}",
    }


def build_rgb_subject_mask(rgb: np.ndarray) -> np.ndarray:
    rgb_f = rgb.astype(np.float32)
    r = rgb_f[:, :, 0]
    g = rgb_f[:, :, 1]
    b = rgb_f[:, :, 2]
    maxc = rgb_f.max(axis=2)
    minc = rgb_f.min(axis=2)
    chroma = maxc - minc
    color_subject = (
        ((g > 45) & (g > r * 0.72) & (g > b * 0.82))
        | ((r > 55) & (g > 45) & (b < 180))
        | (chroma > 24)
    ) & (maxc > 35)

    if np.count_nonzero(color_subject) < 60:
        yy, xx = np.indices(rgb.shape[:2])
        cy, cx = np.array(rgb.shape[:2]) / 2.0
        color_subject = ((yy - cy) / (rgb.shape[0] * 0.38)) ** 2 + ((xx - cx) / (rgb.shape[1] * 0.34)) ** 2 <= 1.0

    color_subject = binary_opening(color_subject, radius=1, iterations=1)
    color_subject = binary_closing(color_subject, radius=2, iterations=2)
    return keep_center_component(color_subject).astype(bool)


def copy_tree_replace(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
