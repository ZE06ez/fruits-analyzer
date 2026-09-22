from __future__ import annotations

import json
import gc
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import urlopen

from backend_server import start_backend
from inspection.repository import INSPECTION_SCHEMA_VERSION, InspectionRepository
from model_studio.service import MODEL_STUDIO_SCHEMA_VERSION, ModelStudioError, ModelStudioService
from runtime_support import (
    DatabaseMigrationError,
    RuntimeConfigurationError,
    atomic_write_json,
    bootstrap_config,
    load_json_config,
    migrate_sqlite,
    shutdown_logging,
)


class ProductizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="fta_productization_")
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.app.mkdir()

    def tearDown(self) -> None:
        gc.collect()
        shutdown_logging()
        self.temp.cleanup()

    def test_fresh_databases_create_latest_schema(self):
        studio = ModelStudioService(self.app)
        inspection = InspectionRepository(self.app / "inspection" / "inspection.sqlite")
        self.assertEqual(studio.schema_version, MODEL_STUDIO_SCHEMA_VERSION)
        self.assertEqual(inspection.schema_version, INSPECTION_SCHEMA_VERSION)
        with studio.connect() as conn:
            self.assertEqual(conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], MODEL_STUDIO_SCHEMA_VERSION)

    def test_old_model_studio_schema_migrates_and_preserves_rows_with_backup(self):
        database = self.app / "model_studio" / "database" / "model_studio.sqlite"
        database.parent.mkdir(parents=True)
        with sqlite3.connect(database) as conn:
            conn.execute("CREATE TABLE datasets(dataset_id TEXT PRIMARY KEY,dataset_name TEXT NOT NULL,fruit_type TEXT,variety TEXT,description TEXT,created_at TEXT NOT NULL,storage_path TEXT NOT NULL)")
            conn.execute("INSERT INTO datasets VALUES('old','Old','','','', '2026-01-01','C:/old')")
        studio = ModelStudioService(self.app)
        self.assertEqual(studio.get_dataset("old")["dataset_name"], "Old")
        self.assertIsNotNone(studio.migration_backup_path)
        self.assertTrue(studio.migration_backup_path.exists())
        with studio.connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(datasets)")}
        self.assertIn("updated_at", columns)

    def test_latest_migration_is_a_noop(self):
        first = ModelStudioService(self.app)
        second = ModelStudioService(self.app)
        self.assertEqual(first.schema_version, second.schema_version)
        self.assertIsNone(second.migration_backup_path)

    def test_failed_migration_rolls_back_without_recording_version(self):
        conn = sqlite3.connect(":memory:")

        def broken(connection):
            connection.execute("CREATE TABLE should_not_survive(value TEXT)")
            raise RuntimeError("injected failure")

        with self.assertRaises(DatabaseMigrationError):
            migrate_sqlite(conn, database_name="test", migrations=[(1, broken)])
        self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='should_not_survive'").fetchone())
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 0)

    def test_restart_reconciles_training_and_inspection_work(self):
        studio = ModelStudioService(self.app)
        with studio.connect() as conn:
            conn.execute("INSERT INTO jobs(job_id,experiment_id,status,step,created_at) VALUES('j','e','Training','Training','2026-01-01')")
        restarted = ModelStudioService(self.app)
        self.assertEqual(restarted.get_job("j")["status"], "Interrupted")

        repository = InspectionRepository(self.app / "inspection" / "inspection.sqlite")
        repository.create({"inspection_id": "i", "sample_id": "s", "sample_name": "S", "status": "RUNNING", "software_version": "test"})
        reopened = InspectionRepository(repository.database_path)
        self.assertEqual(reopened.get("i")["status"], "FAILED_RECOVERABLE")

    def test_config_bootstrap_validation_and_corruption_are_explicit(self):
        example = self.root / "example.json"
        runtime = self.root / "runtime" / "policy.json"
        atomic_write_json(example, {"schema_version": 1, "enabled": True})
        bootstrap_config(example, runtime)
        self.assertEqual(load_json_config(runtime)["schema_version"], 1)
        atomic_write_json(runtime, {"schema_version": 9, "enabled": False})
        bootstrap_config(example, runtime)
        self.assertEqual(load_json_config(runtime)["schema_version"], 9)
        runtime.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeConfigurationError, "CONFIG_INVALID"):
            load_json_config(runtime)

    def test_corrupt_database_does_not_get_recreated(self):
        database = self.app / "model_studio" / "database" / "model_studio.sqlite"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"not sqlite")
        with self.assertRaisesRegex(ModelStudioError, "DATABASE_MIGRATION_FAILED"):
            ModelStudioService(self.app)
        self.assertEqual(database.read_bytes(), b"not sqlite")

    def test_health_endpoint_reports_software_readiness(self):
        static = self.app / "static"
        static.mkdir()
        server, port = start_backend(static, self.app / "outputs", self.app)
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["databases"]["modelStudio"])
            self.assertTrue(payload["databases"]["inspection"])
        finally:
            server.shutdown()
            server.server_close()

    def test_packaging_spec_keeps_scipy_and_new_runtime_modules(self):
        spec = (Path(__file__).resolve().parents[1] / "FruitTasteAnalyzer.spec").read_text(encoding="utf-8")
        self.assertNotIn("'scipy'", spec.split("excludes=")[1])
        for module in ("quality_algorithm.analysis_pipeline", "quality_algorithm.model_quality", "inspection.service", "model_studio.service", "training.train"):
            self.assertIn(module, spec)


if __name__ == "__main__":
    unittest.main()
