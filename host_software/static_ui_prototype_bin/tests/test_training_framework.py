import csv
import tempfile
import unittest
from pathlib import Path

from quality_algorithm.model_io import ModelInputMismatch, load_model_bundle, validate_feature_record
from quality_algorithm.spectral_features import FeatureRecord
from training.train import grouped_holdout, train_one


class TrainingFrameworkTests(unittest.TestCase):
    def make_feature_csv(self) -> Path:
        folder = Path(tempfile.mkdtemp(prefix="fta_training_"))
        path = folder / "features.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample_id", "R450", "R560", "R670", "ssc", "ta", "ph"])
            for index in range(12):
                r450 = 0.1 + index * 0.02
                r560 = 0.2 + index * 0.015
                r670 = 0.3 + index * 0.01
                writer.writerow([
                    f"sample_{index:03d}",
                    r450,
                    r560,
                    r670,
                    9.0 + r450 * 10,
                    0.35 + r560 * 0.1,
                    3.1 + r670 * 0.2,
                ])
        return path

    def test_grouped_holdout_keeps_same_sample_together(self):
        groups = ["s1", "s1", "s2", "s2", "s3", "s3", "s4", "s4"]
        train_idx, test_idx = grouped_holdout(groups)
        train_groups = {groups[i] for i in train_idx}
        test_groups = {groups[i] for i in test_idx}
        self.assertTrue(train_groups.isdisjoint(test_groups))

    def test_plsr_svr_rf_train_save_load(self):
        feature_csv = self.make_feature_csv()
        for model_type in ("PLSR", "SVR", "RF"):
            out = Path(tempfile.mkdtemp(prefix=f"fta_{model_type.lower()}_"))
            result = train_one(feature_csv, target="ssc", preprocessing="RAW", model_type=model_type, output_dir=out)
            self.assertTrue((out / "model.joblib").exists())
            self.assertTrue((out / "metadata.json").exists())
            bundle = load_model_bundle(out)
            self.assertEqual(bundle.metadata["model_type"], model_type)
            self.assertEqual(result["metadata"]["wavelengths_nm"], [450, 560, 670])

    def test_model_missing_is_not_affected_by_test_models(self):
        from quality_prediction import build_sample_session, predict_ssc
        from PIL import Image

        sample = Path(tempfile.mkdtemp(prefix="fta_prediction_missing_"))
        rgb = sample / "rgb"
        ms = sample / "multispectral"
        rgb.mkdir()
        ms.mkdir()
        Image.new("RGB", (16, 16), (80, 120, 70)).save(rgb / "rgb_001.png")
        for band in (450, 560, 670):
            Image.new("L", (16, 16), 128).save(ms / f"{band}.png")
        session, _report = build_sample_session(sample)
        result = predict_ssc(session)
        self.assertEqual(result.status, "model_missing")

    def test_model_input_mismatch_is_detected(self):
        record = FeatureRecord(
            sample_id="s1",
            wavelengths=[450, 560],
            features=[0.1, 0.2],
            calibrated=False,
            roi_pixel_count=10,
            source_dir="",
            warnings=[],
        )
        with self.assertRaises(ModelInputMismatch):
            validate_feature_record({"wavelengths_nm": [450, 560, 670]}, record)


if __name__ == "__main__":
    unittest.main()
