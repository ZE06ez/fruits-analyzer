import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from quality_algorithm.calibration import reflectance_correction
from quality_algorithm.dataset import read_labels_csv
from quality_algorithm.filters import FilterBand
from quality_algorithm.roi import build_rgb_fruit_mask, roi_mean
from quality_algorithm.spectral_features import extract_feature_record, inspect_sample_structure


class QualityAlgorithmTests(unittest.TestCase):
    def make_sample(self, bands=(450, 560, 670), *, include_calibration=False) -> Path:
        root = Path(tempfile.mkdtemp(prefix="fta_quality_algo_"))
        rgb = root / "rgb"
        ms = root / "multispectral"
        rgb.mkdir()
        ms.mkdir()
        image = Image.new("RGB", (20, 20), (0, 0, 0))
        arr = np.asarray(image).copy()
        arr[5:15, 5:15] = [80, 130, 70]
        Image.fromarray(arr).save(rgb / "rgb_001.png")
        for band in bands:
            value = int((band - 300) / 4)
            Image.new("L", (20, 20), value).save(ms / f"{band}.png")
        if include_calibration:
            dark = root / "calibration" / "dark"
            white = root / "calibration" / "white"
            dark.mkdir(parents=True)
            white.mkdir(parents=True)
            for band in bands:
                Image.new("L", (20, 20), 10).save(dark / f"{band}.png")
                Image.new("L", (20, 20), 210).save(white / f"{band}.png")
        return root

    def test_one_rgb_and_multiple_multispectral_is_valid(self):
        sample = self.make_sample()
        report = inspect_sample_structure(sample)
        self.assertTrue(report["valid"])
        self.assertTrue(report["complete"])
        self.assertEqual(report["rgb_count"], 1)
        self.assertEqual(report["multispectral_count"], 3)

    def test_missing_enabled_band_and_disabled_band(self):
        filters = [
            FilterBand(1, 450, enabled=True),
            FilterBand(2, 560, enabled=True),
            FilterBand(3, 670, enabled=False),
        ]
        sample = self.make_sample(bands=(450,))
        report = inspect_sample_structure(sample, filters=filters)
        self.assertEqual(report["missing_bands"], [560])
        self.assertNotIn(670, report["missing_bands"])
        self.assertFalse(report["complete"])

    def test_calibration_formula_and_uint16_are_safe(self):
        sample = np.asarray([[1200, 2200]], dtype=np.uint16)
        dark = np.asarray([[200, 200]], dtype=np.uint16)
        white = np.asarray([[2200, 2200]], dtype=np.uint16)
        corrected = reflectance_correction(sample, dark, white)
        self.assertEqual(corrected.dtype, np.float32)
        self.assertAlmostEqual(float(corrected[0, 0]), 0.5, places=5)
        self.assertAlmostEqual(float(corrected[0, 1]), 1.0, places=5)

    def test_dark_white_matching_status(self):
        sample = self.make_sample(include_calibration=True)
        report = inspect_sample_structure(sample)
        self.assertEqual(report["calibration_status"], "complete")

    def test_roi_mean_and_wavelength_order(self):
        sample = self.make_sample(bands=(670, 450, 560))
        record = extract_feature_record(sample)
        self.assertEqual(record.wavelengths, [450, 560, 670])
        self.assertEqual(len(record.features), 3)
        self.assertGreater(record.roi_pixel_count, 0)
        rgb = np.zeros((6, 6, 3), dtype=np.uint8)
        rgb[1:5, 1:5] = [80, 120, 60]
        mask = build_rgb_fruit_mask(rgb)
        image = np.ones((6, 6), dtype=np.float32) * 2.0
        self.assertAlmostEqual(roi_mean(image, mask), 2.0)

    def test_labels_csv(self):
        folder = Path(tempfile.mkdtemp(prefix="fta_labels_"))
        labels = folder / "labels.csv"
        with labels.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample_id", "ssc", "ta", "ph"])
            writer.writerow(["sample_001", "11.6", "0.43", "3.58"])
        parsed = read_labels_csv(labels)
        self.assertEqual(parsed["sample_001"].ssc, 11.6)
        self.assertEqual(parsed["sample_001"].ta, 0.43)
        self.assertEqual(parsed["sample_001"].ph, 3.58)


if __name__ == "__main__":
    unittest.main()

