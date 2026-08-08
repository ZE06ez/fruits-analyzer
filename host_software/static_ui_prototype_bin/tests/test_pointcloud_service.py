import shutil
import tempfile
import unittest
from pathlib import Path

from pointcloud_service import AnalysisError, analyze_rgbd_dataset


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data" / "rgbd_sample_object"
POINTCLOUD_MODEL = ROOT / "sample_data" / "pointcloud_model"


class PointcloudServiceTests(unittest.TestCase):
    def test_missing_dataset_reports_clear_error(self):
        with self.assertRaises(AnalysisError) as ctx:
            analyze_rgbd_dataset(ROOT / "missing-data", Path(tempfile.mkdtemp()))
        self.assertEqual(ctx.exception.code, "NO_DATASET")

    def test_sample_dataset_runs_real_analysis(self):
        if not SAMPLE.exists():
            self.skipTest("local RGB sample dataset is not committed")
        out_dir = Path(tempfile.mkdtemp(prefix="fta_image_morphology_test_"))
        result = analyze_rgbd_dataset(SAMPLE, out_dir)
        self.assertTrue(result["ok"])
        self.assertEqual(result["algorithm"], "rgb_multispectral_morphology")
        self.assertGreater(result["diameterMm"], 0)
        self.assertGreater(result["heightMm"], 0)
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
        temp_root = Path(tempfile.mkdtemp(prefix="水果 数据 "))
        dataset = temp_root / "样品 数据"
        shutil.copytree(SAMPLE, dataset)
        out_dir = temp_root / "输出 结果"
        result = analyze_rgbd_dataset(dataset, out_dir)
        self.assertTrue(result["ok"])
        self.assertTrue((out_dir / "input_preview.png").exists())


if __name__ == "__main__":
    unittest.main()
