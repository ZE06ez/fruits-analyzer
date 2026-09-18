from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable


APP_VERSION = "1.1.0-p1e6"
RUNTIME_ENV = "FRUIT_TASTE_ANALYZER_RUNTIME_DIR"


class RuntimeConfigurationError(RuntimeError):
    code = "CONFIG_INVALID"


class DatabaseMigrationError(RuntimeError):
    code = "DATABASE_MIGRATION_FAILED"


def runtime_data_dir(app_dir: str | Path) -> Path:
    """Return mutable data storage without moving development-test fixtures."""
    configured = os.environ.get(RUNTIME_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return (Path(base) / "FruitTasteAnalyzer" / "app_data").resolve()
    return Path(app_dir).resolve()


@dataclass(frozen=True)
class RuntimePaths:
    root: Path

    @classmethod
    def for_app(cls, app_dir: str | Path) -> "RuntimePaths":
        root = runtime_data_dir(app_dir)
        for path in (root, root / "config", root / "database", root / "logs", root / "backups", root / "outputs"):
            path.mkdir(parents=True, exist_ok=True)
        return cls(root=root)

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def database_dir(self) -> Path:
        return self.root / "database"


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    with temporary.open("w", encoding=encoding, newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def atomic_write_json(path: str | Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def load_json_config(path: str | Path, *, required: bool = False) -> dict[str, Any] | None:
    source = Path(path)
    if not source.exists():
        if required:
            raise RuntimeConfigurationError(f"CONFIG_INVALID: required config is missing: {source.name}")
        return None
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeConfigurationError(f"CONFIG_INVALID: could not parse {source.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeConfigurationError(f"CONFIG_INVALID: {source.name} root must be an object")
    return value


def bootstrap_config(example_path: str | Path, runtime_path: str | Path) -> Path:
    """Install an example once; later application upgrades never overwrite it."""
    example, runtime = Path(example_path), Path(runtime_path)
    if not runtime.exists() and example.exists():
        atomic_write_text(runtime, example.read_text(encoding="utf-8"))
    return runtime


def backup_sqlite_database(database_path: str | Path, backup_dir: str | Path) -> Path | None:
    source_path = Path(database_path)
    if not source_path.exists() or source_path.stat().st_size == 0:
        return None
    destination_dir = Path(backup_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{source_path.stem}.pre_migration.sqlite"
    # Keep the most recent recoverable copy without relying on a copied WAL file.
    with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    return destination


Migration = tuple[int, Callable[[sqlite3.Connection], None]]


def migrate_sqlite(
    conn: sqlite3.Connection,
    *,
    database_name: str,
    migrations: list[Migration],
) -> int:
    """Apply numbered migrations atomically and record each successful version."""
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        current = int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0])
        for version, migration in migrations:
            if version <= current:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                migration(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(?, datetime('now'))",
                    (version,),
                )
                conn.execute(f"PRAGMA user_version={int(version)}")
                conn.commit()
                current = version
            except Exception:
                conn.rollback()
                raise
        return current
    except Exception as exc:
        raise DatabaseMigrationError(f"DATABASE_MIGRATION_FAILED: {database_name}") from exc


def configure_logging(app_dir: str | Path) -> logging.Logger:
    paths = RuntimePaths.for_app(app_dir)
    logger = logging.getLogger("fruit_taste_analyzer")
    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        log_path = paths.root / "logs" / "fruit_taste_analyzer.log"
        try:
            handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        except OSError:
            # A source tree or Program Files can be readable but not writable.
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
            fallback = Path(base) / "FruitTasteAnalyzer" / "app_data" / "logs" / "fruit_taste_analyzer.log"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(fallback, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def shutdown_logging() -> None:
    logger = logging.getLogger("fruit_taste_analyzer")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
