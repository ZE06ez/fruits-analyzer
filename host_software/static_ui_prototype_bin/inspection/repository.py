from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_support import DatabaseMigrationError, backup_sqlite_database, migrate_sqlite


INSPECTION_SCHEMA_VERSION = 2


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class InspectionRepository:
    """SQLite repository kept separate from the Model Studio database."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.migration_backup_path: Path | None = None
        self.init_db()
        self.reconcile_interrupted_inspections()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self) -> None:
        existing = self.database_path.exists() and self.database_path.stat().st_size > 0
        if existing and self._schema_version_before_init() < INSPECTION_SCHEMA_VERSION:
            self.migration_backup_path = backup_sqlite_database(self.database_path, self.database_path.parent.parent / "backups")
        try:
            with self.connect() as conn:
                conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inspections (
                    inspection_id TEXT PRIMARY KEY,
                    sample_id TEXT NOT NULL,
                    sample_name TEXT NOT NULL,
                    fruit_type TEXT,
                    variety TEXT,
                    sample_mode TEXT,
                    sample_path TEXT,
                    source_files_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    software_version TEXT NOT NULL,
                    pipeline_signature TEXT,
                    pipeline_contract_json TEXT NOT NULL,
                    feature_pipeline_json TEXT NOT NULL,
                    calibration_json TEXT NOT NULL,
                    background_reference_json TEXT NOT NULL,
                    registration_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
                );

                CREATE TABLE IF NOT EXISTS prediction_results (
                    result_id TEXT PRIMARY KEY,
                    inspection_id TEXT NOT NULL REFERENCES inspections(inspection_id) ON DELETE CASCADE,
                    target TEXT NOT NULL,
                    value REAL,
                    unit TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    error_message TEXT,
                    model_id TEXT,
                    model_name TEXT,
                    model_version TEXT,
                    model_type TEXT,
                    preprocessing TEXT,
                    model_pipeline_signature TEXT,
                    model_input_contract_json TEXT NOT NULL,
                    elapsed_time REAL,
                    created_at TEXT NOT NULL,
                    UNIQUE(inspection_id, target)
                );

                CREATE INDEX IF NOT EXISTS idx_inspections_created_at ON inspections(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_inspections_scope ON inspections(fruit_type, variety);
                CREATE INDEX IF NOT EXISTS idx_inspections_status ON inspections(status);
                CREATE INDEX IF NOT EXISTS idx_prediction_results_inspection ON prediction_results(inspection_id);
                """
                )
                self.schema_version = migrate_sqlite(
                    conn,
                    database_name="inspection",
                    migrations=[(1, self._migration_v1), (2, self._migration_v2)],
                )
        except (sqlite3.Error, DatabaseMigrationError) as exc:
            raise RuntimeError("DATABASE_MIGRATION_FAILED: inspection") from exc

    def _schema_version_before_init(self) -> int:
        try:
            with sqlite3.connect(self.database_path) as conn:
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
                return int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]) if exists else 0
        except sqlite3.Error as exc:
            raise RuntimeError("DATABASE_MIGRATION_FAILED: inspection") from exc

    @staticmethod
    def _migration_v1(conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(inspections)")}
        for column, ddl in {
            "pipeline_signature": "TEXT", "pipeline_contract_json": "TEXT NOT NULL DEFAULT '{}'",
            "feature_pipeline_json": "TEXT NOT NULL DEFAULT '{}'", "calibration_json": "TEXT NOT NULL DEFAULT '{}'",
            "background_reference_json": "TEXT NOT NULL DEFAULT '{}'", "registration_json": "TEXT NOT NULL DEFAULT '{}'",
            "archived_at": "TEXT",
        }.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE inspections ADD COLUMN {column} {ddl}")
        result_columns = {row["name"] for row in conn.execute("PRAGMA table_info(prediction_results)")}
        for column, ddl in {"model_pipeline_signature": "TEXT", "model_input_contract_json": "TEXT NOT NULL DEFAULT '{}'"}.items():
            if column not in result_columns:
                conn.execute(f"ALTER TABLE prediction_results ADD COLUMN {column} {ddl}")

    @staticmethod
    def _migration_v2(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inspections_scope ON inspections(fruit_type, variety)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inspections_status ON inspections(status)")

    def reconcile_interrupted_inspections(self) -> int:
        with self._lock, self.connect() as conn:
            result = conn.execute(
                """
                UPDATE inspections SET status='FAILED_RECOVERABLE', completed_at=COALESCE(completed_at, ?), updated_at=?
                WHERE status IN ('CREATED', 'RUNNING')
                """,
                (utc_timestamp(), utc_timestamp()),
            )
        return int(result.rowcount)

    def create(self, provenance: dict[str, Any]) -> str:
        inspection_id = str(provenance.get("inspection_id") or f"I{uuid.uuid4().hex}")
        now = utc_timestamp()
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO inspections(
                    inspection_id,sample_id,sample_name,fruit_type,variety,sample_mode,sample_path,
                    source_files_json,started_at,completed_at,status,software_version,
                    pipeline_signature,pipeline_contract_json,feature_pipeline_json,calibration_json,
                    background_reference_json,registration_json,created_at,updated_at,archived_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
                """,
                (
                    inspection_id, str(provenance.get("sample_id") or ""), str(provenance.get("sample_name") or ""),
                    str(provenance.get("fruit_type") or ""), str(provenance.get("variety") or "generic"),
                    str(provenance.get("sample_mode") or "inspection"), str(provenance.get("sample_path") or ""),
                    _json(provenance.get("source_files") or []), str(provenance.get("started_at") or now),
                    provenance.get("completed_at"), str(provenance.get("status") or "CREATED"),
                    str(provenance.get("software_version") or ""), str(provenance.get("pipeline_signature") or ""),
                    _json(provenance.get("pipeline_contract") or {}), _json(provenance.get("feature_pipeline") or {}),
                    _json(provenance.get("calibration") or {}), _json(provenance.get("background_reference") or {}),
                    _json(provenance.get("registration") or {}), now, now,
                ),
            )
        return inspection_id

    def save_results(self, inspection_id: str, *, inspection_updates: dict[str, Any], results: list[dict[str, Any]]) -> None:
        now = utc_timestamp()
        with self._lock, self.connect() as conn:
            if not conn.execute("SELECT 1 FROM inspections WHERE inspection_id=?", (inspection_id,)).fetchone():
                raise KeyError(f"inspection not found: {inspection_id}")
            conn.execute(
                """
                UPDATE inspections SET status=?,completed_at=?,pipeline_signature=?,
                    pipeline_contract_json=?,feature_pipeline_json=?,calibration_json=?,
                    background_reference_json=?,registration_json=?,updated_at=?
                WHERE inspection_id=?
                """,
                (
                    inspection_updates.get("status"), inspection_updates.get("completed_at"),
                    inspection_updates.get("pipeline_signature") or "",
                    _json(inspection_updates.get("pipeline_contract") or {}),
                    _json(inspection_updates.get("feature_pipeline") or {}),
                    _json(inspection_updates.get("calibration") or {}),
                    _json(inspection_updates.get("background_reference") or {}),
                    _json(inspection_updates.get("registration") or {}), now, inspection_id,
                ),
            )
            for result in results:
                conn.execute(
                    """
                    INSERT INTO prediction_results(
                        result_id,inspection_id,target,value,unit,status,error_code,error_message,
                        model_id,model_name,model_version,model_type,preprocessing,
                        model_pipeline_signature,model_input_contract_json,elapsed_time,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(inspection_id,target) DO UPDATE SET
                        value=excluded.value,unit=excluded.unit,status=excluded.status,
                        error_code=excluded.error_code,error_message=excluded.error_message,
                        model_id=excluded.model_id,model_name=excluded.model_name,
                        model_version=excluded.model_version,model_type=excluded.model_type,
                        preprocessing=excluded.preprocessing,
                        model_pipeline_signature=excluded.model_pipeline_signature,
                        model_input_contract_json=excluded.model_input_contract_json,
                        elapsed_time=excluded.elapsed_time,created_at=excluded.created_at
                    """,
                    (
                        str(result.get("result_id") or f"R{uuid.uuid4().hex}"), inspection_id,
                        str(result.get("target") or ""), result.get("value"), str(result.get("unit") or ""),
                        str(result.get("status") or "failed"), str(result.get("error_code") or ""),
                        str(result.get("error_message") or ""), str(result.get("model_id") or ""),
                        str(result.get("model_name") or ""), str(result.get("model_version") or ""),
                        str(result.get("model_type") or ""), str(result.get("preprocessing") or ""),
                        str(result.get("model_pipeline_signature") or ""),
                        _json(result.get("model_input_contract") or {}), result.get("elapsed_time"), now,
                    ),
                )

    def get(self, inspection_id: str) -> dict[str, Any] | None:
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM inspections WHERE inspection_id=?", (inspection_id,)).fetchone()
            if not row:
                return None
            results = conn.execute("SELECT * FROM prediction_results WHERE inspection_id=? ORDER BY target", (inspection_id,)).fetchall()
        return self._inspection(dict(row), [dict(item) for item in results])

    def list(self, *, limit: int = 20, offset: int = 0, fruit_type: str = "", variety: str = "", status: str = "", date_from: str = "", date_to: str = "") -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        where = ["archived_at IS NULL"]
        params: list[Any] = []
        if fruit_type:
            where.append("lower(fruit_type)=lower(?)")
            params.append(fruit_type)
        if variety:
            where.append("lower(variety)=lower(?)")
            params.append(variety)
        if status:
            where.append("upper(status)=upper(?)")
            params.append(status)
        if date_from:
            where.append("created_at>=?")
            params.append(date_from)
        if date_to:
            where.append("created_at<=?")
            params.append(date_to)
        predicate = " AND ".join(where)
        with self._lock, self.connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM inspections WHERE {predicate}", params).fetchone()[0])
            rows = conn.execute(f"SELECT * FROM inspections WHERE {predicate} ORDER BY created_at DESC LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                results = conn.execute("SELECT * FROM prediction_results WHERE inspection_id=? ORDER BY target", (item["inspection_id"],)).fetchall()
                items.append(self._inspection(item, [dict(result) for result in results], summary=True))
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def archive(self, inspection_id: str) -> bool:
        now = utc_timestamp()
        with self._lock, self.connect() as conn:
            result = conn.execute("UPDATE inspections SET archived_at=?,updated_at=? WHERE inspection_id=? AND archived_at IS NULL", (now, now, inspection_id))
            return result.rowcount == 1

    @staticmethod
    def _inspection(row: dict[str, Any], results: list[dict[str, Any]], summary: bool = False) -> dict[str, Any]:
        payload = {
            "inspectionId": row["inspection_id"],
            "sample": {
                "sampleId": row["sample_id"], "sampleName": row["sample_name"],
                "fruitType": row["fruit_type"] or "", "variety": row["variety"] or "generic",
                "sampleMode": row["sample_mode"] or "inspection", "samplePath": row["sample_path"] or "",
                "sourceFiles": _decode(row["source_files_json"], []),
            },
            "status": row["status"], "startedAt": row["started_at"], "completedAt": row["completed_at"],
            "softwareVersion": row["software_version"], "pipelineSignature": row["pipeline_signature"] or "",
            "pipelineContract": _decode(row["pipeline_contract_json"], {}),
            "featurePipeline": _decode(row["feature_pipeline_json"], {}),
            "calibration": _decode(row["calibration_json"], {}),
            "backgroundReference": _decode(row["background_reference_json"], {}),
            "registration": _decode(row["registration_json"], {}),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"], "results": [],
        }
        for result in results:
            payload["results"].append({
                "resultId": result["result_id"], "target": result["target"], "value": result["value"],
                "unit": result["unit"] or "", "status": result["status"], "errorCode": result["error_code"] or "",
                "errorMessage": result["error_message"] or "", "modelId": result["model_id"] or "",
                "modelName": result["model_name"] or "", "modelVersion": result["model_version"] or "",
                "modelType": result["model_type"] or "", "preprocessing": result["preprocessing"] or "",
                "modelPipelineSignature": result["model_pipeline_signature"] or "",
                "modelInputContract": _decode(result["model_input_contract_json"], {}),
                "elapsedTime": result["elapsed_time"], "createdAt": result["created_at"],
            })
        if summary:
            return {key: payload[key] for key in ("inspectionId", "sample", "status", "startedAt", "completedAt", "softwareVersion", "results")}
        return payload
