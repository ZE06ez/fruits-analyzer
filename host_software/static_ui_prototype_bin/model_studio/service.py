from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import threading
import time
import traceback
import uuid
import zipfile
from pathlib import Path

from runtime_support import (
    DatabaseMigrationError,
    RuntimeConfigurationError,
    RuntimePaths,
    backup_sqlite_database,
    atomic_write_json,
    bootstrap_config,
    load_json_config,
    migrate_sqlite,
)
from quality_algorithm.dataset import InsufficientTrainingDataset, read_labels_csv
from quality_algorithm.analysis_pipeline import FeaturePipelineConfig, run_feature_pipeline
from quality_algorithm.filters import expected_wavelengths, load_filter_config
from quality_algorithm.model_io import ModelInputMismatch, validate_model_metadata_contract, validate_production_model_metadata_contract
from quality_algorithm.model_quality import ModelQualityPolicy, evaluate_model_quality, resolve_quality_policy
from quality_algorithm.spectral_features import inspect_sample_structure
from training.train import train_one


TARGETS = {"ssc", "ta", "ph"}
MODEL_STUDIO_SCHEMA_VERSION = 2
MODEL_ALIASES = {"PLSR": "PLSR", "SVR": "SVR", "RF": "RF", "Random Forest": "RF"}
PREPROCESSING = {"RAW", "SNV", "MSC"}
EXCLUDE_REASONS = {
    "Image Blur",
    "Missing Band",
    "Calibration Error",
    "Label Error",
    "Damaged Fruit",
    "Outlier",
    "Capture Error",
    "Manual Exclusion",
    "Other",
}
PUBLISHED_STATUSES = {"Published", "Default", "Production"}
MODEL_STATUSES_VISIBLE_TO_STATION = tuple(sorted(PUBLISHED_STATUSES))
DATASET_PRODUCTION_REFERENCE_ERROR = "DATASET_HAS_PRODUCTION_MODEL_REFERENCES"
DATASET_SAMPLE_SCOPE_CONFLICT = "DATASET_SAMPLE_SCOPE_CONFLICT"


class ModelStudioError(RuntimeError):
    pass


