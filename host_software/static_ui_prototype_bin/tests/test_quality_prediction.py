import tempfile
import unittest
from pathlib import Path

from PIL import Image

from quality_prediction import build_sample_session, predict_ph, predict_ssc, predict_ta


class QualityPredictionTests(unittest.TestCase):
    def make_dataset(self) -> Path:
        dataset = Path(tempfile.mkdtemp(prefix="fta_quality_session_"))
        rgb = dataset / "rgb"
        spectral = dataset / "multispectral"
        rgb.mkdir()
        spectral.mkdir()
        Image.new("RGB", (16, 16), (80, 120, 70)).save(rgb / "rgb_001.png")
        for band in (450, 560, 670):
            Image.new("L", (16, 16), 128).save(spectral / f"{band}.png")
        return dataset

    def test_sample_session_uses_unified_analysis_folder(self):
        dataset = self.make_dataset()
        session, report = build_sample_session(dataset, sample_id="S001", rgb_dir="rgb", spectral_dir="multispectral")

        self.assertEqual(session.sample_id, "S001")
        self.assertEqual(Path(session.analysis_data_dir), dataset)
        self.assertEqual(len(session.rgb_files), 1)
        self.assertEqual(len(session.multispectral_files), 3)
        self.assertTrue(report["valid"])
        self.assertTrue(report["complete"])

    def test_prediction_entries_do_not_fake_values(self):
        dataset = self.make_dataset()
        session, _report = build_sample_session(dataset)

        ssc = predict_ssc(session)
        ta = predict_ta(session)
        ph = predict_ph(session)

        self.assertIsNone(ssc.value)
        self.assertIsNone(ta.value)
        self.assertIsNone(ph.value)
        self.assertEqual(ssc.status, "model_missing")
        self.assertEqual(ta.status, "model_missing")
        self.assertEqual(ph.status, "model_missing")
        self.assertEqual(ssc.unit, "°Brix")
        self.assertEqual(ta.unit, "%")
        self.assertEqual(ph.unit, "pH")


if __name__ == "__main__":
    unittest.main()
