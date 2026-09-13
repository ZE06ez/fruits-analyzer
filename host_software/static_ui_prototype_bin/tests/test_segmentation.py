import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from quality_algorithm.background_reference import (
    BACKGROUND_REFERENCE_CAMERA_PROFILE_MISMATCH,
    BACKGROUND_REFERENCE_DEVICE_MISMATCH,
    BACKGROUND_REFERENCE_HASH_MISMATCH,
    BACKGROUND_REFERENCE_IMAGE_MISSING,
    BACKGROUND_REFERENCE_RESOLUTION_MISMATCH,
    create_background_reference,
    load_background_reference,
    save_background_reference,
    validate_background_reference,
)
from quality_algorithm.background_segmenter import (
    BACKGROUND_DIFFERENCE_TOO_LOW,
    FRUIT_MASK_TOO_LARGE,
    FRUIT_MASK_TOO_SMALL,
    FRUIT_NOT_DETECTED,
    FRUIT_TOUCHES_BORDER,
    MULTIPLE_LARGE_FOREGROUNDS,
    BackgroundReferenceSegmenter,
    BackgroundSegmentationConfig,
    clean_binary_mask,
    compute_difference_map,
)
from quality_algorithm.mask_quality import compute_mask_dice, compute_mask_iou
from quality_algorithm.segmentation import LegacyColorSegmenter
from quality_algorithm.spectral_features import FeatureExtractionError, extract_feature_record


class BackgroundReferenceSegmentationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="bg_seg_"))

    def image(self, fruit_box=(22, 18, 42, 38), *, background=(25, 28, 34), fruit=(120, 65, 42), size=(64, 56)) -> tuple[np.ndarray, np.ndarray]:
        bg = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        bg[:, :] = background
        sample = bg.copy()
        if fruit_box is not None:
            x0, y0, x1, y1 = fruit_box
            sample[y0:y1, x0:x1] = fruit
        return bg, sample

    def save_rgb(self, path: Path, array: np.ndarray) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(array).save(path)
        return path

    def make_reference(self, bg: np.ndarray, **metadata):
        path = self.save_rgb(self.root / "background.png", bg)
        return create_background_reference(path, **metadata)

    def test_reference_save_load_and_sha256_validation(self):
        bg, sample = self.image()
        reference = self.make_reference(bg, device={"stableId": "cam-a"})
        json_path = self.root / "background_reference.json"
        save_background_reference(reference, json_path)
        loaded = load_background_reference(json_path)
        self.assertEqual(loaded.referenceId, reference.referenceId)
        self.assertTrue(validate_background_reference(loaded, sample_shape=sample.shape[:2], camera_metadata={"device": {"stableId": "cam-a"}}).valid)

    def test_reference_missing_image_hash_resolution_and_device_guards(self):
        bg, sample = self.image()
        reference = self.make_reference(bg, device={"stableId": "cam-a"}, camera_profile={"actualFourcc": "MJPG"})
        missing = create_background_reference(reference.imagePath)
        missing.imagePath = str(self.root / "missing.png")
        self.assertEqual(validate_background_reference(missing).errorCode, BACKGROUND_REFERENCE_IMAGE_MISSING)
        reference.imageSha256 = "bad"
        self.assertEqual(validate_background_reference(reference).errorCode, BACKGROUND_REFERENCE_HASH_MISMATCH)
        reference = self.make_reference(bg, device={"stableId": "cam-a"}, camera_profile={"actualFourcc": "MJPG"})
        self.assertEqual(validate_background_reference(reference, sample_shape=(sample.shape[0] + 1, sample.shape[1])).errorCode, BACKGROUND_REFERENCE_RESOLUTION_MISMATCH)
        self.assertEqual(validate_background_reference(reference, camera_metadata={"device": {"stableId": "cam-b"}}).errorCode, BACKGROUND_REFERENCE_DEVICE_MISMATCH)
        self.assertEqual(validate_background_reference(reference, camera_metadata={"cameraProfile": {"actualFourcc": "YUY2"}}).errorCode, BACKGROUND_REFERENCE_CAMERA_PROFILE_MISMATCH)

    def test_segmenter_returns_explainable_mask_diagnostics(self):
        bg, sample = self.image()
        reference = self.make_reference(bg)
        result = BackgroundReferenceSegmenter().segment(sample, background_reference=reference, background_image=bg)
        self.assertTrue(result.valid, result.errorCode)
        self.assertEqual(result.algorithm, "background_reference")
        self.assertEqual(result.referenceId, reference.referenceId)
        self.assertEqual(result.maskShape, sample.shape[:2])
        self.assertGreater(result.foregroundPixelCount, 0)
        self.assertEqual(result.componentCount, 1)
        self.assertEqual(result.selectedComponentArea, result.foregroundPixelCount)
        self.assertIsNotNone(result.boundingBox)
        self.assertIsNotNone(result.centroid)
        self.assertIn("p95", result.differenceStats)

    def test_threshold_difference_map_and_low_difference_guard(self):
        bg, sample = self.image(fruit=(28, 30, 34))
        config = BackgroundSegmentationConfig(threshold=15, minDifferenceP95=20)
        diff = compute_difference_map(sample, bg, config)
        self.assertEqual(diff.shape, sample.shape[:2])
        result = BackgroundReferenceSegmenter(config).segment(sample, background_reference=self.make_reference(bg), background_image=bg)
        self.assertIn(BACKGROUND_DIFFERENCE_TOO_LOW, result.qualityFlags)

    def test_no_foreground_too_small_too_large_and_border_guards(self):
        bg, sample = self.image(fruit_box=None)
        reference = self.make_reference(bg)
        self.assertEqual(BackgroundReferenceSegmenter().segment(sample, background_reference=reference, background_image=bg).errorCode, FRUIT_NOT_DETECTED)
        _, tiny = self.image(fruit_box=(20, 20, 21, 21))
        tiny_config = BackgroundSegmentationConfig(openKernel=1, closeKernel=1)
        self.assertEqual(BackgroundReferenceSegmenter(tiny_config).segment(tiny, background_reference=reference, background_image=bg).errorCode, FRUIT_MASK_TOO_SMALL)
        _, huge = self.image(fruit_box=(2, 2, 62, 54))
        self.assertEqual(BackgroundReferenceSegmenter().segment(huge, background_reference=reference, background_image=bg).errorCode, FRUIT_MASK_TOO_LARGE)
        _, border = self.image(fruit_box=(0, 10, 20, 32))
        self.assertEqual(BackgroundReferenceSegmenter().segment(border, background_reference=reference, background_image=bg).errorCode, FRUIT_TOUCHES_BORDER)

    def test_multiple_large_components_is_not_blindly_max_only(self):
        bg, sample = self.image(fruit_box=None)
        sample[8:24, 8:24] = (120, 70, 40)
        sample[30:46, 38:54] = (122, 72, 42)
        result = BackgroundReferenceSegmenter(BackgroundSegmentationConfig(largeComponentRatio=0.04)).segment(sample, background_reference=self.make_reference(bg), background_image=bg)
        self.assertEqual(result.errorCode, MULTIPLE_LARGE_FOREGROUNDS)
        self.assertGreaterEqual(result.componentCount, 2)

    def test_morphology_closes_holes_without_fake_ellipse(self):
        raw = np.zeros((30, 30), dtype=bool)
        raw[6:24, 6:24] = True
        raw[12:16, 12:16] = False
        clean = clean_binary_mask(raw, BackgroundSegmentationConfig(openKernel=1, closeKernel=3, fillHoles=True))
        self.assertTrue(clean[13, 13])
        self.assertLessEqual(np.count_nonzero(clean), raw.size)

    def test_legacy_color_segmenter_still_exists_as_legacy_mode(self):
        _, sample = self.image()
        result = LegacyColorSegmenter().segment(sample)
        self.assertEqual(result.algorithm, "legacy_color")
        self.assertIn("LEGACY_COLOR_THRESHOLD", result.warnings)

    def test_background_reference_feature_extraction_requires_reference_and_uses_mask(self):
        bg, sample = self.image()
        sample_dir = self.root / "sample"
        self.save_rgb(sample_dir / "rgb" / "rgb_001.png", sample)
        (sample_dir / "multispectral").mkdir(parents=True, exist_ok=True)
        for band, value in ((450, 50), (560, 90), (670, 130)):
            spectral = np.full(sample.shape[:2], 8, dtype=np.uint8)
            spectral[18:38, 22:42] = value
            Image.fromarray(spectral, mode="L").save(sample_dir / "multispectral" / f"{band}.png")
            (sample_dir / "calibration" / "dark").mkdir(parents=True, exist_ok=True)
            (sample_dir / "calibration" / "white").mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.zeros(sample.shape[:2], dtype=np.uint8), mode="L").save(sample_dir / "calibration" / "dark" / f"dark_{band}.png")
            Image.fromarray(np.full(sample.shape[:2], 255, dtype=np.uint8), mode="L").save(sample_dir / "calibration" / "white" / f"white_{band}.png")
        with self.assertRaisesRegex(FeatureExtractionError, "BACKGROUND_REFERENCE_MISSING"):
            extract_feature_record(sample_dir, segmentation_mode="background_reference")
        reference = self.make_reference(bg)
        record = extract_feature_record(sample_dir, segmentation_mode="background_reference", background_reference=reference)
        self.assertEqual(record.wavelengths, [450, 560, 670])
        self.assertGreater(record.roi_pixel_count, 0)
        self.assertLess(record.features[0], record.features[1])
        self.assertLess(record.features[1], record.features[2])

    def test_feature_extraction_rejects_background_sample_resolution_mismatch(self):
        bg, sample = self.image()
        sample_dir = self.root / "mismatch_sample"
        self.save_rgb(sample_dir / "rgb" / "rgb_001.png", sample[:40, :40])
        (sample_dir / "multispectral").mkdir(parents=True, exist_ok=True)
        for band in (450, 560, 670):
            Image.fromarray(np.full((40, 40), 100, dtype=np.uint8), mode="L").save(sample_dir / "multispectral" / f"{band}.png")
        reference = self.make_reference(bg)
        with self.assertRaisesRegex(FeatureExtractionError, BACKGROUND_REFERENCE_RESOLUTION_MISMATCH):
            extract_feature_record(sample_dir, segmentation_mode="background_reference", background_reference=reference)

    def test_mask_quality_metrics_remain_generic(self):
        a = np.zeros((5, 5), dtype=bool)
        b = np.zeros((5, 5), dtype=bool)
        a[1:4, 1:4] = True
        b[2:5, 2:5] = True
        self.assertAlmostEqual(compute_mask_iou(a, a), 1.0)
        self.assertLess(compute_mask_iou(a, b), 1.0)
        self.assertLess(compute_mask_dice(a, b), 1.0)


if __name__ == "__main__":
    unittest.main()
