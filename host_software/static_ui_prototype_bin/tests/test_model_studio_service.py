import csv
import json
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import quality_prediction
from model_studio.service import ModelStudioError, ModelStudioService
from quality_prediction import build_sample_session, predict_ssc


class ModelStudioServiceTests(unittest.TestCase):
    def setUp(self):
        self.app_dir = Path(tempfile.mkdtemp(prefix="fta_model_studio_app_"))
        self.samples_root = Path(tempfile.mkdtemp(prefix="fta_model_studio_samples_"))
        self.service = ModelStudioService(self.app_dir)
        self._write_samples(9)
        self.labels_csv = self.samples_root / "labels.csv"
        with self.labels_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample_id", "ssc", "ta", "ph"])
            for index in range(9):
                writer.writerow([f"sample_{index:03d}", 10.0 + index * 0.12, 0.42 + index * 0.01, 3.4 + index * 0.02])

    def _write_samples(self, count: int) -> None:
        self._write_sample_range(0, count)

    def _write_sample_range(self, start: int, stop: int) -> None:
        for index in range(start, stop):
            sample = self.samples_root / f"sample_{index:03d}"
            rgb = sample / "rgb"
            ms = sample / "multispectral"
            dark = sample / "calibration" / "dark"
            white = sample / "calibration" / "white"
            rgb.mkdir(parents=True)
            ms.mkdir()
            dark.mkdir(parents=True)
            white.mkdir(parents=True)
            arr = np.zeros((24, 24, 3), dtype=np.uint8)
            arr[6:18, 6:18] = [70 + index, 120, 70]
            Image.fromarray(arr).save(rgb / "rgb_001.png")
            for band, base in [(450, 70), (560, 95), (670, 120)]:
                Image.new("L", (24, 24), base + index * 3).save(ms / f"{band}.png")
                Image.new("L", (24, 24), 0).save(dark / f"{band}.png")
                Image.new("L", (24, 24), 255).save(white / f"{band}.png")

    def test_dataset_versions_are_snapshots_and_excluded_samples_are_not_included(self):
        dataset = self.service.create_dataset({
            "datasetName": "Blueberry_Versions",
            "fruitType": "blueberry",
            "variety": "Duke",
            "storagePath": str(self.samples_root),
        })
        self.service.import_samples(dataset["dataset_id"])
        v1 = self.service.create_dataset_version(dataset["dataset_id"], "V1 snapshot")
        self.assertEqual(v1["sample_count"], 9)

        self._write_sample_range(9, 12)
        imported = self.service.import_samples(dataset["dataset_id"])
        self.assertEqual(imported["newSamples"], 3)
        self.assertEqual(self.service.get_dataset_version(v1["dataset_version_id"])["sample_count"], 9)

        self.service.update_sample_status(dataset["dataset_id"], "sample_011", "Excluded", "bad white reference")
        v2 = self.service.create_dataset_version(dataset["dataset_id"], "V2 with new samples")
        self.assertEqual(v2["sample_count"], 11)
        self.assertNotIn("sample_011", ",".join(v2["sample_ids"]))

    def test_sample_import_copies_into_managed_dataset_and_handles_duplicates(self):
        source_sample = self.samples_root / "sample_000"
        (source_sample / "metadata.json").write_text('{"sample_id":"sample_000"}', encoding="utf-8")
        dataset = self.service.create_dataset({
            "datasetName": "Blueberry_Managed_Copy",
            "fruitType": "blueberry",
            "variety": "Duke",
            "storagePath": str(self.samples_root),
        })
        validation = self.service.validate_sample_folder(source_sample)
        self.assertEqual(validation["status"], "Valid")

        imported = self.service.import_samples(dataset["dataset_id"], source_sample)
        self.assertEqual(imported["imported"], 1)
        sample = self.service.get_sample(dataset["dataset_id"], "sample_000")
        self.assertEqual(Path(sample["source_path"]), source_sample)
        self.assertNotEqual(Path(sample["local_path"]), source_sample)
        self.assertTrue((Path(sample["local_path"]) / "rgb" / "rgb_001.png").exists())
        self.assertTrue((Path(sample["local_path"]) / "multispectral" / "450.png").exists())
        self.assertTrue((Path(sample["local_path"]) / "calibration" / "dark" / "450.png").exists())
        self.assertTrue((Path(sample["local_path"]) / "metadata.json").exists())
        self.assertTrue(source_sample.exists())

        duplicate = self.service.import_samples(dataset["dataset_id"], source_sample)
        self.assertEqual(duplicate["imported"], 0)
        self.assertEqual(duplicate["conflicts"], 1)
        self.assertEqual(duplicate["skipped"], 1)

        copied_as_new = self.service.import_samples(dataset["dataset_id"], source_sample, duplicate_policy="new")
        self.assertEqual(copied_as_new["imported"], 1)
        samples = self.service.list_samples(dataset["dataset_id"])["items"]
        self.assertIn("sample_000_2", {item["sample_id"] for item in samples})
        copied_sample = self.service.get_sample(dataset["dataset_id"], "sample_000_2")
        copied_path = Path(copied_sample["local_path"])
        deleted = self.service.delete_sample(dataset["dataset_id"], "sample_000_2", delete_local_copy=True)
        self.assertTrue(deleted["sourceExists"])
        self.assertFalse(copied_path.exists())
        self.assertTrue(source_sample.exists())

    def test_save_sample_label_updates_sqlite_csv_dirty_and_version_snapshot(self):
        source_sample = self.samples_root / "sample_001"
        (source_sample / "metadata.json").write_text('{"sample_id":"sample_001"}', encoding="utf-8")
        dataset = self.service.create_dataset({
            "datasetName": "Blueberry_Label_Save",
            "fruitType": "blueberry",
            "variety": "Duke",
        })
        self.service.import_samples(dataset["dataset_id"], source_sample)

        first = self.service.save_sample_label(dataset["dataset_id"], "sample_001", {"ssc": "10.5", "ta": "0.41", "ph": "3.42"})
        self.assertEqual(first["label_status"], "Complete")
        v1 = self.service.create_dataset_version(dataset["dataset_id"], "Frozen labels")

        updated = self.service.save_sample_label(dataset["dataset_id"], "sample_001", {"ssc": "11.6", "ta": "", "ph": "3.58"})
        self.assertEqual(updated["label_status"], "Partial")
        self.assertEqual(self.service.get_dataset(dataset["dataset_id"])["dirty"], 1)
        labels_csv = Path(self.service.get_dataset(dataset["dataset_id"])["local_path"]) / "labels.csv"
        with labels_csv.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["sample_id"], "sample_001")
        self.assertEqual(rows[0]["ssc"], "11.6")
        self.assertEqual(rows[0]["ta"], "")
        self.assertEqual(rows[0]["ph"], "3.58")

        frozen = self.service.get_dataset_version(v1["dataset_version_id"])
        label_snapshot = json.loads(frozen["label_snapshot_json"])
        self.assertEqual(label_snapshot["sample_001"]["ssc"], 10.5)

        features = self.service.generate_features(dataset["dataset_id"], v1["dataset_version_id"])
        with Path(features["featureCsv"]).open("r", encoding="utf-8", newline="") as handle:
            feature_rows = list(csv.DictReader(handle))
        self.assertEqual(feature_rows[0]["ssc"], "10.5")

    def test_multiple_published_models_and_one_default_per_scope(self):
        dataset = self.service.create_dataset({
            "datasetName": "Blueberry_Defaults",
            "fruitType": "blueberry",
            "variety": "Duke",
            "storagePath": str(self.samples_root),
        })
        self.service.import_samples(dataset["dataset_id"])
        self.service.import_labels(dataset["dataset_id"], self.labels_csv)
        experiment = self.service.create_experiment({
            "datasetId": dataset["dataset_id"],
            "target": "ssc",
            "models": ["SVR", "RF"],
            "preprocessing": ["RAW"],
            "validationMethod": "GroupKFold",
        })
        job = self.service.create_training_job(experiment["experiment_id"])
        for _ in range(80):
            job = self.service.get_job(job["job_id"])
            if job["status"] in {"Completed", "Failed", "Cancelled"}:
                break
            time.sleep(0.15)
        self.assertEqual(job["status"], "Completed", job.get("error") or job.get("message"))
        models = [m for m in self.service.list_models() if m["target"] == "ssc"]
        self.assertGreaterEqual(len(models), 2)
        first = self.service.publish_model(models[0]["model_id"], {"displayName": "Duke SSC A"})
        second = self.service.publish_model(models[1]["model_id"], {"displayName": "Duke SSC B", "setDefault": True})
        self.assertEqual(first["status"], "Published")
        self.assertEqual(second["status"], "Default")
        visible = self.service.list_published_models(fruit_type="blueberry", variety="Duke", target="ssc")
        self.assertGreaterEqual(len(visible), 2)
        self.assertEqual(len([m for m in visible if m["status"] == "Default" or m["is_default"]]), 1)

    def test_model_catalog_filters_published_scope_and_generic_fallback(self):
        blueberry = self.service.create_dataset({
            "datasetName": "Blueberry_Catalog",
            "fruitType": "blueberry",
            "variety": "Duke",
            "storagePath": str(self.samples_root),
        })
        self.service.import_samples(blueberry["dataset_id"])
        self.service.import_labels(blueberry["dataset_id"], self.labels_csv)
        experiment = self.service.create_experiment({
            "datasetId": blueberry["dataset_id"],
            "target": "ssc",
            "models": ["SVR"],
            "preprocessing": ["RAW"],
            "validationMethod": "GroupKFold",
        })
        job = self.service.create_training_job(experiment["experiment_id"])
        for _ in range(80):
            job = self.service.get_job(job["job_id"])
            if job["status"] in {"Completed", "Failed", "Cancelled"}:
                break
            time.sleep(0.15)
        candidate = [m for m in self.service.list_models() if m["target"] == "ssc"][0]
        empty_catalog = self.service.model_catalog()
        self.assertNotIn("blueberry", [item.lower() for item in empty_catalog["fruitTypes"]])

        published = self.service.publish_model(candidate["model_id"], {"setDefault": True, "displayName": "Duke SSC"})
        catalog = self.service.model_catalog(fruit_type="blueberry", variety="Duke")
        self.assertIn("blueberry", [item.lower() for item in catalog["fruitTypes"]])
        self.assertIn("Duke", catalog["varieties"])
        self.assertEqual(catalog["defaults"]["ssc"]["model_id"], published["model_id"])
        self.assertEqual(catalog["compatible"]["ta"], [])
        self.assertEqual(self.service.list_published_models(fruit_type="apple", variety="Fuji", target="ssc"), [])

    def test_dataset_import_labels_features_training_publish_and_predict(self):
        dataset = self.service.create_dataset({
            "datasetName": "Blueberry_Test",
            "fruitType": "blueberry",
            "storagePath": str(self.samples_root),
        })
        imported = self.service.import_samples(dataset["dataset_id"])
        self.assertEqual(imported["imported"], 9)
        labels = self.service.import_labels(dataset["dataset_id"], self.labels_csv)
        self.assertEqual(labels["imported"], 9)

        quality = self.service.quality_report(dataset["dataset_id"])
        self.assertEqual(quality["completeSamples"], 9)
        self.assertEqual(len(quality["missingBands"]), 0)

        features = self.service.generate_features(dataset["dataset_id"])
        self.assertEqual(features["rows"], 9)
        self.assertTrue(Path(features["featureCsv"]).exists())

        experiment = self.service.create_experiment({
            "datasetId": dataset["dataset_id"],
            "target": "ssc",
            "models": ["PLSR", "SVR"],
            "preprocessing": ["RAW"],
            "validationMethod": "GroupKFold",
        })
        job = self.service.create_training_job(experiment["experiment_id"])
        for _ in range(80):
            job = self.service.get_job(job["job_id"])
            if job["status"] in {"Completed", "Failed", "Cancelled"}:
                break
            time.sleep(0.15)
        self.assertEqual(job["status"], "Completed", job.get("error") or job.get("message"))
        self.assertGreaterEqual(len(job["result"]["results"]), 2)

        models = self.service.list_models()
        self.assertGreaterEqual(len(models), 2)
        production = self.service.publish_model(models[0]["model_id"], {"setDefault": True, "displayName": "Blueberry SSC Default"})
        self.assertEqual(production["status"], "Default")
        self.assertTrue((self.app_dir / "trained_models" / "ssc" / "model.joblib").exists())
        self.assertEqual(len([m for m in self.service.list_models() if m["target"] == "ssc" and m["status"] == "Default"]), 1)

        old_root = quality_prediction.MODEL_ROOT
        try:
            quality_prediction.MODEL_ROOT = self.app_dir / "trained_models"
            session, report = build_sample_session(self.samples_root / "sample_001", fruit_type="blueberry", variety="generic")
            self.assertTrue(report["complete"])
            result = predict_ssc(session)
            self.assertEqual(result.status, "success")
            self.assertIsNotNone(result.value)
            self.assertEqual(result.model_id, production["model_id"])
        finally:
            quality_prediction.MODEL_ROOT = old_root

    def test_empty_dataset_version_cannot_start_training_job(self):
        dataset = self.service.create_dataset({
            "datasetName": "Empty_Training_Set",
            "fruitType": "blueberry",
            "storagePath": str(self.samples_root),
        })
        version = self.service.create_dataset_version(dataset["dataset_id"], "Empty snapshot")
        self.assertEqual(version["sample_count"], 0)
        experiment = self.service.create_experiment({
            "datasetId": dataset["dataset_id"],
            "datasetVersionId": version["dataset_version_id"],
            "target": "ssc",
            "models": ["PLSR"],
            "preprocessing": ["RAW"],
        })
        with self.assertRaisesRegex(ModelStudioError, "没有样品"):
            self.service.create_training_job(experiment["experiment_id"])


if __name__ == "__main__":
    unittest.main()
