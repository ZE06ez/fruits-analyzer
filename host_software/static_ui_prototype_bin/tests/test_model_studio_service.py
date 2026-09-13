import csv
import json
import tempfile
import time
import unittest
import sqlite3
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

    def _write_sample_metadata(self, folder_name: str, **values) -> None:
        sample = self.samples_root / folder_name
        metadata = {
            "sample_id": values.get("sample_id", folder_name),
            "sample_name": values.get("sample_name", folder_name),
        }
        metadata.update({key: value for key, value in values.items() if value is not None})
        (sample / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

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

    def test_dataset_version_diff_tracks_added_removed_and_label_changes(self):
        dataset = self.service.create_dataset({
            "datasetName": "Blueberry_Diff",
            "fruitType": "blueberry",
            "variety": "Duke",
            "storagePath": str(self.samples_root),
        })
        self.service.import_samples(dataset["dataset_id"], self.samples_root / "sample_000")
        self.service.import_samples(dataset["dataset_id"], self.samples_root / "sample_001")
        self.service.save_sample_label(dataset["dataset_id"], "sample_000", {"ssc": "10.0", "ta": "0.64", "ph": "3.5"})
        v1 = self.service.create_dataset_version(dataset["dataset_id"], "V1")

        self.service.import_samples(dataset["dataset_id"], self.samples_root / "sample_002")
        self.service.update_sample_status(dataset["dataset_id"], "sample_001", "Excluded", "Outlier")
        self.service.save_sample_label(dataset["dataset_id"], "sample_000", {"ssc": "10.0", "ta": "0.59", "ph": "3.5"})
        v2 = self.service.create_dataset_version(dataset["dataset_id"], "V2")

        diff = self.service.dataset_version_diff(v1["dataset_version_id"], v2["dataset_version_id"])
        self.assertEqual(diff["summary"]["addedSamples"], 1)
        self.assertEqual(diff["summary"]["removedOrExcludedSamples"], 1)
        self.assertEqual(diff["summary"]["changedLabels"], 1)
        self.assertEqual(diff["addedSamples"], ["sample_002"])
        self.assertEqual(diff["removedOrExcludedSamples"], ["sample_001"])
        self.assertEqual(diff["labelChanges"][0]["changes"]["ta"], {"from": 0.64, "to": 0.59})

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

    def test_import_samples_prefers_sample_metadata_scope_and_initializes_dataset_scope(self):
        self._write_sample_metadata(
            "sample_000",
            sample_id="Duke_001",
            sample_name="Duke Training 001",
            sample_mode="training_capture",
            fruit_type="蓝莓",
            variety="Duke",
        )
        dataset = self.service.create_dataset({
            "datasetName": "Training Capture Import",
            "storagePath": str(self.samples_root / "sample_000"),
        })

        imported = self.service.import_samples(dataset["dataset_id"], self.samples_root / "sample_000")

        self.assertEqual(imported["imported"], 1)
        self.assertEqual(imported["scope"]["source"], "sample_metadata")
        dataset = self.service.get_dataset(dataset["dataset_id"])
        self.assertEqual(dataset["fruit_type"], "蓝莓")
        self.assertEqual(dataset["variety"], "Duke")
        sample = self.service.get_sample(dataset["dataset_id"], "Duke_001")
        self.assertEqual(sample["sample_name"], "Duke Training 001")
        self.assertEqual(sample["fruit_type"], "蓝莓")
        self.assertEqual(sample["variety"], "Duke")

    def test_import_samples_rejects_scope_conflicts(self):
        self._write_sample_metadata("sample_000", fruit_type="蓝莓", variety="Duke")
        self._write_sample_metadata("sample_001", fruit_type="苹果", variety="Fuji")
        dataset = self.service.create_dataset({
            "datasetName": "Mixed Scope",
            "storagePath": str(self.samples_root),
        })

        with self.assertRaisesRegex(ModelStudioError, "DATASET_SAMPLE_SCOPE_CONFLICT") as ctx:
            self.service.import_samples(dataset["dataset_id"], self.samples_root)

        message = str(ctx.exception)
        self.assertIn("蓝莓", message)
        self.assertIn("苹果", message)
        self.assertEqual(self.service.list_samples(dataset["dataset_id"])["total"], 0)

    def test_import_samples_falls_back_to_dataset_scope_for_legacy_metadata(self):
        self._write_sample_metadata("sample_000")
        dataset = self.service.create_dataset({
            "datasetName": "Legacy Scope",
            "fruitType": "blueberry",
            "variety": "Duke",
            "storagePath": str(self.samples_root / "sample_000"),
        })

        self.service.import_samples(dataset["dataset_id"], self.samples_root / "sample_000")

        sample = self.service.get_sample(dataset["dataset_id"], "sample_000")
        self.assertEqual(sample["fruit_type"], "blueberry")
        self.assertEqual(sample["variety"], "Duke")

    def test_dataset_scope_conflict_blocks_import_into_existing_scope(self):
        self._write_sample_metadata("sample_000", fruit_type="苹果", variety="Fuji")
        dataset = self.service.create_dataset({
            "datasetName": "Blueberry Existing Scope",
            "fruitType": "蓝莓",
            "variety": "Duke",
            "storagePath": str(self.samples_root / "sample_000"),
        })

        with self.assertRaisesRegex(ModelStudioError, "DATASET_SAMPLE_SCOPE_CONFLICT"):
            self.service.import_samples(dataset["dataset_id"], self.samples_root / "sample_000")

    def test_referenced_sample_cannot_be_replaced_or_permanently_deleted(self):
        source_sample = self.samples_root / "sample_003"
        (source_sample / "metadata.json").write_text('{"sample_id":"sample_003"}', encoding="utf-8")
        dataset = self.service.create_dataset({
            "datasetName": "Blueberry_Delete_Guard",
            "fruitType": "blueberry",
            "variety": "Duke",
        })
        self.service.import_samples(dataset["dataset_id"], source_sample)
        version = self.service.create_dataset_version(dataset["dataset_id"], "Frozen")
        refs = self.service.sample_references(dataset["dataset_id"], "sample_003")
        self.assertTrue(refs["blocked"])
        self.assertEqual(refs["versions"][0]["datasetVersionId"], version["dataset_version_id"])
        with self.assertRaisesRegex(ModelStudioError, "referenced"):
            self.service.delete_sample(dataset["dataset_id"], "sample_003", delete_local_copy=True)
        with self.assertRaisesRegex(ModelStudioError, "referenced"):
            self.service.import_samples(dataset["dataset_id"], source_sample, duplicate_policy="replace")

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

    def test_existing_sqlite_migration_preserves_rows(self):
        app_dir = Path(tempfile.mkdtemp(prefix="fta_old_studio_"))
        db_dir = app_dir / "model_studio" / "database"
        db_dir.mkdir(parents=True)
        db = db_dir / "model_studio.sqlite"
        with sqlite3.connect(db) as conn:
            conn.executescript(
                """
                CREATE TABLE datasets(dataset_id TEXT PRIMARY KEY,dataset_name TEXT NOT NULL,fruit_type TEXT,variety TEXT,description TEXT,created_at TEXT NOT NULL,storage_path TEXT NOT NULL,sample_count INTEGER DEFAULT 0,label_count INTEGER DEFAULT 0,enabled_wavelengths TEXT,calibration_status TEXT DEFAULT 'missing');
                CREATE TABLE dataset_versions(dataset_version_id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,version INTEGER NOT NULL,version_name TEXT NOT NULL,sample_count INTEGER DEFAULT 0,sample_ids TEXT NOT NULL,label_count INTEGER DEFAULT 0,created_at TEXT NOT NULL,created_by TEXT,description TEXT,parent_version TEXT,snapshot_hash TEXT NOT NULL);
                CREATE TABLE samples(id INTEGER PRIMARY KEY AUTOINCREMENT,dataset_id TEXT NOT NULL,sample_id TEXT NOT NULL,fruit_type TEXT,variety TEXT,storage_path TEXT NOT NULL,rgb_count INTEGER DEFAULT 0,multispectral_count INTEGER DEFAULT 0,dark_count INTEGER DEFAULT 0,white_count INTEGER DEFAULT 0,ssc REAL,ta REAL,ph REAL,data_status TEXT DEFAULT 'unknown',capture_time TEXT,quality_json TEXT,created_at TEXT NOT NULL,UNIQUE(dataset_id,sample_id));
                CREATE TABLE labels(id INTEGER PRIMARY KEY AUTOINCREMENT,dataset_id TEXT NOT NULL,sample_id TEXT NOT NULL,ssc REAL,ta REAL,ph REAL,updated_at TEXT NOT NULL,UNIQUE(dataset_id,sample_id));
                CREATE TABLE training_experiments(experiment_id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,experiment_name TEXT NOT NULL,target TEXT NOT NULL,description TEXT,models_json TEXT NOT NULL,preprocessing_json TEXT NOT NULL,validation_method TEXT NOT NULL,status TEXT NOT NULL,feature_csv TEXT,result_json TEXT,created_at TEXT NOT NULL);
                CREATE TABLE jobs(job_id TEXT PRIMARY KEY,experiment_id TEXT NOT NULL,status TEXT NOT NULL,step TEXT NOT NULL,progress INTEGER DEFAULT 0,message TEXT,logs_json TEXT,result_json TEXT,error TEXT,cancel_requested INTEGER DEFAULT 0,started_at TEXT,finished_at TEXT,created_at TEXT NOT NULL);
                CREATE TABLE models(model_id TEXT PRIMARY KEY,experiment_id TEXT,dataset_id TEXT,model_name TEXT NOT NULL,target TEXT NOT NULL,model_type TEXT NOT NULL,preprocessing TEXT NOT NULL,version TEXT NOT NULL,status TEXT NOT NULL,r2 REAL,rmse REAL,mae REAL,rpd REAL,model_dir TEXT NOT NULL,metadata_json TEXT,created_at TEXT NOT NULL,published_at TEXT);
                CREATE TABLE operation_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp TEXT NOT NULL,operation TEXT NOT NULL,target TEXT,resource_id TEXT,message TEXT);
                INSERT INTO datasets(dataset_id,dataset_name,fruit_type,variety,created_at,storage_path) VALUES('old_ds','Old','blueberry','Duke','2026-01-01','C:/old');
                """
            )
        migrated = ModelStudioService(app_dir)
        row = migrated.get_dataset("old_ds")
        self.assertEqual(row["dataset_name"], "Old")
        self.assertIn("updated_at", row)
        self.assertIn("archived", row)

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
        with self.assertRaisesRegex(ModelStudioError, "default model cannot be permanently deleted"):
            self.service.delete_model_permanently(second["model_id"], confirm=second["model_id"])
        self.service.archive_model(first["model_id"])
        self.assertEqual(self.service.get_model(first["model_id"])["status"], "Archived")
        self.assertEqual(self.service.list_published_models(fruit_type="blueberry", variety="Duke", target="ssc")[0]["model_id"], second["model_id"])

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

    def test_permanent_delete_removes_db_and_managed_files_after_default_switch(self):
        first = self._insert_fake_model("delete_a", status="Published")
        second = self._insert_fake_model("delete_b", status="Published")
        self.service.set_default_model(first)
        self.service.set_default_model(second)
        old = self.service.get_model(first)
        deleted = self.service.delete_model_permanently(first, confirm=first)
        self.assertTrue(deleted["deleted"])
        with self.assertRaises(ModelStudioError):
            self.service.get_model(first)
        self.assertFalse(Path(old["model_dir"]).exists())

    def test_empty_dataset_can_be_archived_and_permanently_deleted_without_touching_source(self):
        external_source = self.samples_root
        dataset = self.service.create_dataset({
            "datasetName": "Scratch_Delete_Empty",
            "fruitType": "blueberry",
            "variety": "Duke",
            "storagePath": str(external_source),
        })
        local_path = Path(dataset["local_path"])
        archived = self.service.archive_dataset(dataset["dataset_id"])
        self.assertEqual(archived["archived"], 1)
        self.assertNotIn(dataset["dataset_id"], {item["dataset_id"] for item in self.service.list_datasets(archived="0")})
        self.assertIn(dataset["dataset_id"], {item["dataset_id"] for item in self.service.list_datasets(archived="all")})

        deleted = self.service.delete_dataset_permanently(dataset["dataset_id"], confirm=dataset["dataset_name"])
        self.assertTrue(deleted["deleted"])
        self.assertFalse(local_path.exists())
        self.assertTrue(external_source.exists())
        with self.assertRaises(ModelStudioError):
            self.service.get_dataset(dataset["dataset_id"])

    def test_dataset_permanent_delete_cascades_non_production_records_and_managed_paths(self):
        dataset = self.service.create_dataset({
            "datasetName": "Scratch_Delete_Cascade",
            "fruitType": "blueberry",
            "variety": "Duke",
            "storagePath": str(self.samples_root),
        })
        self.service.import_samples(dataset["dataset_id"], self.samples_root / "sample_000")
        self.service.save_sample_label(dataset["dataset_id"], "sample_000", {"ssc": "10.1"})
        version = self.service.create_dataset_version(dataset["dataset_id"], "Trash V1")
        experiment = self.service.create_experiment({
            "datasetId": dataset["dataset_id"],
            "datasetVersionId": version["dataset_version_id"],
            "target": "ssc",
            "models": ["PLSR"],
            "preprocessing": ["RAW"],
        })
        artifact_file = self.service.artifact_dir / "features" / dataset["dataset_id"] / "features.csv"
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_text("sample_id,ssc\nsample_000,10.1\n", encoding="utf-8")
        with self.service.connect() as conn:
            conn.execute("UPDATE training_experiments SET feature_csv=? WHERE experiment_id=?", (str(artifact_file), experiment["experiment_id"]))
            conn.execute(
                """
                INSERT INTO jobs(job_id,experiment_id,status,step,created_at,dataset_version_id)
                VALUES('job_cascade',?,'Completed','Done','2026-01-01',?)
                """,
                (experiment["experiment_id"], version["dataset_version_id"]),
            )
        candidate_id = self._insert_fake_model(
            "cascade_candidate",
            status="Candidate",
            dataset_id=dataset["dataset_id"],
            dataset_version_id=version["dataset_version_id"],
            experiment_id=experiment["experiment_id"],
            job_id="job_cascade",
        )
        archived_id = self._insert_fake_model(
            "cascade_archived",
            status="Archived",
            dataset_id=dataset["dataset_id"],
            dataset_version_id=version["dataset_version_id"],
            experiment_id=experiment["experiment_id"],
            job_id="job_cascade",
        )
        candidate_dir = Path(self.service.get_model(candidate_id)["model_dir"])
        archived_dir = Path(self.service.get_model(archived_id)["model_dir"])
        local_path = Path(self.service.get_dataset(dataset["dataset_id"])["local_path"])

        refs = self.service.dataset_references(dataset["dataset_id"])
        self.assertTrue(refs["canDeletePermanently"])
        self.assertEqual(refs["summary"]["models"], 2)
        result = self.service.delete_dataset_permanently(dataset["dataset_id"], confirm=dataset["dataset_name"])
        self.assertTrue(result["deleted"])
        self.assertFalse(local_path.exists())
        self.assertFalse(candidate_dir.exists())
        self.assertFalse(archived_dir.exists())
        self.assertFalse(artifact_file.exists())
        with self.service.connect() as conn:
            for table in ("datasets", "samples", "labels", "dataset_versions", "training_experiments", "jobs", "models"):
                column = "dataset_id" if table not in {"jobs", "models"} else None
                if table == "jobs":
                    count = conn.execute("SELECT COUNT(*) FROM jobs WHERE job_id='job_cascade'").fetchone()[0]
                elif table == "models":
                    count = conn.execute("SELECT COUNT(*) FROM models WHERE model_id IN ('cascade_candidate','cascade_archived')").fetchone()[0]
                else:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (dataset["dataset_id"],)).fetchone()[0]
                self.assertEqual(count, 0, table)

    def test_dataset_permanent_delete_blocks_published_and_default_model_references(self):
        for status in ("Published", "Default"):
            dataset = self.service.create_dataset({
                "datasetName": f"Scratch_Delete_Block_{status}",
                "fruitType": "blueberry",
                "variety": "Duke",
            })
            version = self.service.create_dataset_version(dataset["dataset_id"], "Referenced")
            model_id = self._insert_fake_model(
                f"block_{status.lower()}",
                status=status,
                dataset_id=dataset["dataset_id"],
                dataset_version_id=version["dataset_version_id"],
            )
            refs = self.service.dataset_references(dataset["dataset_id"])
            self.assertFalse(refs["canDeletePermanently"])
            self.assertEqual(refs["blockCode"], "DATASET_HAS_PRODUCTION_MODEL_REFERENCES")
            with self.assertRaisesRegex(ModelStudioError, "DATASET_HAS_PRODUCTION_MODEL_REFERENCES"):
                self.service.delete_dataset_permanently(dataset["dataset_id"], confirm=dataset["dataset_name"])
            self.assertEqual(self.service.get_dataset(dataset["dataset_id"])["dataset_id"], dataset["dataset_id"])
            self.assertEqual(self.service.get_model(model_id)["model_id"], model_id)

    def test_model_permanent_delete_status_rules_and_station_catalog_refresh(self):
        candidate_id = self._insert_fake_model("delete_candidate", status="Candidate")
        candidate_dir = Path(self.service.get_model(candidate_id)["model_dir"])
        self.service.delete_model_permanently(candidate_id, confirm=candidate_id)
        with self.assertRaises(ModelStudioError):
            self.service.get_model(candidate_id)
        self.assertFalse(candidate_dir.exists())

        published_id = self._insert_fake_model("delete_published", status="Published")
        published = self.service.get_model(published_id)
        published_dir = Path(published["model_dir"])
        published_copy = self.service.production_dir / "published" / published_id
        self.assertIn(published_id, [item["model_id"] for item in self.service.model_catalog(fruit_type="blueberry", variety="Duke")["compatible"]["ssc"]])
        self.service.delete_model_permanently(published_id, confirm=published_id)
        self.assertFalse(published_dir.exists())
        self.assertFalse(published_copy.exists())
        self.assertNotIn(published_id, [item["model_id"] for item in self.service.model_catalog(fruit_type="blueberry", variety="Duke")["compatible"]["ssc"]])
        self.assertNotIn(published_id, [item["model_id"] for item in self.service.list_models()])

        default_id = self._insert_fake_model("delete_default", status="Default")
        with self.assertRaisesRegex(ModelStudioError, "default model cannot be permanently deleted"):
            self.service.delete_model_permanently(default_id, confirm=default_id)

    def test_model_batch_delete_reuses_single_delete_rules(self):
        candidate_id = self._insert_fake_model("batch_candidate", status="Candidate")
        published_id = self._insert_fake_model("batch_published", status="Published")
        default_id = self._insert_fake_model("batch_default", status="Default")

        result = self.service.delete_models_permanently_batch([candidate_id, published_id, default_id, candidate_id])

        self.assertEqual({item["modelId"] for item in result["deleted"]}, {candidate_id, published_id})
        self.assertEqual([item["modelId"] for item in result["blocked"]], [default_id])
        self.assertFalse(result["failed"])
        for model_id in (candidate_id, published_id):
            with self.assertRaises(ModelStudioError):
                self.service.get_model(model_id)
        self.assertEqual(self.service.get_model(default_id)["model_id"], default_id)

    def test_set_default_model_switches_previous_default_in_same_scope(self):
        first = self._insert_fake_model("scope_default_a", status="Published")
        second = self._insert_fake_model("scope_default_b", status="Published")

        self.service.set_default_model(first)
        self.service.set_default_model(second)

        self.assertFalse(self.service.get_model(first)["isDefault"])
        self.assertEqual(self.service.get_model(first)["status"], "Published")
        self.assertTrue(self.service.get_model(second)["isDefault"])
        visible = self.service.list_published_models(fruit_type="blueberry", variety="Duke", target="ssc")
        self.assertEqual(len([model for model in visible if model["isDefault"]]), 1)

    def test_archive_model_keeps_files_and_hides_from_station_catalog(self):
        published_id = self._insert_fake_model("archive_keeps_files", status="Published")
        published_dir = Path(self.service.get_model(published_id)["model_dir"])
        self.service.archive_model(published_id)
        self.assertTrue(published_dir.exists())
        self.assertEqual(self.service.get_model(published_id)["status"], "Archived")
        self.assertNotIn(published_id, [item["model_id"] for item in self.service.model_catalog(fruit_type="blueberry", variety="Duke")["compatible"]["ssc"]])

    def test_managed_path_guards_reject_unmanaged_model_and_dataset_paths(self):
        model_id = self._insert_fake_model("unmanaged_path", status="Candidate")
        external_model_dir = self.samples_root / "external_model_dir"
        external_model_dir.mkdir()
        (external_model_dir / "model.joblib").write_bytes(b"fake")
        (external_model_dir / "metadata.json").write_text("{}", encoding="utf-8")
        with self.service.connect() as conn:
            conn.execute("UPDATE models SET model_dir=? WHERE model_id=?", (str(external_model_dir), model_id))
        with self.assertRaisesRegex(ModelStudioError, "unmanaged model path"):
            self.service.delete_model_permanently(model_id, confirm=model_id)
        self.assertTrue(external_model_dir.exists())

        dataset = self.service.create_dataset({"datasetName": "Bad Dataset Path", "fruitType": "blueberry"})
        with self.service.connect() as conn:
            conn.execute("UPDATE datasets SET local_path=?, storage_path=? WHERE dataset_id=?", (str(self.samples_root), str(self.samples_root), dataset["dataset_id"]))
        with self.assertRaisesRegex(ModelStudioError, "unmanaged dataset path"):
            self.service.delete_dataset_permanently(dataset["dataset_id"], confirm=dataset["dataset_name"])
        self.assertTrue(self.samples_root.exists())

    def test_create_dataset_keeps_dynamic_fruit_type_as_model_scope(self):
        dataset = self.service.create_dataset({"datasetName": "Dragon Fruit", "fruitType": "  dragon   fruit "})
        self.assertEqual(dataset["fruit_type"], "dragon   fruit")
        localized = self.service.create_dataset({"datasetName": "Localized Fruit", "fruitType": "蓝莓"})
        self.assertEqual(localized["fruit_type"], "蓝莓")

    def test_training_start_from_current_config_creates_target_specific_experiment(self):
        dataset = self.service.create_dataset({
            "datasetName": "Blueberry_Targets",
            "fruitType": "blueberry",
            "variety": "Duke",
            "storagePath": str(self.samples_root),
        })
        self.service.import_samples(dataset["dataset_id"])
        self.service.import_labels(dataset["dataset_id"], self.labels_csv)
        version = self.service.create_dataset_version(dataset["dataset_id"], "Targets")
        ssc = self.service.create_experiment_and_training_job({
            "datasetId": dataset["dataset_id"],
            "datasetVersionId": version["dataset_version_id"],
            "target": "ssc",
            "models": ["PLSR"],
            "preprocessing": ["RAW"],
            "fruitType": "apple",
            "variety": "Fuji",
        })
        ta = self.service.create_experiment_and_training_job({
            "datasetId": dataset["dataset_id"],
            "datasetVersionId": version["dataset_version_id"],
            "target": "ta",
            "models": ["PLSR"],
            "preprocessing": ["RAW"],
        })
        self.assertNotEqual(ssc["experiment"]["experiment_id"], ta["experiment"]["experiment_id"])
        self.assertEqual(ssc["experiment"]["fruit_type"], "blueberry")
        self.assertEqual(ssc["experiment"]["variety"], "Duke")
        self.assertEqual(self.service.get_experiment(ta["experiment"]["experiment_id"])["target"], "ta")
        job = self._wait_studio_job(ta["job"]["job_id"])
        self.assertEqual(job["status"], "Completed", job.get("error") or job.get("message"))
        self.assertTrue(all(row.get("target") == "ta" for row in job["result"]["results"] if not row.get("error")))

    def test_ph_experiment_generates_ph_model_variants(self):
        dataset = self.service.create_dataset({
            "datasetName": "Blueberry_PH",
            "fruitType": "blueberry",
            "variety": "Duke",
            "storagePath": str(self.samples_root),
        })
        self.service.import_samples(dataset["dataset_id"])
        self.service.import_labels(dataset["dataset_id"], self.labels_csv)
        version = self.service.create_dataset_version(dataset["dataset_id"], "pH")
        result = self.service.create_experiment_and_training_job({
            "datasetId": dataset["dataset_id"],
            "datasetVersionId": version["dataset_version_id"],
            "target": "ph",
            "models": ["PLSR"],
            "preprocessing": ["RAW"],
        })
        job = self._wait_studio_job(result["job"]["job_id"])
        self.assertEqual(job["status"], "Completed", job.get("error") or job.get("message"))
        self.assertTrue(all(row.get("target") == "ph" for row in job["result"]["results"] if not row.get("error")))
        model = [item for item in self.service.list_models() if item["target"] == "ph"][0]
        self.assertEqual(model["fruit_type"], "blueberry")
        self.assertEqual(model["variety"], "Duke")
        metadata = json.loads((Path(model["model_dir"]) / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["fruit_type"], "blueberry")
        self.assertEqual(metadata["variety"], "Duke")

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

    def _wait_studio_job(self, job_id: str) -> dict:
        for _ in range(80):
            job = self.service.get_job(job_id)
            if job["status"] in {"Completed", "Failed", "Cancelled"}:
                return job
            time.sleep(0.15)
        self.fail(f"training job did not finish: {job_id}")

    def _insert_fake_model(
        self,
        model_id: str,
        *,
        status: str = "Published",
        target: str = "ssc",
        dataset_id: str = "",
        dataset_version_id: str = "",
        experiment_id: str = "",
        job_id: str = "",
    ) -> str:
        model_dir = self.service.model_dir / "candidates" / "fake" / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "model.joblib").write_bytes(b"fake")
        (model_dir / "metadata.json").write_text(json.dumps({
            "model_id": model_id,
            "model_version": "v1",
            "target": target,
            "model_type": "PLSR",
            "preprocessing": "RAW",
        }), encoding="utf-8")
        with self.service.connect() as conn:
            conn.execute(
                """
                INSERT INTO models(model_id,experiment_id,dataset_id,model_name,display_name,target,fruit_type,variety,model_type,preprocessing,version,status,is_default,dataset_version_id,dataset_version_label,job_id,model_dir,metadata_json,created_at,published_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    model_id,
                    experiment_id,
                    dataset_id,
                    model_id,
                    model_id,
                    target,
                    "blueberry",
                    "Duke",
                    "PLSR",
                    "RAW",
                    "v1",
                    status,
                    0,
                    dataset_version_id,
                    dataset_version_id,
                    job_id,
                    str(model_dir),
                    "{}",
                    "2026-01-01 00:00:00",
                    "2026-01-01 00:00:00",
                ),
            )
        if status in {"Published", "Default", "Production"}:
            self.service.publish_model(model_id, {"displayName": model_id, "setDefault": status == "Default"})
        return model_id


if __name__ == "__main__":
    unittest.main()
