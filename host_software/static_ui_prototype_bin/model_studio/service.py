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

from quality_algorithm.dataset import InsufficientTrainingDataset, read_labels_csv
from quality_algorithm.filters import expected_wavelengths, load_filter_config
from quality_algorithm.spectral_features import extract_feature_record, inspect_sample_structure
from training.train import train_one


TARGETS = {"ssc", "ta", "ph"}
MODEL_ALIASES = {"PLSR": "PLSR", "SVR": "SVR", "RF": "RF", "Random Forest": "RF"}
PREPROCESSING = {"RAW", "SNV", "MSC"}


class ModelStudioError(RuntimeError):
    pass


class ModelStudioService:
    def __init__(self, app_dir: str | Path) -> None:
        self.app_dir = Path(app_dir).resolve()
        self.root = self.app_dir / "model_studio"
        self.data_dir = self.app_dir / "model_studio_data"
        self.dataset_store_dir = self.data_dir / "datasets"
        self.database_dir = self.root / "database"
        self.artifact_dir = self.root / "artifacts"
        self.model_dir = self.root / "models"
        self.production_dir = self.app_dir / "trained_models"
        self.database_path = self.database_dir / "model_studio.sqlite"
        self._lock = threading.Lock()
        self.database_dir.mkdir(parents=True, exist_ok=True)
        self.dataset_store_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.production_dir.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
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
            self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        migrations = {
            "datasets": {
                "local_path": "TEXT",
                "import_source_path": "TEXT",
                "dirty": "INTEGER DEFAULT 0",
                "latest_version_id": "TEXT",
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
        conn.execute("UPDATE models SET display_name=model_name WHERE display_name IS NULL OR display_name=''")
        conn.execute("UPDATE models SET is_default=0 WHERE is_default IS NULL")

    def dashboard(self) -> dict:
        with self.connect() as conn:
            dataset_count = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
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
            production = [dict(row) for row in conn.execute(
                "SELECT * FROM models WHERE status IN ('Published','Default','Production') ORDER BY target, published_at DESC"
            )]
            recent_jobs = [dict(row) for row in conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 5")]
            recent_datasets = [dict(row) for row in conn.execute("SELECT * FROM datasets ORDER BY created_at DESC LIMIT 5")]
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
            },
            "productionModels": production,
            "recentJobs": recent_jobs,
            "recentDatasets": recent_datasets,
            "filterConfig": [band.to_dict() for band in load_filter_config()],
        }

    def list_datasets(self) -> list[dict]:
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM datasets ORDER BY created_at DESC")]
            versions = {}
            for row in conn.execute("SELECT * FROM dataset_versions ORDER BY dataset_id, version DESC"):
                versions.setdefault(row["dataset_id"], []).append(dict(row))
        for dataset in rows:
            dataset["versions"] = versions.get(dataset["dataset_id"], [])
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
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO datasets(dataset_id,dataset_name,fruit_type,variety,description,created_at,storage_path,local_path,import_source_path,enabled_wavelengths)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    dataset_id,
                    name,
                    payload.get("fruit_type") or payload.get("fruitType") or "",
                    _normalize_variety(payload.get("variety") or ""),
                    payload.get("description") or "",
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
        duplicate_policy = duplicate_policy if duplicate_policy in {"skip", "new", "cancel"} else "skip"
        local_root = Path(dataset.get("local_path") or dataset["storage_path"]).expanduser()
        samples_root = local_root / "samples"
        samples_root.mkdir(parents=True, exist_ok=True)
        sample_dirs = self._sample_dirs(root)
        imported = 0
        new_count = 0
        existing_count = 0
        conflicts = 0
        skipped = 0
        warnings: list[str] = []
        duplicates: list[dict] = []
        calibration_statuses: list[str] = []
        for sample_dir in sample_dirs:
            import_report = self._sample_import_report(sample_dir)
            report = import_report["structure"]
            if import_report["status"] == "Invalid":
                skipped += 1
                warnings.append(f"{sample_dir.name}: " + "; ".join(import_report.get("warnings") or ["invalid sample folder"]))
                continue
            duplicate = self._find_duplicate_sample(dataset_id, sample_dir.name, sample_dir)
            if duplicate:
                conflicts += 1
                existing_count += 1
                duplicates.append({
                    "sampleId": duplicate.get("sample_id"),
                    "sourcePath": duplicate.get("source_path") or "",
                    "localPath": duplicate.get("local_path") or duplicate.get("storage_path") or "",
                })
                if duplicate_policy == "cancel":
                    raise ModelStudioError(f"sample already exists: {sample_dir.name}")
                if duplicate_policy == "skip":
                    skipped += 1
                    continue
            sample_id = sample_dir.name if not duplicate else self._unique_sample_id(dataset_id, sample_dir.name)
            local_sample_dir = self._unique_sample_path(samples_root, sample_id)
            shutil.copytree(sample_dir, local_sample_dir)
            calibration_statuses.append(str(report["calibration_status"]))
            row = {
                "dataset_id": dataset_id,
                "sample_id": sample_id,
                "sample_name": sample_dir.name,
                "fruit_type": dataset.get("fruit_type") or "",
                "variety": dataset.get("variety") or "",
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
                "quality_json": json.dumps(import_report, ensure_ascii=False),
                "created_at": _now(),
                "imported_at": _now(),
            }
            if import_report["status"] != "Valid":
                warnings.append(f"{sample_dir.name}: " + "; ".join(import_report.get("warnings") or []))
            self._upsert_sample(row)
            imported += 1
            new_count += 1
        self._refresh_dataset_counts(dataset_id, calibration_statuses)
        self._mark_dataset_dirty(dataset_id)
        self.log("samples.import", "dataset", dataset_id, f"Imported {imported} samples")
        return {
            "dataset": self.get_dataset(dataset_id),
            "imported": imported,
            "newSamples": new_count,
            "existingSamples": existing_count,
            "conflicts": conflicts,
            "skipped": skipped,
            "duplicates": duplicates[:50],
            "warnings": warnings[:50],
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

    def generate_features(self, dataset_id: str, dataset_version_id: str | None = None) -> dict:
        version = self.resolve_dataset_version(dataset_id, dataset_version_id)
        dataset_id = version["dataset_id"]
        wavelengths = expected_wavelengths(load_filter_config())
        feature_dir = self.artifact_dir / "features"
        feature_dir.mkdir(parents=True, exist_ok=True)
        output_csv = feature_dir / f"{version['dataset_version_id']}_features.csv"
        rows = []
        failures = []
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
                record = extract_feature_record(sample_path, sample_id=sample["sample_id"], allow_uncalibrated=True)
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
            label_count = conn.execute(
                "SELECT COUNT(*) FROM labels WHERE dataset_id=? AND (ssc IS NOT NULL OR ta IS NOT NULL OR ph IS NOT NULL)",
                (dataset_id,),
            ).fetchone()[0]
            latest = conn.execute("SELECT * FROM dataset_versions WHERE dataset_id=? ORDER BY version DESC LIMIT 1", (dataset_id,)).fetchone()
            version_no = int(latest["version"]) + 1 if latest else 1
            parent_version = latest["dataset_version_id"] if latest else None
            sample_ids = [row["sample_id"] for row in samples]
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
            conn.execute("UPDATE datasets SET dirty=0, latest_version_id=? WHERE dataset_id=?", (version_id, dataset_id))
        self.log("dataset.version.create", "dataset", dataset_id, f"{dataset['dataset_name']} {version_name} created")
        return self.get_dataset_version(version_id)

    def get_dataset_version(self, dataset_version_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM dataset_versions WHERE dataset_version_id=?", (dataset_version_id,)).fetchone()
        if not row:
            raise ModelStudioError(f"dataset version not found: {dataset_version_id}")
        return dict(row)

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
                    payload.get("fruit_type") or payload.get("fruitType") or dataset.get("fruit_type") or "",
                    _normalize_variety(payload.get("variety") or dataset.get("variety") or ""),
                    payload.get("parent_experiment_id") or payload.get("parentExperimentId") or None,
                    payload.get("parent_model_id") or payload.get("parentModelId") or None,
                    _now(),
                ),
            )
        self.log("experiment.create", "experiment", experiment_id, f"Experiment created: {name}")
        return self.get_experiment(experiment_id)

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
            return [dict(row) for row in conn.execute("SELECT * FROM training_experiments ORDER BY created_at DESC")]

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
            return [dict(row) for row in conn.execute("SELECT * FROM models ORDER BY created_at DESC")]

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
                dst = self.production_dir / "published" / model_id
                if dst.exists():
                    shutil.rmtree(dst)
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / "model.joblib", dst / "model.joblib")
                metadata = json.loads((src / "metadata.json").read_text(encoding="utf-8"))
                display_name = payload.get("displayName") or payload.get("display_name") or model.get("display_name") or model["model_name"]
                version = payload.get("version") or model.get("version") or metadata.get("model_version") or ""
                metadata.update({
                    "model_id": model_id,
                    "display_name": display_name,
                    "model_version": version,
                    "status": "Published",
                    "published_at": _now(),
                    "fruit_type": model.get("fruit_type") or "",
                    "variety": _normalize_variety(model.get("variety") or ""),
                })
                (dst / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
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
                      notes=COALESCE(NULLIF(?, ''), notes)
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

    def set_default_model(self, model_id: str) -> dict:
        with self._lock:
            with self.connect() as conn:
                row = conn.execute("SELECT * FROM models WHERE model_id=?", (model_id,)).fetchone()
                if not row:
                    raise ModelStudioError(f"model not found: {model_id}")
                model = dict(row)
                if model["status"] not in {"Published", "Default", "Production"}:
                    raise ModelStudioError("only published models can be set as default")
                self._clear_default_in_scope(conn, model)
                conn.execute("UPDATE models SET status='Default', is_default=1 WHERE model_id=?", (model_id,))
                src = Path(model["model_dir"])
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
        where = "WHERE status IN ('Published','Default','Production')"
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
        return rows

    def model_catalog(self, *, fruit_type: str = "", variety: str = "") -> dict:
        with self.connect() as conn:
            fruit_types = [
                row["fruit_type"] for row in conn.execute(
                    """
                    SELECT DISTINCT fruit_type FROM models
                    WHERE status IN ('Published','Default','Production') AND COALESCE(fruit_type,'')!=''
                    ORDER BY fruit_type
                    """
                )
            ]
            variety_params: list[object] = []
            variety_where = "WHERE status IN ('Published','Default','Production')"
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
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM models WHERE model_id=?", (model_id,)).fetchone()
        if not row:
            raise ModelStudioError(f"model not found: {model_id}")
        return dict(row)

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
                    )
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
            "warnings": warnings,
            "structure": structure,
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
            conn.execute("UPDATE datasets SET dirty=1 WHERE dataset_id=?", (dataset_id,))

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
                INSERT INTO samples(dataset_id,sample_id,fruit_type,variety,sample_name,storage_path,source_path,local_path,rgb_count,multispectral_count,dark_count,white_count,available_bands,calibration_status,data_status,quality_json,created_at,imported_at)
                VALUES(:dataset_id,:sample_id,:fruit_type,:variety,:sample_name,:storage_path,:source_path,:local_path,:rgb_count,:multispectral_count,:dark_count,:white_count,:available_bands,:calibration_status,:data_status,:quality_json,:created_at,:imported_at)
                ON CONFLICT(dataset_id,sample_id) DO UPDATE SET
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
                "UPDATE datasets SET sample_count=?, label_count=?, calibration_status=? WHERE dataset_id=?",
                (sample_count, label_count, calibration, dataset_id),
            )

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
        with (model_output / "metadata.json").open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=2)
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
                "UPDATE jobs SET status='Failed',step='Failed',message=?,error=?,finished_at=? WHERE job_id=?",
                (error, trace, _now(), job_id),
            )

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


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _none_if_nan(value):
    try:
        if value != value:
            return None
    except Exception:
        pass
    return value