class ModelStudioService:
    def __init__(self, app_dir: str | Path, *, quality_policy: ModelQualityPolicy | dict | None = None) -> None:
        self.app_dir = Path(app_dir).resolve()
        self.runtime_paths = RuntimePaths.for_app(self.app_dir)
        self.root = self.app_dir / "model_studio"
        development_layout = self.runtime_paths.root == self.app_dir
        self.data_dir = (self.app_dir / "model_studio_data") if development_layout else (self.runtime_paths.root / "model_studio_data")
        self.dataset_store_dir = self.data_dir / "datasets"
        self.database_dir = (self.root / "database") if development_layout else self.runtime_paths.database_dir
        self.artifact_dir = (self.root / "artifacts") if development_layout else (self.runtime_paths.root / "model_studio_artifacts")
        self.model_dir = (self.root / "models") if development_layout else (self.runtime_paths.root / "model_studio_models")
        self.production_dir = (self.app_dir / "trained_models") if development_layout else (self.runtime_paths.root / "trained_models")
        self.database_path = self.database_dir / "model_studio.sqlite"
        self.quality_policy_source = quality_policy if quality_policy is not None else self._load_quality_policy_config()
        self.feature_pipeline_config = FeaturePipelineConfig.production()
        self._lock = threading.Lock()
        self.database_dir.mkdir(parents=True, exist_ok=True)
        self.dataset_store_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.production_dir.mkdir(parents=True, exist_ok=True)
        self.migration_backup_path: Path | None = None
        self.init_db()
        self.reconcile_interrupted_jobs()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        existing = self.database_path.exists() and self.database_path.stat().st_size > 0
        if existing and self._schema_version_before_init() < MODEL_STUDIO_SCHEMA_VERSION:
            self.migration_backup_path = backup_sqlite_database(self.database_path, self.runtime_paths.root / "backups")
        try:
            with self.connect() as conn:
                conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS datasets (
                    dataset_id TEXT PRIMARY KEY,
                    dataset_name TEXT NOT NULL,
                    fruit_type TEXT,
                    variety TEXT,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    local_path TEXT,
                    import_source_path TEXT,
                    sample_count INTEGER DEFAULT 0,
                    label_count INTEGER DEFAULT 0,
                    enabled_wavelengths TEXT,
                    calibration_status TEXT DEFAULT 'missing',
                    dirty INTEGER DEFAULT 0,
                    latest_version_id TEXT
                );

                CREATE TABLE IF NOT EXISTS dataset_versions (
                    dataset_version_id TEXT PRIMARY KEY,
                    dataset_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    version_name TEXT NOT NULL,
                    sample_count INTEGER DEFAULT 0,
                    sample_ids TEXT NOT NULL,
                    label_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    created_by TEXT,
                    description TEXT,
                    parent_version TEXT,
                    sample_snapshot_json TEXT,
                    label_snapshot_json TEXT,
                    snapshot_hash TEXT NOT NULL,
                    UNIQUE(dataset_id, version)
                );

                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset_id TEXT NOT NULL,
                    sample_id TEXT NOT NULL,
                    fruit_type TEXT,
                    variety TEXT,
                    sample_name TEXT,
                    maturity TEXT,
                    weight_g REAL,
                    storage_path TEXT NOT NULL,
                    source_path TEXT,
                    local_path TEXT,
                    rgb_count INTEGER DEFAULT 0,
                    multispectral_count INTEGER DEFAULT 0,
                    dark_count INTEGER DEFAULT 0,
                    white_count INTEGER DEFAULT 0,
                    available_bands TEXT,
                    calibration_status TEXT,
                    ssc REAL,
                    ta REAL,
                    ph REAL,
                    data_status TEXT DEFAULT 'unknown',
                    capture_time TEXT,
                    quality_json TEXT,
                    feature_json TEXT,
                    include_status TEXT DEFAULT 'Included',
                    exclude_reason TEXT,
                    created_at TEXT NOT NULL,
                    imported_at TEXT,
                    UNIQUE(dataset_id, sample_id)
                );

                CREATE TABLE IF NOT EXISTS labels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset_id TEXT NOT NULL,
                    sample_id TEXT NOT NULL,
                    ssc REAL,
                    ta REAL,
                    ph REAL,
                    measurement_date TEXT,
                    instrument TEXT,
                    operator TEXT,
                    notes TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(dataset_id, sample_id)
                );

                CREATE TABLE IF NOT EXISTS training_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    dataset_id TEXT NOT NULL,
                    experiment_name TEXT NOT NULL,
                    target TEXT NOT NULL,
                    description TEXT,
                    models_json TEXT NOT NULL,
                    preprocessing_json TEXT NOT NULL,
                    validation_method TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dataset_version_id TEXT,
                    fruit_type TEXT,
                    variety TEXT,
                    parent_experiment_id TEXT,
                    parent_model_id TEXT,
                    feature_csv TEXT,
                    result_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    step TEXT NOT NULL,
                    progress INTEGER DEFAULT 0,
                    message TEXT,
                    logs_json TEXT,
                    result_json TEXT,
                    error TEXT,
                    cancel_requested INTEGER DEFAULT 0,
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS models (
                    model_id TEXT PRIMARY KEY,
                    experiment_id TEXT,
                    dataset_id TEXT,
                    model_name TEXT NOT NULL,
                    display_name TEXT,
                    target TEXT NOT NULL,
                    fruit_type TEXT,
                    variety TEXT,
                    model_type TEXT NOT NULL,
                    preprocessing TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    is_default INTEGER DEFAULT 0,
                    dataset_version_id TEXT,
                    dataset_version_label TEXT,
                    job_id TEXT,
                    parent_model_id TEXT,
                    description TEXT,
                    tags TEXT,
                    notes TEXT,
                    r2 REAL,
                    rmse REAL,
                    mae REAL,
                    rpd REAL,
                    model_dir TEXT NOT NULL,
                    metadata_json TEXT,
                    quality_report_json TEXT,
                    created_at TEXT NOT NULL,
                    published_at TEXT
                );

                CREATE TABLE IF NOT EXISTS operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    target TEXT,
                    resource_id TEXT,
                    message TEXT
                );

                """
            )
                self.schema_version = migrate_sqlite(
                    conn,
                    database_name="model_studio",
                    migrations=[(1, self._migrate_schema), (2, self._migration_v2)],
                )
        except (sqlite3.Error, DatabaseMigrationError) as exc:
            raise ModelStudioError(f"DATABASE_MIGRATION_FAILED: model_studio") from exc

    def _schema_version_before_init(self) -> int:
        try:
            with sqlite3.connect(self.database_path) as conn:
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
                return int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]) if exists else 0
        except sqlite3.Error as exc:
            raise ModelStudioError("DATABASE_MIGRATION_FAILED: model_studio") from exc

    @staticmethod
    def _migration_v2(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_models_scope ON models(target, fruit_type, variety, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        migrations = {
            "datasets": {
                "local_path": "TEXT",
                "import_source_path": "TEXT",
                "dirty": "INTEGER DEFAULT 0",
                "latest_version_id": "TEXT",
                "archived": "INTEGER DEFAULT 0",
                "updated_at": "TEXT",
            },
            "dataset_versions": {
                "sample_snapshot_json": "TEXT",
                "label_snapshot_json": "TEXT",
            },
            "samples": {
                "sample_name": "TEXT",
                "source_path": "TEXT",
                "local_path": "TEXT",
                "available_bands": "TEXT",
                "calibration_status": "TEXT",
                "include_status": "TEXT DEFAULT 'Included'",
                "exclude_reason": "TEXT",
                "imported_at": "TEXT",
            },
            "training_experiments": {
                "dataset_version_id": "TEXT",
                "fruit_type": "TEXT",
                "variety": "TEXT",
                "parent_experiment_id": "TEXT",
                "parent_model_id": "TEXT",
            },
            "jobs": {
                "run_number": "INTEGER DEFAULT 1",
                "dataset_version_id": "TEXT",
            },
                "models": {
                "display_name": "TEXT",
                "fruit_type": "TEXT",
                "variety": "TEXT",
                "is_default": "INTEGER DEFAULT 0",
                "dataset_version_id": "TEXT",
                "dataset_version_label": "TEXT",
                "job_id": "TEXT",
                "parent_model_id": "TEXT",
                "description": "TEXT",
                "tags": "TEXT",
                "notes": "TEXT",
                    "deleted_at": "TEXT",
                    "quality_report_json": "TEXT",
                },
        }
        for table, columns in migrations.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, ddl in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.execute("UPDATE samples SET include_status='Included' WHERE include_status IS NULL OR include_status=''")
        conn.execute("UPDATE samples SET local_path=storage_path WHERE local_path IS NULL OR local_path=''")
        conn.execute("UPDATE samples SET sample_name=sample_id WHERE sample_name IS NULL OR sample_name=''")
        conn.execute("UPDATE datasets SET local_path=storage_path WHERE local_path IS NULL OR local_path=''")
        conn.execute("UPDATE datasets SET dirty=COALESCE(dirty, 0)")
        conn.execute("UPDATE datasets SET archived=COALESCE(archived, 0)")
        conn.execute("UPDATE datasets SET updated_at=created_at WHERE updated_at IS NULL OR updated_at=''")
        conn.execute("UPDATE models SET display_name=model_name WHERE display_name IS NULL OR display_name=''")
        conn.execute("UPDATE models SET is_default=0 WHERE is_default IS NULL")

    def reconcile_interrupted_jobs(self) -> int:
        """Daemon worker threads cannot survive a restart, so never leave them RUNNING."""
        with self.connect() as conn:
            result = conn.execute(
                """
                UPDATE jobs SET status='Interrupted', step='Interrupted', progress=100,
                    message='Training interrupted by application restart',
                    error=COALESCE(error, 'PROCESS_RESTARTED'), finished_at=COALESCE(finished_at, ?)
                WHERE status IN ('Queued','Preparing','Training')
                """,
                (_now(),),
            )
        return int(result.rowcount)

    def dashboard(self) -> dict:
        with self.connect() as conn:
            dataset_count = conn.execute("SELECT COUNT(*) FROM datasets WHERE COALESCE(archived,0)=0").fetchone()[0]
            sample_count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            label_count = conn.execute(
                "SELECT COUNT(*) FROM labels WHERE ssc IS NOT NULL OR ta IS NOT NULL OR ph IS NOT NULL"
            ).fetchone()[0]
            experiment_count = conn.execute("SELECT COUNT(*) FROM training_experiments").fetchone()[0]
            version_count = conn.execute("SELECT COUNT(*) FROM dataset_versions").fetchone()[0]
            job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            published_count = conn.execute("SELECT COUNT(*) FROM models WHERE status IN ('Published','Default','Production')").fetchone()[0]
            default_count = conn.execute("SELECT COUNT(*) FROM models WHERE status='Default' OR is_default=1").fetchone()[0]
            review_count = conn.execute("SELECT COUNT(*) FROM models WHERE status='Candidate'").fetchone()[0]
            running_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('Queued','Preparing','Training')").fetchone()[0]
            dirty_datasets = conn.execute("SELECT COUNT(*) FROM datasets WHERE COALESCE(dirty,0)=1 AND COALESCE(archived,0)=0").fetchone()[0]
            failed_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='Failed'").fetchone()[0]
            production = [dict(row) for row in conn.execute(
                "SELECT * FROM models WHERE status IN ('Published','Default','Production') AND deleted_at IS NULL ORDER BY target, published_at DESC"
            )]
            recent_jobs = [dict(row) for row in conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 5")]
            recent_datasets = [dict(row) for row in conn.execute("SELECT * FROM datasets WHERE COALESCE(archived,0)=0 ORDER BY updated_at DESC, created_at DESC LIMIT 5")]
            candidates = [dict(row) for row in conn.execute("SELECT * FROM models WHERE status='Candidate' AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 8")]
        return {
            "databasePath": str(self.database_path),
            "counts": {
                "datasets": dataset_count,
                "datasetVersions": version_count,
                "samples": sample_count,
                "labels": label_count,
                "experiments": experiment_count,
                "trainingJobs": job_count,
                "publishedModels": published_count,
                "productionModels": published_count,
                "defaultModels": default_count,
                "modelsNeedingReview": review_count,
                "runningTraining": running_count,
                "dirtyDatasets": dirty_datasets,
                "failedTraining": failed_jobs,
            },
            "productionModels": production,
            "recentJobs": recent_jobs,
            "recentDatasets": recent_datasets,
            "needsAttention": self._dashboard_attention(dirty_datasets, failed_jobs, candidates),
            "filterConfig": [band.to_dict() for band in load_filter_config()],
        }

    def list_datasets(self, *, query: str = "", fruit_type: str = "", variety: str = "", dirty: str = "", archived: str = "") -> list[dict]:
        params: list[object] = []
        where = "WHERE 1=1"
        if query:
            where += " AND (dataset_name LIKE ? OR dataset_id LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])
        if fruit_type:
            where += " AND lower(COALESCE(fruit_type,''))=lower(?)"
            params.append(fruit_type)
        if variety:
            where += " AND lower(COALESCE(variety,'generic'))=lower(?)"
            params.append(_normalize_variety(variety))
        if dirty in {"0", "1"}:
            where += " AND COALESCE(dirty,0)=?"
            params.append(int(dirty))
        if archived in {"0", "1"}:
            where += " AND COALESCE(archived,0)=?"
            params.append(int(archived))
        elif archived != "all":
            where += " AND COALESCE(archived,0)=0"
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute(f"SELECT * FROM datasets {where} ORDER BY updated_at DESC, created_at DESC", params)]
            versions = {}
            for row in conn.execute("SELECT * FROM dataset_versions ORDER BY dataset_id, version DESC"):
                versions.setdefault(row["dataset_id"], []).append(dict(row))
            label_stats = {
                row["dataset_id"]: dict(row) for row in conn.execute(
                    """
                    SELECT dataset_id,
                      SUM(CASE WHEN ssc IS NOT NULL THEN 1 ELSE 0 END) AS ssc_count,
                      SUM(CASE WHEN ta IS NOT NULL THEN 1 ELSE 0 END) AS ta_count,
                      SUM(CASE WHEN ph IS NOT NULL THEN 1 ELSE 0 END) AS ph_count,
                      SUM(CASE WHEN include_status='Excluded' THEN 1 ELSE 0 END) AS excluded_count
                    FROM samples GROUP BY dataset_id
                    """
                )
            }
        for dataset in rows:
            dataset["versions"] = versions.get(dataset["dataset_id"], [])
            stats = label_stats.get(dataset["dataset_id"], {})
            sample_count = int(dataset.get("sample_count") or 0)
            dataset["workingSampleCount"] = sample_count
            dataset["excludedSampleCount"] = int(stats.get("excluded_count") or 0)
            dataset["labelCompleteness"] = {
                "ssc": int(stats.get("ssc_count") or 0),
                "ta": int(stats.get("ta_count") or 0),
                "ph": int(stats.get("ph_count") or 0),
                "sampleCount": sample_count,
            }
        return rows

    def create_dataset(self, payload: dict) -> dict:
        dataset_id = payload.get("dataset_id") or f"ds_{uuid.uuid4().hex[:10]}"
        name = (payload.get("dataset_name") or payload.get("datasetName") or "").strip()
        source_path = str(payload.get("storage_path") or payload.get("storagePath") or "").strip()
        source = Path(source_path).expanduser() if source_path else None
        if not name:
            raise ModelStudioError("dataset_name is required")
        if source and (not source.exists() or not source.is_dir()):
            raise ModelStudioError(f"dataset path does not exist: {source}")
        local_path = self._dataset_local_path(dataset_id)
        (local_path / "samples").mkdir(parents=True, exist_ok=True)
        labels_csv = local_path / "labels.csv"
        if not labels_csv.exists():
            self._write_labels_csv_file(labels_csv, [])
        now = _now()
        wavelengths = expected_wavelengths(load_filter_config())
        fruit_type = str(payload.get("fruit_type") or payload.get("fruitType") or "").strip()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO datasets(dataset_id,dataset_name,fruit_type,variety,description,created_at,updated_at,storage_path,local_path,import_source_path,enabled_wavelengths)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    dataset_id,
                    name,
                    fruit_type,
                    _normalize_variety(payload.get("variety") or ""),
                    payload.get("description") or "",
                    now,
                    now,
                    str(local_path),
                    str(local_path),
                    str(source) if source else "",
                    json.dumps(wavelengths),
                ),
            )
        self.log("dataset.create", "dataset", dataset_id, f"Dataset created: {name}")
        return self.get_dataset(dataset_id)

    def get_dataset(self, dataset_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
        if not row:
            raise ModelStudioError(f"dataset not found: {dataset_id}")
        return dict(row)

    def dataset_references(self, dataset_id: str) -> dict:
        dataset = self.get_dataset(dataset_id)
        with self.connect() as conn:
            samples = [dict(row) for row in conn.execute(
                "SELECT sample_id,sample_name,local_path,source_path,include_status FROM samples WHERE dataset_id=? ORDER BY sample_id",
                (dataset_id,),
            )]
            versions = [dict(row) for row in conn.execute(
                "SELECT dataset_version_id,version,version_name,sample_count,label_count,created_at FROM dataset_versions WHERE dataset_id=? ORDER BY version DESC",
                (dataset_id,),
            )]
            experiments = [dict(row) for row in conn.execute(
                "SELECT experiment_id,experiment_name,target,status,dataset_version_id,created_at FROM training_experiments WHERE dataset_id=? ORDER BY created_at DESC",
                (dataset_id,),
            )]
            experiment_ids = [row["experiment_id"] for row in experiments]
            jobs: list[dict] = []
            if experiment_ids:
                placeholders = ",".join("?" for _ in experiment_ids)
                jobs = [dict(row) for row in conn.execute(
                    f"SELECT job_id,experiment_id,status,step,created_at,finished_at FROM jobs WHERE experiment_id IN ({placeholders}) ORDER BY created_at DESC",
                    experiment_ids,
                )]
            models = [self._enrich_model(dict(row)) for row in conn.execute(
                """
                SELECT * FROM models
                WHERE deleted_at IS NULL
                  AND (dataset_id=? OR dataset_version_id IN (SELECT dataset_version_id FROM dataset_versions WHERE dataset_id=?))
                ORDER BY is_default DESC,status,created_at DESC
                """,
                (dataset_id, dataset_id),
            )]
        model_status_counts: dict[str, int] = {}
        for model in models:
            status = "Default" if model.get("isDefault") else model.get("status") or "Unknown"
            model_status_counts[status] = model_status_counts.get(status, 0) + 1
        blocking_models = [
            model for model in models
            if model.get("isDefault") or model.get("status") in PUBLISHED_STATUSES
        ]
        return {
            "dataset": dataset,
            "samples": samples,
            "versions": versions,
            "experiments": experiments,
            "jobs": jobs,
            "models": models,
            "summary": {
                "samples": len(samples),
                "versions": len(versions),
                "experiments": len(experiments),
                "jobs": len(jobs),
                "models": len(models),
                "modelStatusCounts": model_status_counts,
            },
            "blockingModels": blocking_models,
            "canDeletePermanently": not blocking_models,
            "blockCode": DATASET_PRODUCTION_REFERENCE_ERROR if blocking_models else "",
            "blockReason": "该数据集仍被已发布/默认模型引用，请先处理这些模型。" if blocking_models else "",
        }

    def archive_dataset(self, dataset_id: str) -> dict:
        dataset = self.get_dataset(dataset_id)
        with self.connect() as conn:
            conn.execute("UPDATE datasets SET archived=1, updated_at=? WHERE dataset_id=?", (_now(), dataset_id))
        self.log("dataset.archive", "dataset", dataset_id, f"Dataset archived: {dataset.get('dataset_name') or dataset_id}")
        return self.get_dataset(dataset_id)

    def delete_dataset_permanently(self, dataset_id: str, *, confirm: str = "") -> dict:
        refs = self.dataset_references(dataset_id)
        dataset = refs["dataset"]
        dataset_name = dataset.get("dataset_name") or dataset_id
        if confirm != dataset_name:
            raise ModelStudioError("permanent delete requires exact dataset name confirmation")
        if refs["blockingModels"]:
            raise ModelStudioError(DATASET_PRODUCTION_REFERENCE_ERROR)
        model_paths: list[Path] = []
        for model in refs["models"]:
            model_paths.extend(self._model_artifact_paths(model))
        artifact_paths = self._dataset_artifact_paths(dataset_id, refs["experiments"], refs["versions"])
        dataset_path = Path(dataset.get("local_path") or dataset.get("storage_path") or "")
        for path in _unique_paths([*model_paths, *artifact_paths]):
            if path.exists() and not (self._is_managed_model_path(path) or self._is_managed_artifact_path(path)):
                raise ModelStudioError(f"refusing to delete unmanaged artifact path: {path}")
        if dataset_path and dataset_path.exists():
            self._assert_managed_dataset_path(dataset_path)
        with self._lock:
            with self.connect() as conn:
                experiment_ids = [row["experiment_id"] for row in refs["experiments"]]
                model_ids = [row["model_id"] for row in refs["models"]]
                if experiment_ids:
                    placeholders = ",".join("?" for _ in experiment_ids)
                    conn.execute(f"DELETE FROM jobs WHERE experiment_id IN ({placeholders})", experiment_ids)
                if model_ids:
                    placeholders = ",".join("?" for _ in model_ids)
                    conn.execute(f"DELETE FROM models WHERE model_id IN ({placeholders})", model_ids)
                conn.execute("DELETE FROM training_experiments WHERE dataset_id=?", (dataset_id,))
                conn.execute("DELETE FROM dataset_versions WHERE dataset_id=?", (dataset_id,))
                conn.execute("DELETE FROM labels WHERE dataset_id=?", (dataset_id,))
                conn.execute("DELETE FROM samples WHERE dataset_id=?", (dataset_id,))
                conn.execute("DELETE FROM datasets WHERE dataset_id=?", (dataset_id,))
            deleted_paths: list[str] = []
            for path in _unique_paths([*model_paths, *artifact_paths]):
                if path.exists():
                    if self._is_managed_model_path(path):
                        self._delete_managed_model_path(path)
                    elif self._is_managed_artifact_path(path):
                        self._delete_managed_artifact_path(path)
                    else:
                        raise ModelStudioError(f"refusing to delete unmanaged artifact path: {path}")
                    deleted_paths.append(str(path))
            if dataset_path and dataset_path.exists():
                deleted_paths.append(str(self._delete_managed_dataset_path(dataset_path)))
        self.log("dataset.delete", "dataset", dataset_id, f"Dataset permanently deleted: {dataset_name}")
        return {
            "datasetId": dataset_id,
            "datasetName": dataset_name,
            "deleted": True,
            "deletedPaths": deleted_paths,
            "preservedSourcePath": dataset.get("import_source_path") or "",
            "summary": refs["summary"],
        }

    def validate_sample_folder(self, source_path: str | Path) -> dict:
        root = Path(source_path).expanduser()
        if not root.exists() or not root.is_dir():
            raise ModelStudioError(f"sample path does not exist: {root}")
        reports = [self._sample_import_report(sample_dir) for sample_dir in self._sample_dirs(root)]
        if not reports:
            raise ModelStudioError(f"no sample folders found: {root}")
        status = "Valid"
        if any(item["status"] == "Invalid" for item in reports):
            status = "Invalid"
        elif any(item["status"] == "Warning" for item in reports):
            status = "Warning"
        return {"sourcePath": str(root), "status": status, "samples": reports}

    def import_samples(self, dataset_id: str, source_path: str | Path | None = None, duplicate_policy: str = "skip") -> dict:
        dataset = self.get_dataset(dataset_id)
        default_source = dataset.get("import_source_path") or ""
        root = Path(source_path).expanduser() if source_path else Path(default_source or dataset["storage_path"])
        if not root.exists() or not root.is_dir():
            raise ModelStudioError(f"sample path does not exist: {root}")
        duplicate_policy = duplicate_policy if duplicate_policy in {"skip", "replace", "new", "cancel"} else "skip"
        local_root = Path(dataset.get("local_path") or dataset["storage_path"]).expanduser()
        samples_root = local_root / "samples"
        samples_root.mkdir(parents=True, exist_ok=True)
        sample_dirs = self._sample_dirs(root)
        import_reports = [(sample_dir, self._sample_import_report(sample_dir)) for sample_dir in sample_dirs]
        identities = [
            self._sample_identity_from_report(sample_dir, import_report, dataset)
            for sample_dir, import_report in import_reports
            if import_report["status"] != "Invalid"
        ]
        scope_info = self._resolve_dataset_scope_for_import(dataset, identities)
        dataset = scope_info["dataset"]
        imported = 0
        new_count = 0
        existing_count = 0
        conflicts = 0
        skipped = 0
        warnings: list[str] = []
        duplicates: list[dict] = []
        calibration_statuses: list[str] = []
        for sample_dir, import_report in import_reports:
            report = import_report["structure"]
            if import_report["status"] == "Invalid":
                skipped += 1
                warnings.append(f"{sample_dir.name}: " + "; ".join(import_report.get("warnings") or ["invalid sample folder"]))
                continue
            identity = self._sample_identity_from_report(sample_dir, import_report, dataset)
            source_sample_id = identity["sample_id"]
            duplicate = self._find_duplicate_sample(dataset_id, source_sample_id, sample_dir)
            if duplicate:
                conflicts += 1
                existing_count += 1
                duplicates.append({
                    "sampleId": duplicate.get("sample_id"),
                    "sourcePath": duplicate.get("source_path") or "",
                    "localPath": duplicate.get("local_path") or duplicate.get("storage_path") or "",
                })
                if duplicate_policy == "cancel":
                    raise ModelStudioError(f"sample already exists: {source_sample_id}")
                if duplicate_policy == "skip":
                    skipped += 1
                    continue
            replacing = bool(duplicate and duplicate_policy == "replace")
            if replacing:
                refs = self.sample_references(dataset_id, duplicate["sample_id"])
                if refs["blocked"]:
                    raise ModelStudioError(f"sample is referenced by historical artifacts and cannot be replaced: {duplicate['sample_id']}")
            sample_id = source_sample_id if not duplicate else (duplicate["sample_id"] if replacing else self._unique_sample_id(dataset_id, source_sample_id))
            local_sample_dir = Path(duplicate.get("local_path") or duplicate.get("storage_path")) if replacing else self._unique_sample_path(samples_root, sample_id)
            if replacing and local_sample_dir.exists():
                local_root = (Path(dataset.get("local_path") or dataset["storage_path"]) / "samples").resolve()
                resolved = local_sample_dir.resolve()
                if local_root == resolved or local_root not in resolved.parents:
                    raise ModelStudioError("refusing to replace a path outside the managed dataset samples folder")
                shutil.rmtree(resolved)
            shutil.copytree(sample_dir, local_sample_dir)
            calibration_statuses.append(str(report["calibration_status"]))
            row = {
                "dataset_id": dataset_id,
                "sample_id": sample_id,
                "sample_name": identity["sample_name"],
                "fruit_type": identity["fruit_type"],
                "variety": identity["variety"],
                "storage_path": str(local_sample_dir),
                "source_path": str(sample_dir),
                "local_path": str(local_sample_dir),
                "rgb_count": int(report["rgb_count"]),
                "multispectral_count": int(report["multispectral_count"]),
                "dark_count": _calibration_count(sample_dir, "dark"),
                "white_count": _calibration_count(sample_dir, "white"),
                "available_bands": json.dumps(report.get("available_bands") or []),
                "calibration_status": str(report["calibration_status"]),
                "data_status": "complete" if report["complete"] else "incomplete",
                "capture_time": identity["capture_time"],
                "quality_json": json.dumps(import_report, ensure_ascii=False),
                "created_at": _now(),
                "imported_at": _now(),
            }
            if import_report["status"] != "Valid":
                warnings.append(f"{sample_dir.name}: " + "; ".join(import_report.get("warnings") or []))
            self._upsert_sample(row)
            imported += 1
            new_count += 0 if replacing else 1
        self._refresh_dataset_counts(dataset_id, calibration_statuses)
        self._mark_dataset_dirty(dataset_id)
        self.log("samples.import", "dataset", dataset_id, f"Imported {imported} samples with duplicate_policy={duplicate_policy}")
        return {
            "dataset": self.get_dataset(dataset_id),
            "imported": imported,
            "newSamples": new_count,
            "existingSamples": existing_count,
            "conflicts": conflicts,
            "skipped": skipped,
            "duplicates": duplicates[:50],
            "warnings": warnings[:50],
            "scope": scope_info["scope"],
        }

    def import_labels(self, dataset_id: str, labels_csv: str | Path) -> dict:
        labels = read_labels_csv(labels_csv)
        duplicate_count = 0
        now = _now()
        with self.connect() as conn:
            for sample_id, label in labels.items():
                existing = conn.execute("SELECT id FROM labels WHERE dataset_id=? AND sample_id=?", (dataset_id, sample_id)).fetchone()
                duplicate_count += 1 if existing else 0
                conn.execute(
                    """
                    INSERT INTO labels(dataset_id,sample_id,ssc,ta,ph,updated_at)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(dataset_id,sample_id) DO UPDATE SET
                      ssc=excluded.ssc, ta=excluded.ta, ph=excluded.ph, updated_at=excluded.updated_at
                    """,
                    (dataset_id, sample_id, label.ssc, label.ta, label.ph, now),
                )
                conn.execute(
                    "UPDATE samples SET ssc=?, ta=?, ph=? WHERE dataset_id=? AND sample_id=?",
                    (label.ssc, label.ta, label.ph, dataset_id, sample_id),
                )
        self._refresh_dataset_counts(dataset_id)
        self._mark_dataset_dirty(dataset_id)
        self._write_dataset_labels_csv(dataset_id)
        self.log("labels.import", "dataset", dataset_id, f"Imported {len(labels)} labels")
        return {"imported": len(labels), "duplicates": duplicate_count, "dataset": self.get_dataset(dataset_id)}

    def save_sample_label(self, dataset_id: str, sample_id: str, payload: dict) -> dict:
        values = {
            "ssc": _optional_float(payload.get("ssc")),
            "ta": _optional_float(payload.get("ta")),
            "ph": _optional_float(payload.get("ph")),
        }
        now = _now()
        with self.connect() as conn:
            sample = conn.execute(
                "SELECT sample_id FROM samples WHERE dataset_id=? AND sample_id=?",
                (dataset_id, sample_id),
            ).fetchone()
            if not sample:
                raise ModelStudioError(f"sample not found: {sample_id}")
            conn.execute(
                """
                INSERT INTO labels(dataset_id,sample_id,ssc,ta,ph,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(dataset_id,sample_id) DO UPDATE SET
                  ssc=excluded.ssc, ta=excluded.ta, ph=excluded.ph, updated_at=excluded.updated_at
                """,
                (dataset_id, sample_id, values["ssc"], values["ta"], values["ph"], now),
            )
            conn.execute(
                "UPDATE samples SET ssc=?, ta=?, ph=? WHERE dataset_id=? AND sample_id=?",
                (values["ssc"], values["ta"], values["ph"], dataset_id, sample_id),
            )
        self._refresh_dataset_counts(dataset_id)
        self._mark_dataset_dirty(dataset_id)
        self._write_dataset_labels_csv(dataset_id)
        self.log("labels.save", "sample", sample_id, f"Label saved for {sample_id}")
        return self.get_sample(dataset_id, sample_id)

    def delete_sample(self, dataset_id: str, sample_id: str, *, delete_local_copy: bool = False) -> dict:
        sample = self.get_sample(dataset_id, sample_id)
        dataset = self.get_dataset(dataset_id)
        local_path = Path(sample.get("local_path") or sample.get("storage_path") or "")
        source_path = Path(sample.get("source_path") or "") if sample.get("source_path") else None
        refs = self.sample_references(dataset_id, sample_id)
        if refs["blocked"]:
            raise ModelStudioError("sample is referenced by Dataset Version, Experiment, or Model lineage; exclude it instead of permanent delete")
        with self.connect() as conn:
            conn.execute("DELETE FROM labels WHERE dataset_id=? AND sample_id=?", (dataset_id, sample_id))
            conn.execute("DELETE FROM samples WHERE dataset_id=? AND sample_id=?", (dataset_id, sample_id))
        local_deleted = False
        if delete_local_copy and local_path:
            local_root = (Path(dataset.get("local_path") or dataset["storage_path"]) / "samples").resolve()
            resolved = local_path.resolve()
            if local_root == resolved or local_root not in resolved.parents:
                raise ModelStudioError("refusing to delete a path outside the managed dataset samples folder")
            shutil.rmtree(resolved, ignore_errors=True)
            local_deleted = True
        self._refresh_dataset_counts(dataset_id)
        self._mark_dataset_dirty(dataset_id)
        self._write_dataset_labels_csv(dataset_id)
        self.log("samples.delete", "sample", sample_id, f"Deleted sample record; local_deleted={local_deleted}")
        return {
            "dataset": self.get_dataset(dataset_id),
            "sampleId": sample_id,
            "localDeleted": local_deleted,
            "sourcePath": str(source_path) if source_path else "",
            "sourceExists": bool(source_path and source_path.exists()),
        }

    def list_samples(self, dataset_id: str, *, limit: int = 50, offset: int = 0, query: str = "") -> dict:
        params: list[object] = [dataset_id]
        where = "WHERE dataset_id=?"
        if query:
            where += " AND sample_id LIKE ?"
            params.append(f"%{query}%")
        with self.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM samples {where}", params).fetchone()[0]
            rows = [dict(row) for row in conn.execute(
                f"SELECT * FROM samples {where} ORDER BY sample_id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            )]
        for row in rows:
            row["label_status"] = _label_status(row)
        return {"total": total, "items": rows, "limit": limit, "offset": offset}

    def filter_samples(
        self,
        dataset_id: str,
        *,
        limit: int = 80,
        offset: int = 0,
        query: str = "",
        include_status: str = "",
        label_status: str = "",
        calibration: str = "",
        quality: str = "",
    ) -> dict:
        params: list[object] = [dataset_id]
        where = "WHERE dataset_id=?"
        if query:
            where += " AND (sample_id LIKE ? OR COALESCE(sample_name,'') LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])
        if include_status and include_status != "all":
            where += " AND include_status=?"
            params.append(include_status)
        if calibration and calibration != "all":
            where += " AND COALESCE(calibration_status,'')=?"
            params.append(calibration)
        if quality and quality != "all":
            where += " AND COALESCE(data_status,'')=?"
            params.append(quality)
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                f"SELECT * FROM samples {where} ORDER BY sample_id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            )]
        filtered = []
        for row in rows:
            row["label_status"] = _label_status(row)
            if label_status and label_status != "all" and row["label_status"] != label_status:
                continue
            filtered.append(row)
        return {"total": len(filtered), "items": filtered, "limit": limit, "offset": offset}

    def sample_references(self, dataset_id: str, sample_id: str) -> dict:
        refs = {"versions": [], "experiments": [], "models": []}
        with self.connect() as conn:
            for row in conn.execute("SELECT dataset_version_id,version_name,sample_ids FROM dataset_versions WHERE dataset_id=?", (dataset_id,)):
                ids = json.loads(row["sample_ids"] or "[]")
                if sample_id in ids:
                    refs["versions"].append({"datasetVersionId": row["dataset_version_id"], "versionName": row["version_name"]})
            version_ids = [item["datasetVersionId"] for item in refs["versions"]]
            if version_ids:
                placeholders = ",".join("?" for _ in version_ids)
                refs["experiments"] = [dict(row) for row in conn.execute(
                    f"SELECT experiment_id,experiment_name,target,dataset_version_id FROM training_experiments WHERE dataset_version_id IN ({placeholders})",
                    version_ids,
                )]
                refs["models"] = [dict(row) for row in conn.execute(
                    f"SELECT model_id,display_name,model_name,target,status,dataset_version_id FROM models WHERE dataset_version_id IN ({placeholders}) AND deleted_at IS NULL",
                    version_ids,
                )]
        refs["blocked"] = bool(refs["versions"] or refs["experiments"] or refs["models"])
        return refs

    def get_sample(self, dataset_id: str, sample_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM samples WHERE dataset_id=? AND sample_id=?",
                (dataset_id, sample_id),
            ).fetchone()
        if not row:
            raise ModelStudioError(f"sample not found: {sample_id}")
        sample = dict(row)
        sample["label_status"] = _label_status(sample)
        sample["quality"] = json.loads(sample.get("quality_json") or "{}")
        return sample

    def quality_report(self, dataset_id: str) -> dict:
        with self.connect() as conn:
            samples = [dict(row) for row in conn.execute("SELECT * FROM samples WHERE dataset_id=?", (dataset_id,))]
        missing_bands = []
        missing_calibration = []
        missing_ssc = []
        missing_ta = []
        missing_ph = []
        broken = []
        excluded = []
        needs_review = []
        for sample in samples:
            quality = json.loads(sample.get("quality_json") or "{}")
            if sample.get("include_status") == "Excluded":
                excluded.append({"sample_id": sample["sample_id"], "reason": sample.get("exclude_reason") or ""})
            if sample.get("include_status") == "Needs Review":
                needs_review.append({"sample_id": sample["sample_id"], "reason": sample.get("exclude_reason") or ""})
            if quality.get("missing_bands"):
                missing_bands.append({"sample_id": sample["sample_id"], "bands": quality["missing_bands"]})
            if quality.get("calibration_status") != "complete":
                missing_calibration.append(sample["sample_id"])
            if sample.get("ssc") is None:
                missing_ssc.append(sample["sample_id"])
            if sample.get("ta") is None:
                missing_ta.append(sample["sample_id"])
            if sample.get("ph") is None:
                missing_ph.append(sample["sample_id"])
            if quality.get("bad_images"):
                broken.append({"sample_id": sample["sample_id"], "files": quality["bad_images"]})
        return {
            "sampleCount": len(samples),
            "completeSamples": sum(1 for item in samples if item.get("data_status") == "complete"),
            "missingBands": missing_bands,
            "missingCalibration": missing_calibration,
            "missingSSC": missing_ssc,
            "missingTA": missing_ta,
            "missingPH": missing_ph,
            "brokenImages": broken,
            "excluded": excluded,
            "needsReview": needs_review,
        }

    def generate_features(self, dataset_id: str, dataset_version_id: str | None = None, pipeline_config: FeaturePipelineConfig | dict | None = None) -> dict:
        version = self.resolve_dataset_version(dataset_id, dataset_version_id)
        dataset_id = version["dataset_id"]
        wavelengths = expected_wavelengths(load_filter_config())
        config = pipeline_config if isinstance(pipeline_config, FeaturePipelineConfig) else FeaturePipelineConfig.from_dict(pipeline_config) if pipeline_config else self.feature_pipeline_config
        feature_dir = self.artifact_dir / "features"
        feature_dir.mkdir(parents=True, exist_ok=True)
        output_csv = feature_dir / f"{version['dataset_version_id']}_features.csv"
        rows = []
        failures = []
        model_input_contract: dict | None = None
        pipeline_signature = ""
        sample_ids = json.loads(version["sample_ids"] or "[]")
        samples = json.loads(version.get("sample_snapshot_json") or "[]")
        if not samples:
            with self.connect() as conn:
                if sample_ids:
                    placeholders = ",".join("?" for _ in sample_ids)
                    samples = [dict(row) for row in conn.execute(
                        f"SELECT * FROM samples WHERE dataset_id=? AND sample_id IN ({placeholders}) ORDER BY sample_id",
                        [dataset_id, *sample_ids],
                    )]
                else:
                    samples = []
        for sample in samples:
            if sample.get("include_status") == "Excluded":
                continue
            try:
                sample_path = sample.get("local_path") or sample.get("storage_path")
                record = run_feature_pipeline(sample_path, sample_id=sample["sample_id"], config=config)
                if not isinstance(record.model_input_contract, dict) or not record.pipeline_signature:
                    raise ModelStudioError("MODEL_INPUT_CONTRACT_MISSING: feature pipeline did not return model input contract")
                if pipeline_signature and record.pipeline_signature != pipeline_signature:
                    raise ModelStudioError("MODEL_INPUT_MISMATCH: training sample pipeline signature mismatch")
                if not pipeline_signature:
                    pipeline_signature = record.pipeline_signature
                    model_input_contract = record.model_input_contract
                row = {"sample_id": record.sample_id}
                for wavelength, value in zip(record.wavelengths, record.features):
                    row[f"R{wavelength}"] = value
                row.update({"ssc": sample.get("ssc"), "ta": sample.get("ta"), "ph": sample.get("ph")})
                rows.append(row)
                self._update_sample_feature(dataset_id, sample["sample_id"], record.to_dict())
            except Exception as exc:
                failures.append({"sample_id": sample["sample_id"], "error": str(exc)})
        if not rows:
            raise InsufficientTrainingDataset("Insufficient training dataset")
        fields = ["sample_id"] + [f"R{w}" for w in wavelengths] + ["ssc", "ta", "ph"]
        with output_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fields})
        self.log("features.generate", "dataset_version", version["dataset_version_id"], f"Generated features: {output_csv}")
        return {
            "featureCsv": str(output_csv),
            "rows": len(rows),
            "failures": failures[:50],
            "wavelengths": wavelengths,
            "datasetVersionId": version["dataset_version_id"],
            "datasetVersion": version["version_name"],
            "pipelineConfig": config.to_dict(),
            "modelInputContract": model_input_contract or {},
            "pipelineSignature": pipeline_signature,
        }

    def list_dataset_versions(self, dataset_id: str) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM dataset_versions WHERE dataset_id=? ORDER BY version DESC",
                (dataset_id,),
            )]

    def create_dataset_version(self, dataset_id: str, description: str = "", created_by: str = "local") -> dict:
        dataset = self.get_dataset(dataset_id)
        with self.connect() as conn:
            samples = [dict(row) for row in conn.execute(
                """
                SELECT sample_id,storage_path,source_path,local_path,include_status,ssc,ta,ph
                ,fruit_type,variety,sample_name
                FROM samples
                WHERE dataset_id=? AND include_status!='Excluded'
                ORDER BY sample_id
                """,
                (dataset_id,),
            )]
            labels = {
                row["sample_id"]: dict(row) for row in conn.execute(
                    "SELECT sample_id,ssc,ta,ph,updated_at FROM labels WHERE dataset_id=? ORDER BY sample_id",
                    (dataset_id,),
                )
            }
            latest = conn.execute("SELECT * FROM dataset_versions WHERE dataset_id=? ORDER BY version DESC LIMIT 1", (dataset_id,)).fetchone()
            version_no = int(latest["version"]) + 1 if latest else 1
            parent_version = latest["dataset_version_id"] if latest else None
            sample_ids = [row["sample_id"] for row in samples]
            label_count = sum(
                1 for sample_id in sample_ids
                if labels.get(sample_id) and any(labels[sample_id].get(target) is not None for target in ("ssc", "ta", "ph"))
            )
            sample_snapshot = []
            label_snapshot = {}
            for sample in samples:
                sample_id = sample["sample_id"]
                label = labels.get(sample_id) or {
                    "sample_id": sample_id,
                    "ssc": sample.get("ssc"),
                    "ta": sample.get("ta"),
                    "ph": sample.get("ph"),
                    "updated_at": "",
                }
                sample_snapshot.append({
                    "sample_id": sample_id,
                    "storage_path": sample.get("local_path") or sample.get("storage_path") or "",
                    "local_path": sample.get("local_path") or sample.get("storage_path") or "",
                    "source_path": sample.get("source_path") or "",
                    "sample_name": sample.get("sample_name") or sample_id,
                    "fruit_type": sample.get("fruit_type") or dataset.get("fruit_type") or "",
                    "variety": _normalize_variety(sample.get("variety") or dataset.get("variety") or ""),
                    "ssc": label.get("ssc"),
                    "ta": label.get("ta"),
                    "ph": label.get("ph"),
                })
                label_snapshot[sample_id] = {
                    "ssc": label.get("ssc"),
                    "ta": label.get("ta"),
                    "ph": label.get("ph"),
                    "updated_at": label.get("updated_at") or "",
                }
            sample_snapshot_json = json.dumps(sample_snapshot, ensure_ascii=False, sort_keys=True)
            label_snapshot_json = json.dumps(label_snapshot, ensure_ascii=False, sort_keys=True)
            snapshot_hash = _snapshot_hash(dataset_id, sample_snapshot_json, label_snapshot_json)
            version_id = f"dsv_{uuid.uuid4().hex[:10]}"
            version_name = f"Dataset V{version_no}"
            conn.execute(
                """
                INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,version_name,sample_count,sample_ids,label_count,created_at,created_by,description,parent_version,sample_snapshot_json,label_snapshot_json,snapshot_hash)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    version_id,
                    dataset_id,
                    version_no,
                    version_name,
                    len(sample_ids),
                    json.dumps(sample_ids, ensure_ascii=False),
                    label_count,
                    _now(),
                    created_by,
                    description,
                    parent_version,
                    sample_snapshot_json,
                    label_snapshot_json,
                    snapshot_hash,
                ),
            )
            conn.execute("UPDATE datasets SET dirty=0, latest_version_id=?, updated_at=? WHERE dataset_id=?", (version_id, _now(), dataset_id))
        self.log("dataset.version.create", "dataset", dataset_id, f"{dataset['dataset_name']} {version_name} created")
        return self.get_dataset_version(version_id)

    def get_dataset_version(self, dataset_version_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM dataset_versions WHERE dataset_version_id=?", (dataset_version_id,)).fetchone()
        if not row:
            raise ModelStudioError(f"dataset version not found: {dataset_version_id}")
        return dict(row)

    def dataset_version_diff(self, from_version_id: str, to_version_id: str) -> dict:
        before = self.get_dataset_version(from_version_id)
        after = self.get_dataset_version(to_version_id)
        before_samples = {
            item["sample_id"]: item for item in json.loads(before.get("sample_snapshot_json") or "[]")
            if isinstance(item, dict) and item.get("sample_id")
        }
        after_samples = {
            item["sample_id"]: item for item in json.loads(after.get("sample_snapshot_json") or "[]")
            if isinstance(item, dict) and item.get("sample_id")
        }
        before_labels = json.loads(before.get("label_snapshot_json") or "{}")
        after_labels = json.loads(after.get("label_snapshot_json") or "{}")
        added = sorted(set(after_samples) - set(before_samples))
        removed = sorted(set(before_samples) - set(after_samples))
        label_changes = []
        for sample_id in sorted(set(before_labels) & set(after_labels)):
            old = before_labels.get(sample_id) or {}
            new = after_labels.get(sample_id) or {}
            changes = {}
            for target in ("ssc", "ta", "ph"):
                if old.get(target) != new.get(target):
                    changes[target] = {"from": old.get(target), "to": new.get(target)}
            if changes:
                label_changes.append({"sampleId": sample_id, "changes": changes})
        return {
            "from": {"datasetVersionId": before["dataset_version_id"], "versionName": before["version_name"]},
            "to": {"datasetVersionId": after["dataset_version_id"], "versionName": after["version_name"]},
            "summary": {
                "addedSamples": len(added),
                "removedOrExcludedSamples": len(removed),
                "changedLabels": len(label_changes),
            },
            "addedSamples": added,
            "removedOrExcludedSamples": removed,
            "labelChanges": label_changes,
        }

    def resolve_dataset_version(self, dataset_id: str, dataset_version_id: str | None = None) -> dict:
        if dataset_version_id:
            return self.get_dataset_version(dataset_version_id)
        dataset = self.get_dataset(dataset_id)
        latest = dataset.get("latest_version_id")
        if latest:
            return self.get_dataset_version(latest)
        return self.create_dataset_version(dataset_id, "Initial training snapshot")

    def update_sample_status(self, dataset_id: str, sample_id: str, include_status: str, reason: str = "") -> dict:
        status = include_status if include_status in {"Included", "Excluded", "Needs Review"} else "Included"
        if status == "Excluded" and reason and reason not in EXCLUDE_REASONS:
            reason = "Other"
        with self.connect() as conn:
            conn.execute(
                "UPDATE samples SET include_status=?, exclude_reason=? WHERE dataset_id=? AND sample_id=?",
                (status, reason, dataset_id, sample_id),
            )
        self._mark_dataset_dirty(dataset_id)
        self.log("sample.status", "sample", sample_id, f"{status}: {reason}")
        return self.list_samples(dataset_id, query=sample_id, limit=1)["items"][0]

    def create_experiment(self, payload: dict) -> dict:
        dataset_id = payload.get("dataset_id") or payload.get("datasetId")
        dataset = self.get_dataset(dataset_id)
        dataset_version_id = payload.get("dataset_version_id") or payload.get("datasetVersionId")
        version = self.resolve_dataset_version(dataset_id, dataset_version_id)
        target = str(payload.get("target") or "").lower()
        if target not in TARGETS:
            raise ModelStudioError("target must be ssc, ta or ph")
        models = _normalize_models(payload.get("models") or ["PLSR", "SVR"])
        preprocessing = _normalize_preprocessing(payload.get("preprocessing") or ["RAW", "SNV", "MSC"])
        experiment_id = payload.get("experiment_id") or f"exp_{uuid.uuid4().hex[:10]}"
        name = (payload.get("experiment_name") or payload.get("experimentName") or f"{target.upper()}_{time.strftime('%Y%m%d_%H%M%S')}").strip()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO training_experiments(experiment_id,dataset_id,experiment_name,target,description,models_json,preprocessing_json,validation_method,status,dataset_version_id,fruit_type,variety,parent_experiment_id,parent_model_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    experiment_id,
                    dataset_id,
                    name,
                    target,
                    payload.get("description") or "",
                    json.dumps(models),
                    json.dumps(preprocessing),
                    payload.get("validation_method") or payload.get("validationMethod") or "GroupKFold",
                    "Created",
                    version["dataset_version_id"],
                    dataset.get("fruit_type") or "",
                    _normalize_variety(dataset.get("variety") or ""),
                    payload.get("parent_experiment_id") or payload.get("parentExperimentId") or None,
                    payload.get("parent_model_id") or payload.get("parentModelId") or None,
                    _now(),
                ),
            )
        self.log("experiment.create", "experiment", experiment_id, f"Experiment created: {name}")
        return self.get_experiment(experiment_id)

    def create_experiment_and_training_job(self, payload: dict) -> dict:
        experiment = self.create_experiment(payload)
        job = self.create_training_job(experiment["experiment_id"])
        return {"experiment": experiment, "job": job}

    def clone_experiment(self, experiment_id: str, name: str | None = None) -> dict:
        source = self.get_experiment(experiment_id)
        payload = {
            "datasetId": source["dataset_id"],
            "datasetVersionId": source.get("dataset_version_id"),
            "experimentName": name or f"{source['experiment_name']} Copy",
            "target": source["target"],
            "description": source.get("description") or "",
            "models": source["models"],
            "preprocessing": source["preprocessing"],
            "validationMethod": source["validation_method"],
            "parentExperimentId": experiment_id,
        }
        return self.create_experiment(payload)

    def retrain_from_model(self, model_id: str, dataset_version_id: str | None = None, name: str | None = None) -> dict:
        model = self.get_model(model_id)
        dataset_id = model["dataset_id"]
        version = self.resolve_dataset_version(dataset_id, dataset_version_id)
        payload = {
            "datasetId": dataset_id,
            "datasetVersionId": version["dataset_version_id"],
            "experimentName": name or f"Retrain {model.get('display_name') or model['model_name']}",
            "target": model["target"],
            "description": f"Retrain from {model_id}",
            "models": [model["model_type"]],
            "preprocessing": [model["preprocessing"]],
            "validationMethod": "GroupKFold",
            "parentModelId": model_id,
        }
        return self.create_experiment(payload)

    def list_experiments(self) -> list[dict]:
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM training_experiments ORDER BY created_at DESC")]
            jobs = {}
            for row in conn.execute("SELECT * FROM jobs ORDER BY created_at DESC"):
                jobs.setdefault(row["experiment_id"], []).append(self._decode_job(dict(row)))
        for row in rows:
            row["models"] = json.loads(row.pop("models_json") or "[]")
            row["preprocessing"] = json.loads(row.pop("preprocessing_json") or "[]")
            row["runs"] = jobs.get(row["experiment_id"], [])
            row["runCount"] = len(row["runs"])
            row["variantCount"] = len(row["models"]) * len(row["preprocessing"])
        return rows

    def get_experiment(self, experiment_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM training_experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
        if not row:
            raise ModelStudioError(f"experiment not found: {experiment_id}")
        result = dict(row)
        result["models"] = json.loads(result.pop("models_json") or "[]")
        result["preprocessing"] = json.loads(result.pop("preprocessing_json") or "[]")
        result["result"] = json.loads(result.pop("result_json") or "null")
        return result

    def create_training_job(self, experiment_id: str) -> dict:
        experiment = self.get_experiment(experiment_id)
        self._validate_experiment_training_inputs(experiment)
        job_id = f"job_{uuid.uuid4().hex[:10]}"
        with self.connect() as conn:
            last_run = conn.execute("SELECT MAX(run_number) FROM jobs WHERE experiment_id=?", (experiment_id,)).fetchone()[0] or 0
            conn.execute(
                """
                INSERT INTO jobs(job_id,experiment_id,status,step,progress,message,logs_json,run_number,dataset_version_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    experiment_id,
                    "Queued",
                    "Queued",
                    0,
                    "Training job queued",
                    json.dumps([]),
                    int(last_run) + 1,
                    experiment.get("dataset_version_id"),
                    _now(),
                ),
            )
            conn.execute("UPDATE training_experiments SET status='Queued' WHERE experiment_id=?", (experiment_id,))
        thread = threading.Thread(target=self._run_training_job, args=(job_id,), daemon=True)
        thread.start()
        self.log("training.start", "job", job_id, f"Training started for {experiment['experiment_name']}")
        return self.get_job(job_id)

    def _validate_experiment_training_inputs(self, experiment: dict) -> None:
        version = self.get_dataset_version(experiment["dataset_version_id"])
        sample_count = int(version.get("sample_count") or 0)
        if sample_count <= 0:
            raise ModelStudioError("Dataset Version 里没有样品。请先导入样品，保存标签，然后创建新的 Dataset Version 再训练。")
        target = str(experiment.get("target") or "").lower()
        labels = json.loads(version.get("label_snapshot_json") or "{}")
        labeled_count = sum(
            1
            for values in labels.values()
            if isinstance(values, dict) and values.get(target) is not None
        )
        if labeled_count <= 0:
            raise ModelStudioError(f"Dataset Version 里没有 {target.upper()} 标签。请先保存该指标标签，然后创建新的 Dataset Version 再训练。")

    def list_jobs(self) -> list[dict]:
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM jobs ORDER BY created_at DESC")]
        return [self._decode_job(row) for row in rows]

    def get_job(self, job_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise ModelStudioError(f"job not found: {job_id}")
        return self._decode_job(dict(row))

    def cancel_job(self, job_id: str) -> dict:
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET cancel_requested=1,status='Cancelled',message='Cancel requested' WHERE job_id=?", (job_id,))
        return self.get_job(job_id)

    def list_models(self) -> list[dict]:
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM models WHERE deleted_at IS NULL ORDER BY created_at DESC")]
        return [self._enrich_model(row) for row in rows]

    def get_model_quality(self, model_id: str, policy: ModelQualityPolicy | dict | None = None) -> dict:
        with self._lock:
            model = self._raw_model(model_id)
            metadata = self._read_model_metadata(model)
            report = self._evaluate_model_quality(model, metadata, policy=policy)
            self._persist_quality_report(model, metadata, report)
            return report

    def _load_quality_policy_config(self) -> dict | None:
        path = bootstrap_config(
            self.app_dir / "config" / "model_quality_policy.example.json",
            self.runtime_paths.config_dir / "model_quality_policy.json",
        )
        try:
            return load_json_config(path)
        except RuntimeConfigurationError as exc:
            raise ModelStudioError(str(exc)) from exc

    def _raw_model(self, model_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM models WHERE model_id=? AND deleted_at IS NULL", (model_id,)).fetchone()
        if not row:
            raise ModelStudioError(f"model not found: {model_id}")
        return dict(row)

    @staticmethod
    def _read_model_metadata(model: dict) -> dict:
        try:
            value = json.loads((Path(model["model_dir"]) / "metadata.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, KeyError):
            try:
                value = json.loads(model.get("metadata_json") or "{}")
                return value if isinstance(value, dict) else {}
            except (TypeError, ValueError):
                return {}

    def _evaluate_model_quality(self, model: dict, metadata: dict, *, policy: ModelQualityPolicy | dict | None = None) -> dict:
        target = str(metadata.get("target") or model.get("target") or "ssc").lower()
        selected_policy = resolve_quality_policy(policy if policy is not None else self.quality_policy_source, target)
        dataset_version = None
        if model.get("dataset_version_id"):
            try:
                dataset_version = self.get_dataset_version(model["dataset_version_id"])
            except ModelStudioError:
                dataset_version = None
        validation_method = ""
        if model.get("experiment_id"):
            try:
                validation_method = self.get_experiment(model["experiment_id"]).get("validation_method") or ""
            except ModelStudioError:
                validation_method = ""
        return evaluate_model_quality(
            metadata,
            model=model,
            policy=selected_policy,
            dataset_version=dataset_version,
            validation_method=validation_method,
        ).to_dict()

    def _persist_quality_report(self, model: dict, metadata: dict, report: dict) -> None:
        metadata["quality_report"] = report
        metadata["quality_policy_version"] = report.get("policy_version") or ""
        metadata_path = Path(model["model_dir"]) / "metadata.json"
        atomic_write_json(metadata_path, metadata)
        with self.connect() as conn:
            conn.execute(
                "UPDATE models SET quality_report_json=?, metadata_json=? WHERE model_id=?",
                (json.dumps(report, ensure_ascii=False), json.dumps(metadata, ensure_ascii=False), model["model_id"]),
            )

    @staticmethod
    def _quality_gate_error(report: dict) -> str:
        blocked = [
            name for name, check in (report.get("checks") or {}).items()
            if check.get("enabled") and check.get("blocking") and check.get("status") in {"FAIL", "NOT_AVAILABLE"}
        ]
        warnings = [
            name for name, check in (report.get("checks") or {}).items()
            if check.get("enabled") and (
                check.get("status") in {"WARN", "NOT_AVAILABLE"}
                or (check.get("status") == "FAIL" and not check.get("blocking"))
            )
        ]
        return f"MODEL_QUALITY_GATE_FAILED: {json.dumps({'overall_status': report.get('overall_status'), 'failed_checks': blocked, 'warning_checks': warnings}, ensure_ascii=False)}"

    def validate_model(self, model_id: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE models SET
                  status='Validated',
                  display_name=COALESCE(NULLIF(?, ''), display_name),
                  version=COALESCE(NULLIF(?, ''), version),
                  description=COALESCE(NULLIF(?, ''), description),
                  tags=COALESCE(NULLIF(?, ''), tags),
                  notes=COALESCE(NULLIF(?, ''), notes)
                WHERE model_id=?
                """,
                (
                    payload.get("displayName") or payload.get("display_name") or "",
                    payload.get("version") or "",
                    payload.get("description") or "",
                    payload.get("tags") or "",
                    payload.get("notes") or "",
                    model_id,
                ),
            )
        self.log("model.validate", "model", model_id, "Model validated")
        return self.get_model(model_id)

    def publish_model(self, model_id: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        with self._lock:
            with self.connect() as conn:
                model = conn.execute("SELECT * FROM models WHERE model_id=?", (model_id,)).fetchone()
                if not model:
                    raise ModelStudioError(f"model not found: {model_id}")
                model = dict(model)
                src = Path(model["model_dir"])
                if not (src / "model.joblib").exists() or not (src / "metadata.json").exists():
                    raise ModelStudioError("model files are incomplete")
                target = model["target"]
                if target not in TARGETS:
                    raise ModelStudioError("invalid target")
                metadata = json.loads((src / "metadata.json").read_text(encoding="utf-8"))
                try:
                    validate_production_model_metadata_contract(metadata)
                except ModelInputMismatch as exc:
                    raise ModelStudioError(str(exc)) from exc
                quality_report = self._evaluate_model_quality(model, metadata)
                if quality_report["overall_status"] == "FAIL" or (
                    quality_report["overall_status"] == "WARN" and not quality_report.get("policy", {}).get("allow_warn_publish", False)
                ):
                    self._persist_quality_report(model, metadata, quality_report)
                    raise ModelStudioError(self._quality_gate_error(quality_report))
                dst = self.production_dir / "published" / model_id
                if dst.exists():
                    shutil.rmtree(dst)
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / "model.joblib", dst / "model.joblib")
                display_name = payload.get("displayName") or payload.get("display_name") or model.get("display_name") or model["model_name"]
                version = payload.get("version") or model.get("version") or metadata.get("model_version") or ""
                metadata.update({
                    "model_id": model_id,
                    "display_name": display_name,
                    "model_version": version,
                    "status": "Published",
                    "published_at": _now(),
                    "source_model_dir": str(src),
                    "fruit_type": model.get("fruit_type") or "",
                    "variety": _normalize_variety(model.get("variety") or ""),
                    "quality_report": quality_report,
                    "quality_policy_version": quality_report.get("policy_version") or "",
                })
                atomic_write_json(dst / "metadata.json", metadata)
                set_default = bool(payload.get("setDefault") or payload.get("set_default"))
                status = "Default" if set_default else "Published"
                if set_default:
                    self._clear_default_in_scope(conn, model)
                    legacy = self.production_dir / target
                    if legacy.exists():
                        shutil.rmtree(legacy)
                    legacy.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst / "model.joblib", legacy / "model.joblib")
                    shutil.copy2(dst / "metadata.json", legacy / "metadata.json")
                conn.execute(
                    """
                    UPDATE models SET status=?, is_default=?, published_at=?, model_dir=?,
                      display_name=COALESCE(NULLIF(?, ''), display_name),
                      version=COALESCE(NULLIF(?, ''), version),
                      description=COALESCE(NULLIF(?, ''), description),
                      tags=COALESCE(NULLIF(?, ''), tags),
                      notes=COALESCE(NULLIF(?, ''), notes),
                      metadata_json=?, quality_report_json=?
                    WHERE model_id=?
                    """,
                    (
                        status,
                        1 if set_default else 0,
                        _now(),
                        str(dst),
                        display_name,
                        version,
                        payload.get("description") or "",
                        payload.get("tags") or "",
                        payload.get("notes") or "",
                        json.dumps(metadata, ensure_ascii=False),
                        json.dumps(quality_report, ensure_ascii=False),
                        model_id,
                    ),
                )
        self.log("model.publish", "model", model_id, f"Published model for {target}")
        return self.get_model(model_id)

    def archive_model(self, model_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT status,is_default FROM models WHERE model_id=?", (model_id,)).fetchone()
            if row and (row["status"] == "Default" or row["is_default"]):
                raise ModelStudioError("default model cannot be archived; set another default first")
            conn.execute("UPDATE models SET status='Archived' WHERE model_id=?", (model_id,))
        self.log("model.archive", "model", model_id, "Model archived")
        return self.get_model(model_id)

    def delete_model_permanently(self, model_id: str, *, confirm: str = "") -> dict:
        model = self.get_model(model_id)
        if model["status"] == "Default" or model.get("is_default"):
            raise ModelStudioError("default model cannot be permanently deleted; set another default or archive flow first")
        if self._model_owns_default_legacy(model):
            raise ModelStudioError("current model still owns the default legacy bundle; set another compatible model as default first")
        if confirm != model_id:
            raise ModelStudioError("permanent delete requires model_id confirmation")
        paths = self._model_artifact_paths(model)
        for path in paths:
            if path.exists() and not self._is_managed_model_path(path):
                raise ModelStudioError(f"refusing to delete unmanaged model path: {path}")
        with self._lock:
            with self.connect() as conn:
                conn.execute("DELETE FROM models WHERE model_id=?", (model_id,))
            for path in paths:
                if path.exists():
                    self._delete_managed_model_path(path)
        self.log("model.delete", "model", model_id, "Model permanently deleted")
        return {"modelId": model_id, "deleted": True, "deletedPaths": [str(path) for path in paths]}

    def delete_models_permanently_batch(self, model_ids: list[str]) -> dict:
        seen: set[str] = set()
        requested = []
        for model_id in model_ids or []:
            model_id = str(model_id or "").strip()
            if model_id and model_id not in seen:
                seen.add(model_id)
                requested.append(model_id)
        deleted = []
        blocked = []
        failed = []
        for model_id in requested:
            try:
                result = self.delete_model_permanently(model_id, confirm=model_id)
                deleted.append(result)
            except ModelStudioError as exc:
                blocked.append({"modelId": model_id, "reason": str(exc)})
            except Exception as exc:
                failed.append({"modelId": model_id, "reason": str(exc)})
        return {
            "requested": requested,
            "deleted": deleted,
            "blocked": blocked,
            "failed": failed,
        }

    def set_default_model(self, model_id: str) -> dict:
        with self._lock:
            with self.connect() as conn:
                row = conn.execute("SELECT * FROM models WHERE model_id=?", (model_id,)).fetchone()
                if not row:
                    raise ModelStudioError(f"model not found: {model_id}")
                model = dict(row)
                if model["status"] not in {"Published", "Default", "Production"}:
                    raise ModelStudioError("only published models can be set as default")
                src = Path(model["model_dir"])
                if not (src / "model.joblib").exists() or not (src / "metadata.json").exists():
                    raise ModelStudioError("model files are incomplete")
                metadata = json.loads((src / "metadata.json").read_text(encoding="utf-8"))
                try:
                    validate_production_model_metadata_contract(metadata)
                except ModelInputMismatch as exc:
                    raise ModelStudioError(str(exc)) from exc
                quality_report = self._evaluate_model_quality(model, metadata)
                if quality_report["overall_status"] == "FAIL" or (
                    quality_report["overall_status"] == "WARN" and not quality_report.get("policy", {}).get("allow_warn_publish", False)
                ):
                    self._persist_quality_report(model, metadata, quality_report)
                    raise ModelStudioError(self._quality_gate_error(quality_report))
                metadata["quality_report"] = quality_report
                metadata["quality_policy_version"] = quality_report.get("policy_version") or ""
                self._clear_default_in_scope(conn, model)
                conn.execute(
                    "UPDATE models SET status='Default', is_default=1, metadata_json=?, quality_report_json=? WHERE model_id=?",
                    (json.dumps(metadata, ensure_ascii=False), json.dumps(quality_report, ensure_ascii=False), model_id),
                )
                atomic_write_json(src / "metadata.json", metadata)
                legacy = self.production_dir / model["target"]
                if legacy.exists():
                    shutil.rmtree(legacy)
                legacy.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / "model.joblib", legacy / "model.joblib")
                shutil.copy2(src / "metadata.json", legacy / "metadata.json")
        self.log("model.default", "model", model_id, "Model set as default")
        return self.get_model(model_id)

    def list_published_models(self, *, fruit_type: str = "", variety: str = "", target: str = "") -> list[dict]:
        params: list[object] = []
        where = "WHERE status IN ('Published','Default','Production') AND deleted_at IS NULL"
        if target:
            where += " AND target=?"
            params.append(target.lower())
        if fruit_type:
            where += " AND lower(fruit_type)=lower(?)"
            params.append(fruit_type)
            if variety:
                where += " AND (lower(variety)=lower(?) OR lower(variety)='generic' OR variety='')"
                params.append(_normalize_variety(variety))
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute(f"SELECT * FROM models {where} ORDER BY is_default DESC, published_at DESC", params)]
        return [self._enrich_model(row) for row in rows]

    def model_catalog(self, *, fruit_type: str = "", variety: str = "") -> dict:
        with self.connect() as conn:
            fruit_types = [
                row["fruit_type"] for row in conn.execute(
                    """
                    SELECT DISTINCT fruit_type FROM models
                    WHERE status IN ('Published','Default','Production') AND deleted_at IS NULL AND COALESCE(fruit_type,'')!=''
                    ORDER BY fruit_type
                    """
                )
            ]
            variety_params: list[object] = []
            variety_where = "WHERE status IN ('Published','Default','Production') AND deleted_at IS NULL"
            if fruit_type:
                variety_where += " AND lower(fruit_type)=lower(?)"
                variety_params.append(fruit_type)
            varieties = [
                row["variety"] or "generic" for row in conn.execute(
                    f"SELECT DISTINCT COALESCE(NULLIF(variety,''),'generic') AS variety FROM models {variety_where} ORDER BY variety",
                    variety_params,
                )
            ]
        compatible = {
            target: self.list_published_models(fruit_type=fruit_type, variety=variety, target=target)
            for target in ("ssc", "ta", "ph")
        }
        defaults = {target: _best_default(compatible[target], variety) for target in compatible}
        return {
            "fruitTypes": fruit_types,
            "varieties": varieties,
            "compatible": compatible,
            "defaults": defaults,
        }

    def export_model_bundle(self, model_id: str) -> dict:
        model = self.get_model(model_id)
        src = Path(model["model_dir"])
        if not (src / "model.joblib").exists() or not (src / "metadata.json").exists():
            raise ModelStudioError("model files are incomplete")
        export_dir = self.artifact_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(ch for ch in (model.get("display_name") or model["model_name"]) if ch.isalnum() or ch in "-_")[:48] or model_id
        zip_path = export_dir / f"{safe_name}_{model_id}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(src / "model.joblib", "model.joblib")
            archive.write(src / "metadata.json", "metadata.json")
        self.log("model.export", "model", model_id, str(zip_path))
        return {"modelId": model_id, "bundlePath": str(zip_path)}

    def get_model(self, model_id: str) -> dict:
        return self._enrich_model(self._raw_model(model_id))

    def model_registry(self, *, query: str = "", fruit_type: str = "", variety: str = "", target: str = "", status: str = "", algorithm: str = "", preprocessing: str = "") -> dict:
        params: list[object] = []
        where = "WHERE deleted_at IS NULL"
        if query:
            where += " AND (model_id LIKE ? OR model_name LIKE ? OR COALESCE(display_name,'') LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        if fruit_type:
            where += " AND lower(COALESCE(fruit_type,''))=lower(?)"
            params.append(fruit_type)
        if variety:
            where += " AND lower(COALESCE(variety,'generic'))=lower(?)"
            params.append(_normalize_variety(variety))
        if target:
            where += " AND target=?"
            params.append(target.lower())
        if status and status != "all":
            where += " AND status=?"
            params.append(status)
        if algorithm:
            where += " AND model_type=?"
            params.append(MODEL_ALIASES.get(algorithm, algorithm))
        if preprocessing:
            where += " AND preprocessing=?"
            params.append(preprocessing.upper())
        with self.connect() as conn:
            rows = [self._enrich_model(dict(row)) for row in conn.execute(f"SELECT * FROM models {where} ORDER BY fruit_type,variety,target,is_default DESC,created_at DESC", params)]
        tree: dict[str, dict] = {}
        for model in rows:
            fruit = model.get("fruit_type") or "unspecified"
            var = _normalize_variety(model.get("variety") or "")
            target_key = model["target"]
            tree.setdefault(fruit, {}).setdefault(var, {}).setdefault(target_key, []).append(model)
        return {"models": rows, "tree": tree}

    def logs(self, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM operation_logs ORDER BY id DESC LIMIT ?", (limit,)
            )]

    def log(self, operation: str, target: str, resource_id: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO operation_logs(timestamp,operation,target,resource_id,message) VALUES(?,?,?,?,?)",
                (_now(), operation, target, resource_id, message),
            )

    def _run_training_job(self, job_id: str) -> None:
        try:
            job = self.get_job(job_id)
            experiment = self.get_experiment(job["experiment_id"])
            dataset_id = experiment["dataset_id"]
            self._job_update(job_id, "Preparing", "Preparing", 8, "Preparing dataset")
            feature_info = self.generate_features(dataset_id, experiment.get("dataset_version_id"))
            feature_csv = feature_info["featureCsv"]
            self._set_experiment_feature_csv(experiment["experiment_id"], feature_csv)
            combinations = [(pre, model) for pre in experiment["preprocessing"] for model in experiment["models"]]
            if not combinations:
                raise ModelStudioError("no training combinations selected")
            results = []
            for index, (preprocessing, model_type) in enumerate(combinations):
                if self.get_job(job_id).get("cancel_requested"):
                    self._job_update(job_id, "Cancelled", "Cancelled", 100, "Training job cancelled")
                    return
                progress = 20 + int(index / len(combinations) * 60)
                self._job_update(job_id, "Training", "Training", progress, f"{preprocessing} + {model_type} started")
                model_output = self.model_dir / "candidates" / experiment["experiment_id"] / f"{experiment['target']}_{preprocessing}_{model_type}"
                try:
                    result = train_one(
                        feature_csv,
                        target=experiment["target"],
                        preprocessing=preprocessing,
                        model_type=model_type,
                        output_dir=model_output,
                        validation_method=experiment["validation_method"],
                        calibration_required=bool((feature_info.get("modelInputContract") or {}).get("calibration", {}).get("required")),
                    )
                    metadata = result.setdefault("metadata", {})
                    metadata["feature_pipeline"] = feature_info.get("pipelineConfig") or FeaturePipelineConfig.production().to_dict()
                    metadata["model_input_contract"] = feature_info.get("modelInputContract") or {}
                    metadata["pipeline_signature"] = feature_info.get("pipelineSignature") or ""
                    validate_model_metadata_contract(metadata)
                    atomic_write_json(model_output / "metadata.json", metadata)
                    model_row = self._register_candidate_model(experiment, result, model_output, job_id)
                    result_row = {k: v for k, v in result.items() if k != "metadata"}
                    result_row["model_id"] = model_row["model_id"]
                    results.append(result_row)
                    self._job_log(job_id, f"{preprocessing} + {model_type} completed")
                except Exception as exc:
                    result_row = {
                        "target": experiment["target"],
                        "preprocessing": preprocessing,
                        "model": model_type,
                        "error": str(exc),
                    }
                    results.append(result_row)
                    self._job_log(job_id, f"{preprocessing} + {model_type} failed: {exc}")
            successful_results = [row for row in results if not row.get("error")]
            if not successful_results:
                raise ModelStudioError("all training combinations failed")
            results = sorted(results, key=lambda row: (row.get("rmse") if row.get("rmse") is not None else 999999))
            payload = {"featureCsv": feature_csv, "results": results, "featureRows": feature_info["rows"]}
            with self.connect() as conn:
                conn.execute(
                    "UPDATE training_experiments SET status='Completed', result_json=? WHERE experiment_id=?",
                    (json.dumps(payload, ensure_ascii=False), experiment["experiment_id"]),
                )
            self._job_finish(job_id, "Completed", payload)
            self.log("training.complete", "job", job_id, "Training completed")
        except Exception as exc:
            error = f"{exc}"
            with self.connect() as conn:
                row = conn.execute("SELECT experiment_id FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row:
                    conn.execute("UPDATE training_experiments SET status='Failed' WHERE experiment_id=?", (row["experiment_id"],))
            self._job_fail(job_id, error, traceback.format_exc())
            self.log("training.failed", "job", job_id, error)

    def _sample_dirs(self, root: Path) -> list[Path]:
        if (root / "rgb").exists() and (root / "multispectral").exists():
            return [root]
        return sorted([child for child in root.iterdir() if child.is_dir()])

    def _sample_exists(self, dataset_id: str, sample_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM samples WHERE dataset_id=? AND sample_id=?",
                (dataset_id, sample_id),
            ).fetchone()
        return bool(row)

    def _dataset_local_path(self, dataset_id: str) -> Path:
        return self.dataset_store_dir / dataset_id

    def _sample_import_report(self, sample_dir: Path) -> dict:
        structure = inspect_sample_structure(sample_dir)
        warnings = list(structure.get("warnings") or [])
        metadata_path = sample_dir / "metadata.json"
        metadata_status = "present" if metadata_path.exists() and metadata_path.is_file() else "missing"
        metadata = {}
        if metadata_status == "present":
            try:
                loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata = loaded if isinstance(loaded, dict) else {}
            except Exception:
                metadata = {}
                warnings.append("metadata.json unreadable")
        if metadata_status == "missing":
            warnings.append("missing metadata.json")
        if not structure["valid"]:
            status = "Invalid"
        elif warnings or structure.get("calibration_status") != "complete" or not structure.get("complete"):
            status = "Warning"
        else:
            status = "Valid"
        return {
            "sample_id": sample_dir.name,
            "sample_dir": str(sample_dir),
            "status": status,
            "rgb_count": int(structure.get("rgb_count") or 0),
            "multispectral_count": int(structure.get("multispectral_count") or 0),
            "available_bands": structure.get("available_bands") or [],
            "dark_count": _calibration_count(sample_dir, "dark"),
            "white_count": _calibration_count(sample_dir, "white"),
            "calibration_status": structure.get("calibration_status") or "missing",
            "metadata_status": metadata_status,
            "metadata": metadata,
            "warnings": warnings,
            "structure": structure,
        }

    def _sample_identity_from_report(self, sample_dir: Path, import_report: dict, dataset: dict) -> dict:
        metadata = import_report.get("metadata") or {}
        fruit_type = str(metadata.get("fruit_type") or metadata.get("fruitType") or "").strip()
        variety = str(metadata.get("variety") or "").strip()
        if not fruit_type:
            fruit_type = str(dataset.get("fruit_type") or "").strip()
        if not variety:
            variety = str(dataset.get("variety") or "").strip()
        return {
            "sample_id": str(metadata.get("sample_id") or metadata.get("sampleId") or sample_dir.name).strip() or sample_dir.name,
            "sample_name": str(metadata.get("sample_name") or metadata.get("sampleName") or sample_dir.name).strip() or sample_dir.name,
            "sample_mode": str(metadata.get("sample_mode") or metadata.get("sampleMode") or "").strip(),
            "fruit_type": fruit_type,
            "variety": _normalize_variety(variety),
            "capture_time": str(
                metadata.get("capture_time")
                or metadata.get("captureTime")
                or metadata.get("captured_at")
                or metadata.get("capturedAt")
                or metadata.get("created_at")
                or metadata.get("createdAt")
                or ""
            ).strip(),
        }

    def _resolve_dataset_scope_for_import(self, dataset: dict, identities: list[dict]) -> dict:
        scoped = [
            {"fruitType": item["fruit_type"], "variety": _normalize_variety(item["variety"])}
            for item in identities
            if item.get("fruit_type")
        ]
        counts: dict[tuple[str, str], dict] = {}
        for item in scoped:
            key = (item["fruitType"].casefold(), item["variety"].casefold())
            counts.setdefault(key, {"fruitType": item["fruitType"], "variety": item["variety"], "count": 0})
            counts[key]["count"] += 1
        dataset_fruit = str(dataset.get("fruit_type") or "").strip()
        dataset_variety = _normalize_variety(dataset.get("variety") or "")
        if dataset_fruit:
            expected = (dataset_fruit.casefold(), dataset_variety.casefold())
            if any(key != expected for key in counts):
                raise ModelStudioError(f"{DATASET_SAMPLE_SCOPE_CONFLICT}: {json.dumps({'scopes': list(counts.values())}, ensure_ascii=False)}")
            return {
                "dataset": dataset,
                "scope": {"fruitType": dataset_fruit, "variety": dataset_variety, "source": "dataset"},
            }
        if len(counts) > 1:
            raise ModelStudioError(f"{DATASET_SAMPLE_SCOPE_CONFLICT}: {json.dumps({'scopes': list(counts.values())}, ensure_ascii=False)}")
        if len(counts) == 1:
            scope = next(iter(counts.values()))
            with self.connect() as conn:
                conn.execute(
                    "UPDATE datasets SET fruit_type=?, variety=?, updated_at=? WHERE dataset_id=?",
                    (scope["fruitType"], scope["variety"], _now(), dataset["dataset_id"]),
                )
            updated = self.get_dataset(dataset["dataset_id"])
            return {
                "dataset": updated,
                "scope": {"fruitType": scope["fruitType"], "variety": scope["variety"], "source": "sample_metadata"},
            }
        return {
            "dataset": dataset,
            "scope": {"fruitType": dataset_fruit, "variety": dataset_variety, "source": "unspecified"},
        }

    def _find_duplicate_sample(self, dataset_id: str, sample_id: str, source_path: Path) -> dict | None:
        source = str(source_path)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM samples
                WHERE dataset_id=? AND (sample_id=? OR source_path=?)
                ORDER BY id LIMIT 1
                """,
                (dataset_id, sample_id, source),
            ).fetchone()
        return dict(row) if row else None

    def _unique_sample_id(self, dataset_id: str, base: str) -> str:
        suffix = 2
        candidate = f"{base}_{suffix}"
        while self._sample_exists(dataset_id, candidate):
            suffix += 1
            candidate = f"{base}_{suffix}"
        return candidate

    def _unique_sample_path(self, samples_root: Path, sample_id: str) -> Path:
        candidate = samples_root / sample_id
        if not candidate.exists():
            return candidate
        suffix = 2
        while True:
            candidate = samples_root / f"{sample_id}_{suffix}"
            if not candidate.exists():
                return candidate
            suffix += 1

    def _write_dataset_labels_csv(self, dataset_id: str) -> None:
        dataset = self.get_dataset(dataset_id)
        target = Path(dataset.get("local_path") or dataset["storage_path"]) / "labels.csv"
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT sample_id,ssc,ta,ph FROM labels WHERE dataset_id=? ORDER BY sample_id",
                (dataset_id,),
            )]
        self._write_labels_csv_file(target, rows)

    def _write_labels_csv_file(self, target: Path, rows: list[dict]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["sample_id", "ssc", "ta", "ph"])
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "sample_id": row.get("sample_id") or "",
                    "ssc": "" if row.get("ssc") is None else row.get("ssc"),
                    "ta": "" if row.get("ta") is None else row.get("ta"),
                    "ph": "" if row.get("ph") is None else row.get("ph"),
                })

    def _mark_dataset_dirty(self, dataset_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE datasets SET dirty=1, updated_at=? WHERE dataset_id=?", (_now(), dataset_id))

    def _clear_default_in_scope(self, conn: sqlite3.Connection, model: dict) -> None:
        conn.execute(
            """
            UPDATE models SET status='Published', is_default=0
            WHERE target=? AND lower(COALESCE(fruit_type,''))=lower(?) AND lower(COALESCE(variety,'generic'))=lower(?) AND (status='Default' OR is_default=1)
            """,
            (
                model["target"],
                model.get("fruit_type") or "",
                _normalize_variety(model.get("variety") or ""),
            ),
        )

    def _upsert_sample(self, row: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO samples(dataset_id,sample_id,fruit_type,variety,sample_name,storage_path,source_path,local_path,rgb_count,multispectral_count,dark_count,white_count,available_bands,calibration_status,data_status,capture_time,quality_json,created_at,imported_at)
                VALUES(:dataset_id,:sample_id,:fruit_type,:variety,:sample_name,:storage_path,:source_path,:local_path,:rgb_count,:multispectral_count,:dark_count,:white_count,:available_bands,:calibration_status,:data_status,:capture_time,:quality_json,:created_at,:imported_at)
                ON CONFLICT(dataset_id,sample_id) DO UPDATE SET
                  fruit_type=excluded.fruit_type,
                  variety=excluded.variety,
                  storage_path=excluded.storage_path,
                  source_path=excluded.source_path,
                  local_path=excluded.local_path,
                  sample_name=excluded.sample_name,
                  rgb_count=excluded.rgb_count,
                  multispectral_count=excluded.multispectral_count,
                  dark_count=excluded.dark_count,
                  white_count=excluded.white_count,
                  available_bands=excluded.available_bands,
                  calibration_status=excluded.calibration_status,
                  data_status=excluded.data_status,
                  capture_time=excluded.capture_time,
                  quality_json=excluded.quality_json,
                  imported_at=excluded.imported_at
                """,
                row,
            )

    def _refresh_dataset_counts(self, dataset_id: str, calibration_statuses: list[str] | None = None) -> None:
        with self.connect() as conn:
            sample_count = conn.execute("SELECT COUNT(*) FROM samples WHERE dataset_id=?", (dataset_id,)).fetchone()[0]
            label_count = conn.execute(
                "SELECT COUNT(*) FROM labels WHERE dataset_id=? AND (ssc IS NOT NULL OR ta IS NOT NULL OR ph IS NOT NULL)",
                (dataset_id,),
            ).fetchone()[0]
            current = conn.execute("SELECT calibration_status FROM datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
            calibration = current["calibration_status"] if current else "unknown"
            if calibration_statuses:
                calibration = "complete" if all(item == "complete" for item in calibration_statuses) else "missing"
            conn.execute(
                "UPDATE datasets SET sample_count=?, label_count=?, calibration_status=?, updated_at=? WHERE dataset_id=?",
                (sample_count, label_count, calibration, _now(), dataset_id),
            )

    def _dashboard_attention(self, dirty_count: int, failed_jobs: int, candidates: list[dict]) -> list[dict]:
        items = []
        if dirty_count:
            items.append({"kind": "dataset_dirty", "severity": "warning", "message": f"{dirty_count} Dataset has working changes"})
        if failed_jobs:
            items.append({"kind": "training_failed", "severity": "error", "message": f"{failed_jobs} Training run failed"})
        if candidates:
            items.append({"kind": "candidate_review", "severity": "warning", "message": f"{len(candidates)} Candidate models waiting for review"})
        return items

    def _enrich_model(self, model: dict) -> dict:
        model["lifecycleStatus"] = "Published" if model.get("status") == "Production" else model.get("status")
        model["isDefault"] = bool(model.get("is_default") or model.get("status") == "Default")
        model["stationVisible"] = model.get("status") in PUBLISHED_STATUSES
        warnings = []
        sample_count = None
        metadata = {}
        try:
            metadata = json.loads(model.get("metadata_json") or "{}")
        except Exception:
            metadata = {}
        try:
            quality_report = json.loads(model.get("quality_report_json") or "{}")
        except Exception:
            quality_report = {}
        if not quality_report:
            quality_report = metadata.get("quality_report") if isinstance(metadata.get("quality_report"), dict) else {}
        model["qualityReport"] = quality_report
        model["qualityStatus"] = quality_report.get("overall_status") or "NOT_AVAILABLE"
        model["qualityPolicyVersion"] = quality_report.get("policy_version") or ""
        sample_count = metadata.get("sample_count")
        if sample_count is None and model.get("dataset_version_id"):
            try:
                sample_count = int(self.get_dataset_version(model["dataset_version_id"]).get("sample_count") or 0)
            except Exception:
                sample_count = None
        if sample_count is not None and int(sample_count) < 10:
            warnings.append("Insufficient Samples")
        if model.get("r2") is not None and float(model["r2"]) < 0:
            warnings.append("Poor Validation")
        if metadata.get("calibrated") is False or metadata.get("calibration_required") and not metadata.get("calibrated"):
            warnings.append("Calibration Incomplete")
        if model.get("status") == "Candidate":
            warnings.append("Experimental")
        if model["qualityStatus"] in {"WARN", "NOT_AVAILABLE"}:
            warnings.append(f"Quality {model['qualityStatus']}")
        elif model["qualityStatus"] == "FAIL":
            warnings.append("Quality FAIL")
        model["qualityWarnings"] = warnings
        model["lineage"] = {
            "datasetId": model.get("dataset_id") or "",
            "datasetVersionId": model.get("dataset_version_id") or "",
            "datasetVersionLabel": model.get("dataset_version_label") or "",
            "experimentId": model.get("experiment_id") or "",
            "runId": model.get("job_id") or "",
            "parentModelId": model.get("parent_model_id") or "",
        }
        path = Path(model.get("model_dir") or "")
        model["fileStatus"] = {
            "modelJoblib": bool((path / "model.joblib").exists()),
            "metadataJson": bool((path / "metadata.json").exists()),
            "modelDir": str(path),
        }
        return model

    def _model_artifact_paths(self, model: dict) -> list[Path]:
        paths = [Path(model["model_dir"])]
        try:
            metadata = json.loads(model.get("metadata_json") or "{}")
            source = metadata.get("source_model_dir")
            if source:
                paths.append(Path(source))
        except Exception:
            pass
        published = self.production_dir / "published" / model["model_id"]
        if published not in paths:
            paths.append(published)
        legacy = self.production_dir / model["target"]
        if (model.get("status") == "Default" or model.get("is_default")) and legacy not in paths:
            paths.append(legacy)
        return _unique_paths(paths)

    def _delete_managed_model_path(self, path: Path) -> None:
        resolved = path.resolve()
        if not self._is_managed_model_path(resolved):
            raise ModelStudioError(f"refusing to delete unmanaged model path: {path}")
        shutil.rmtree(resolved, ignore_errors=True)

    def _is_managed_model_path(self, path: Path) -> bool:
        resolved = path.resolve()
        allowed_roots = [self.model_dir.resolve(), (self.production_dir / "published").resolve()]
        return any(root in resolved.parents for root in allowed_roots)

    def _is_managed_artifact_path(self, path: Path) -> bool:
        resolved = path.resolve()
        root = self.artifact_dir.resolve()
        return root in resolved.parents

    def _delete_managed_artifact_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not self._is_managed_artifact_path(resolved):
            raise ModelStudioError(f"refusing to delete unmanaged artifact path: {path}")
        if resolved.is_dir():
            shutil.rmtree(resolved, ignore_errors=True)
        else:
            resolved.unlink(missing_ok=True)
        return resolved

    def _delete_managed_dataset_path(self, path: Path) -> Path:
        resolved = self._assert_managed_dataset_path(path)
        shutil.rmtree(resolved, ignore_errors=True)
        return resolved

    def _assert_managed_dataset_path(self, path: Path) -> Path:
        resolved = path.resolve()
        root = self.dataset_store_dir.resolve()
        if root not in resolved.parents:
            raise ModelStudioError(f"refusing to delete unmanaged dataset path: {path}")
        return resolved

    def _dataset_artifact_paths(self, dataset_id: str, experiments: list[dict], versions: list[dict]) -> list[Path]:
        paths = [self.artifact_dir / "features" / dataset_id]
        experiment_ids = {row["experiment_id"] for row in experiments if row.get("experiment_id")}
        version_ids = {row["dataset_version_id"] for row in versions if row.get("dataset_version_id")}
        with self.connect() as conn:
            for row in conn.execute("SELECT feature_csv FROM training_experiments WHERE dataset_id=?", (dataset_id,)):
                if row["feature_csv"]:
                    paths.append(Path(row["feature_csv"]))
        for item in self.artifact_dir.rglob("*"):
            text = str(item)
            if dataset_id in text or any(exp_id in text for exp_id in experiment_ids) or any(ver_id in text for ver_id in version_ids):
                paths.append(item)
        return _unique_paths(paths)

    def _model_owns_default_legacy(self, model: dict) -> bool:
        legacy = self.production_dir / model["target"]
        metadata_path = legacy / "metadata.json"
        if not metadata_path.exists():
            return False
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return metadata.get("model_id") == model.get("model_id")

    def _update_sample_feature(self, dataset_id: str, sample_id: str, feature: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE samples SET feature_json=? WHERE dataset_id=? AND sample_id=?",
                (json.dumps(feature, ensure_ascii=False), dataset_id, sample_id),
            )

    def _register_candidate_model(self, experiment: dict, result: dict, model_output: Path, job_id: str) -> dict:
        metadata = result.get("metadata") or {}
        model_id = f"mdl_{uuid.uuid4().hex[:10]}"
        model_name = f"{experiment['target'].upper()}_{result['preprocessing']}_{result['model']}_{metadata.get('model_version','')}"
        version = self.get_dataset_version(experiment["dataset_version_id"]) if experiment.get("dataset_version_id") else None
        metadata.update({
            "model_id": model_id,
            "model_name": model_name,
            "display_name": model_name,
            "dataset_id": experiment["dataset_id"],
            "dataset_version_id": experiment.get("dataset_version_id"),
            "dataset_version_label": version["version_name"] if version else "",
            "experiment_id": experiment["experiment_id"],
            "training_job_id": job_id,
            "fruit_type": experiment.get("fruit_type") or "",
            "variety": _normalize_variety(experiment.get("variety") or ""),
            "parent_model_id": experiment.get("parent_model_id") or "",
        })
        atomic_write_json(model_output / "metadata.json", metadata)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO models(model_id,experiment_id,dataset_id,model_name,display_name,target,fruit_type,variety,model_type,preprocessing,version,status,is_default,dataset_version_id,dataset_version_label,job_id,parent_model_id,r2,rmse,mae,rpd,model_dir,metadata_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    model_id,
                    experiment["experiment_id"],
                    experiment["dataset_id"],
                    model_name,
                    model_name,
                    experiment["target"],
                    experiment.get("fruit_type") or "",
                    _normalize_variety(experiment.get("variety") or ""),
                    result["model"],
                    result["preprocessing"],
                    metadata.get("model_version") or time.strftime("%Y%m%d_%H%M%S"),
                    "Candidate",
                    0,
                    experiment.get("dataset_version_id"),
                    version["version_name"] if version else "",
                    job_id,
                    experiment.get("parent_model_id"),
                    _none_if_nan(result.get("r2")),
                    _none_if_nan(result.get("rmse")),
                    _none_if_nan(result.get("mae")),
                    _none_if_nan(result.get("rpd")),
                    str(model_output),
                    json.dumps(metadata, ensure_ascii=False),
                    _now(),
                ),
            )
        return self.get_model(model_id)

    def _set_experiment_feature_csv(self, experiment_id: str, feature_csv: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE training_experiments SET feature_csv=? WHERE experiment_id=?", (feature_csv, experiment_id))

    def _job_update(self, job_id: str, status: str, step: str, progress: int, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?,step=?,progress=?,message=?,started_at=COALESCE(started_at,?) WHERE job_id=?",
                (status, step, progress, message, _now(), job_id),
            )
        self._job_log(job_id, message)

    def _job_log(self, job_id: str, message: str) -> None:
        job = self.get_job(job_id)
        logs = job.get("logs") or []
        logs.append(f"[{time.strftime('%H:%M:%S')}] {message}")
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET logs_json=? WHERE job_id=?", (json.dumps(logs, ensure_ascii=False), job_id))

    def _job_finish(self, job_id: str, status: str, result: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?,step=?,progress=100,message=?,result_json=?,finished_at=? WHERE job_id=?",
                (status, status, status, json.dumps(result, ensure_ascii=False), _now(), job_id),
            )

    def _job_fail(self, job_id: str, error: str, trace: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='Failed',step='Failed',progress=100,message=?,error=?,finished_at=? WHERE job_id=?",
                (error, trace, _now(), job_id),
            )
        self._job_log(job_id, f"Failed: {error}")

    def _decode_job(self, row: dict) -> dict:
        row["logs"] = json.loads(row.pop("logs_json") or "[]")
        row["result"] = json.loads(row.pop("result_json") or "null")
        return row


def _normalize_models(values) -> list[str]:
    result = []
    for value in values:
        key = str(value)
        model = MODEL_ALIASES.get(key, MODEL_ALIASES.get(key.upper()))
        if not model:
            raise ModelStudioError(f"unsupported model: {value}")
        if model not in result:
            result.append(model)
    return result


def _normalize_preprocessing(values) -> list[str]:
    result = []
    for value in values:
        method = str(value).upper()
        if method not in PREPROCESSING:
            raise ModelStudioError(f"unsupported preprocessing: {value}")
        if method not in result:
            result.append(method)
    return result


def _normalize_variety(value: str) -> str:
    text = str(value or "").strip()
    return text or "generic"


def _snapshot_hash(dataset_id: str, sample_snapshot_json: str, label_snapshot_json: str) -> str:
    digest = hashlib.sha256()
    digest.update(dataset_id.encode("utf-8"))
    digest.update(sample_snapshot_json.encode("utf-8"))
    digest.update(label_snapshot_json.encode("utf-8"))
    return digest.hexdigest()


def _optional_float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ModelStudioError(f"label value must be numeric: {value}") from exc


def _label_status(sample: dict) -> str:
    values = [sample.get("ssc"), sample.get("ta"), sample.get("ph")]
    present = sum(1 for value in values if value is not None and value != "")
    if present == 3:
        return "Complete"
    if present:
        return "Partial"
    return "Missing"


def _best_default(models: list[dict], variety: str = "") -> dict | None:
    normalized = _normalize_variety(variety).lower()
    for model in models:
        if (model.get("status") == "Default" or model.get("is_default")) and _normalize_variety(model.get("variety") or "").lower() == normalized:
            return model
    for model in models:
        if (model.get("status") == "Default" or model.get("is_default")) and _normalize_variety(model.get("variety") or "").lower() == "generic":
            return model
    return None


def _calibration_count(sample_dir: Path, kind: str) -> int:
    folder = sample_dir / "calibration" / kind
    if not folder.exists():
        return 0
    return len([path for path in folder.iterdir() if path.is_file()])


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        if not path:
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _none_if_nan(value):
    try:
        if value != value:
            return None
    except Exception:
        pass
    return value
