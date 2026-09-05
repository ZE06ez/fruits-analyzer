from __future__ import annotations

import json
import unittest

import numpy as np

from camera_service import CameraFrame
from camera_service.focus_quality import FocusEvaluator, FocusRoi, FocusThresholdConfig


def _checkerboard(size: int = 64, block: int = 4, dtype=np.uint8) -> np.ndarray:
    grid = np.indices((size, size)).sum(axis=0) // block
    high = np.iinfo(dtype).max
    return ((grid % 2) * high).astype(dtype)


def _box_blur(image: np.ndarray, passes: int = 4) -> np.ndarray:
    result = image.astype(np.float32)
    for _ in range(passes):
        padded = np.pad(result, 1, mode="edge")
        result = (
            padded[:-2, :-2] + padded[:-2, 1:-1] + padded[:-2, 2:]
            + padded[1:-1, :-2] + padded[1:-1, 1:-1] + padded[1:-1, 2:]
            + padded[2:, :-2] + padded[2:, 1:-1] + padded[2:, 2:]
        ) / 9.0
    return np.clip(result, 0, np.iinfo(image.dtype).max).astype(image.dtype)


class FakeNanCv2:
    CV_32F = 5

    @staticmethod
    def Sobel(sample, depth, dx, dy, ksize=3):
        return np.full(sample.shape, np.nan, dtype=np.float32)

    @staticmethod
    def Laplacian(sample, depth, ksize=3):
        return np.full(sample.shape, np.nan, dtype=np.float32)


class FocusQualityTests(unittest.TestCase):
    def test_uint8_sharp_frame_scores_higher_than_blurred_frame(self):
        evaluator = FocusEvaluator()
        sharp = _checkerboard(dtype=np.uint8)
        blurred = _box_blur(sharp)

        sharp_result = evaluator.evaluate(CameraFrame(sharp, "MONO", "uint8", sharp.shape))
        blurred_result = evaluator.evaluate(CameraFrame(blurred, "MONO", "uint8", blurred.shape))

        self.assertEqual(sharp_result.status, "ok")
        self.assertEqual(sharp_result.classification, "unknown")
        self.assertGreater(sharp_result.focus_score, blurred_result.focus_score)
        self.assertGreater(sharp_result.metrics.laplacian_variance, blurred_result.metrics.laplacian_variance)
        json.dumps(sharp_result.to_dict())

    def test_configured_thresholds_classify_separately_from_score(self):
        evaluator = FocusEvaluator(thresholds=FocusThresholdConfig(blurry_below=10.0, sharp_above=100.0, provisional=True))
        sharp = _checkerboard(dtype=np.uint8)

        result = evaluator.evaluate(CameraFrame(sharp, "MONO", "uint8", sharp.shape))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.classification, "sharp")
        self.assertIsInstance(result.focus_score, float)
        self.assertTrue(result.thresholds.provisional)

    def test_uint16_frame_is_not_downcast_to_uint8(self):
        evaluator = FocusEvaluator()
        sharp16 = _checkerboard(dtype=np.uint16)
        clipped8 = (sharp16 // 257).astype(np.uint8)

        result16 = evaluator.evaluate(CameraFrame(sharp16, "MONO", "uint16", sharp16.shape, {"pixelFormat": "Mono16"}))
        result8 = evaluator.evaluate(CameraFrame(clipped8, "MONO", "uint8", clipped8.shape, {"pixelFormat": "Mono8"}))

        self.assertEqual(result16.frame["dtype"], "uint16")
        self.assertEqual(result16.frame["pixelFormat"], "Mono16")
        self.assertGreater(result16.focus_score, result8.focus_score * 1000)

    def test_center_and_full_frame_roi_are_distinct(self):
        image = np.zeros((100, 100), dtype=np.uint8)
        image[:, :10] = 255
        image[:, -10:] = 255
        evaluator = FocusEvaluator(default_roi=FocusRoi("center", center_fraction=0.5))

        center = evaluator.evaluate(CameraFrame(image, "MONO", "uint8", image.shape))
        full = evaluator.evaluate(CameraFrame(image, "MONO", "uint8", image.shape), roi="full")

        self.assertEqual(center.roi["mode"], "center")
        self.assertEqual(center.roi["width"], 50)
        self.assertEqual(center.roi["height"], 50)
        self.assertEqual(full.roi, {"mode": "full", "x": 0, "y": 0, "width": 100, "height": 100})
        self.assertGreater(full.focus_score, center.focus_score)

    def test_invalid_shape_empty_and_dtype_are_rejected(self):
        evaluator = FocusEvaluator()
        cases = [
            np.zeros((4, 4, 3), dtype=np.uint8),
            np.zeros((0, 4), dtype=np.uint8),
            np.zeros((4, 4), dtype=np.float32),
        ]

        for image in cases:
            with self.subTest(shape=image.shape, dtype=image.dtype):
                with self.assertRaises(ValueError):
                    evaluator.evaluate(image)

    def test_non_finite_metric_values_are_reported_as_zero(self):
        evaluator = FocusEvaluator(cv2_module=FakeNanCv2)
        image = np.ones((8, 8), dtype=np.uint8)

        result = evaluator.evaluate(CameraFrame(image, "MONO", "uint8", image.shape))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.focus_score, 0.0)
        self.assertEqual(result.metrics.tenengrad, 0.0)
        self.assertEqual(result.metrics.laplacian_variance, 0.0)
        self.assertEqual(result.metrics.edge_density, 0.0)

    def test_mask_roi_is_reserved_and_safe_evaluate_is_json_friendly(self):
        evaluator = FocusEvaluator()
        image = np.ones((8, 8), dtype=np.uint8)

        with self.assertRaises(ValueError):
            evaluator.evaluate(image, roi="mask")
        result = evaluator.safe_evaluate(image, roi="mask", band_id="A520", wavelength_nm=520)

        self.assertEqual(result["status"], "evaluation_failed")
        self.assertEqual(result["classification"], "unknown")
        self.assertEqual(result["bandId"], "A520")
        self.assertEqual(result["wavelengthNm"], 520)
        json.dumps(result)


if __name__ == "__main__":
    unittest.main()
