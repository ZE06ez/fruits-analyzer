import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from pointcloud_service import AnalysisError, analyze_rgbd_dataset, inspect_sample_folder, write_ply


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data" / "rgbd_sample_object"
POINTCLOUD_MODEL = ROOT / "sample_data" / "pointcloud_model"


class PointcloudServiceTests(unittest.TestCase):
    def make_rgb_multispectral_dataset(self, *, with_ply: bool = False) -> Path:
        dataset = Path(tempfile.mkdtemp(prefix="fta_rgb_multispectral_units_"))
        rgb = dataset / "rgb"
        spectral = dataset / "multispectral"
        rgb.mkdir()
        spectral.mkdir()

        image = Image.new("RGB", (80, 60), (18, 22, 28))
        for x in range(22, 58):
            for y in range(14, 46):
                image.putpixel((x, y), (70, 150, 58))
        image.save(rgb / "rgb_001.png")
        for band in (450, 560, 670):
            Image.new("L", (80, 60), 128).save(spectral / f"{band}.png")

        if with_ply:
            points = np.array(
                [
                    [0.0, 0.0, 10.0],
                    [12.0, 0.0, 12.0],
                    [0.0, 24.0, 16.0],
                    [12.0, 24.0, 32.0],
                ],
                dtype=np.float32,
            )
            colors = np.full((len(points), 3), [80, 160, 90], dtype=np.float32)
            write_ply(dataset / "reconstructed_sfm_fruit_color.ply", points, colors)

        return dataset

    def test_missing_dataset_reports_clear_error(self):
        with self.assertRaises(AnalysisError) as ctx:
            analyze_rgbd_dataset(ROOT / "missing-data", Path(tempfile.mkdtemp()))
        self.assertEqual(ctx.exception.code, "NO_DATASET")

    def test_rgb_multispectral_without_ply_reports_pixels_not_fake_mm(self):
        dataset = self.make_rgb_multispectral_dataset()
        out_dir = Path(tempfile.mkdtemp(prefix="fta_rgb_multispectral_units_out_"))

        result = analyze_rgbd_dataset(dataset, out_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["algorithm"], "rgb_multispectral_morphology")
        self.assertEqual(Path(result["multispectralDir"]), dataset / "multispectral")
        self.assertEqual(result["depthDir"], result["multispectralDir"])
        self.assertGreater(result["diameterPx"], 0)
        self.assertGreater(result["heightPx"], 0)
        self.assertIsNone(result["diameterMm"])
        self.assertIsNone(result["heightMm"])
        self.assertIsNone(result["volumeMethod"])
        self.assertFalse(result["volumeEstimated"])
        self.assertGreater(result["details"][0]["diameterPx"], 0)
        self.assertGreater(result["details"][0]["heightPx"], 0)

    def test_rgb_multispectral_with_ply_keeps_mm_and_pixel_fields(self):
        dataset = self.make_rgb_multispectral_dataset(with_ply=True)
        out_dir = Path(tempfile.mkdtemp(prefix="fta_rgb_multispectral_ply_units_out_"))

        result = analyze_rgbd_dataset(dataset, out_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["algorithm"], "rgb_multispectral_morphology")
        self.assertGreater(result["diameterPx"], 0)
        self.assertGreater(result["heightPx"], 0)
        self.assertEqual(result["diameterMm"], 24.0)
        self.assertEqual(result["heightMm"], 22.0)
        self.assertEqual(result["volumeMm3"], 32.0)
        self.assertEqual(result["volumeMethod"], "voxel_occupancy_estimate")
        self.assertTrue(result["volumeEstimated"])
        self.assertTrue(result["weightEstimated"])
        self.assertGreater(result["pointCount"], 0)

    def test_sample_dataset_runs_real_analysis(self):
        if not SAMPLE.exists():
            self.skipTest("local RGB sample dataset is not committed")
        out_dir = Path(tempfile.mkdtemp(prefix="fta_image_morphology_test_"))
        result = analyze_rgbd_dataset(SAMPLE, out_dir)
        self.assertTrue(result["ok"])
        self.assertEqual(result["algorithm"], "rgb_multispectral_morphology")
        self.assertGreater(result["diameterPx"], 0)
        self.assertGreater(result["heightPx"], 0)
        self.assertGreater(result["details"][0]["areaPixels"], 0)
        self.assertTrue((out_dir / "input_preview.png").exists())

    def test_cached_pointcloud_model_loads_for_numeric_metrics(self):
        if not POINTCLOUD_MODEL.exists():
            self.skipTest("local point-cloud sample model is not committed")
        out_dir = Path(tempfile.mkdtemp(prefix="fta_legacy_pointcloud_test_"))
        result = analyze_rgbd_dataset(POINTCLOUD_MODEL, out_dir)
        self.assertTrue(result["ok"])
        self.assertEqual(result["algorithm"], "legacy_cached_ply")
        self.assertGreater(result["pointCount"], 0)
        self.assertGreater(result["heightMm"], 0)
        self.assertEqual(result["plyUrl"], "")

    def test_chinese_and_space_paths_are_supported(self):
        if not SAMPLE.exists():
            self.skipTest("local RGB sample dataset is not committed")
        temp_root = Path(tempfile.mkdtemp(prefix="fruit data "))
        dataset = temp_root / "sample data"
        shutil.copytree(SAMPLE, dataset)
        out_dir = temp_root / "analysis output"
        result = analyze_rgbd_dataset(dataset, out_dir)
        self.assertTrue(result["ok"])
        self.assertTrue((out_dir / "input_preview.png").exists())

    def test_inspect_sample_folder_uses_enabled_bands_not_equal_counts(self):
        dataset = Path(tempfile.mkdtemp(prefix="fta_capture_check_"))
        rgb = dataset / "rgb"
        spectral = dataset / "multispectral"
        rgb.mkdir()
        spectral.mkdir()
        for index in range(2):
            Image.new("RGB", (12, 12), (90, 120, 60)).save(rgb / f"rgb_{index}.png")
        for band in (450, 560, 670):
            Image.new("L", (12, 12), 128).save(spectral / f"{band}.png")

        report = inspect_sample_folder(dataset, "rgb", "multispectral")

        self.assertTrue(report["valid"])
        self.assertTrue(report["complete"])
        self.assertEqual(report["rgbCount"], 2)
        self.assertEqual(report["spectralCount"], 3)
        self.assertEqual(report["expectedBands"], [450, 560, 670])
        self.assertEqual(report["missingBands"], [])

    def test_inspect_sample_folder_detects_missing_enabled_band(self):
        dataset = Path(tempfile.mkdtemp(prefix="fta_capture_missing_band_"))
        rgb = dataset / "rgb"
        spectral = dataset / "multispectral"
        rgb.mkdir()
        spectral.mkdir()
        Image.new("RGB", (12, 12), (90, 120, 60)).save(rgb / "rgb_001.png")
        Image.new("L", (12, 12), 128).save(spectral / "450.png")

        report = inspect_sample_folder(dataset, "rgb", "multispectral")

        self.assertTrue(report["valid"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["missingBands"], [560, 670])


if __name__ == "__main__":
    unittest.main()
