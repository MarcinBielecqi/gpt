from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_CANON_DB_PATH = Path("data/canon.sqlite")
DEFAULT_ANALYSIS_DB_PATH = Path("data/project_bus.sqlite")
CANON_SCHEMA = "canon_db"


def connect_workspace(db_path: str | Path, canon_db_path: str | Path | None = None) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=60)
    connection.execute("PRAGMA busy_timeout = 60000")
    if canon_db_path:
        canon_path = Path(canon_db_path)
        canon_path.parent.mkdir(parents=True, exist_ok=True)
        escaped = str(canon_path).replace("'", "''")
        connection.execute(f"ATTACH DATABASE '{escaped}' AS {CANON_SCHEMA}")
        connection.execute(f"PRAGMA {CANON_SCHEMA}.busy_timeout = 60000")
    return connection


def has_canon_attachment(connection: sqlite3.Connection) -> bool:
    return any(row[1] == CANON_SCHEMA for row in connection.execute("PRAGMA database_list").fetchall())


def canon_table(connection: sqlite3.Connection, table_name: str) -> str:
    return f"{CANON_SCHEMA}.{table_name}" if has_canon_attachment(connection) else table_name


def canon_index(connection: sqlite3.Connection, index_name: str) -> str:
    return f"{CANON_SCHEMA}.{index_name}" if has_canon_attachment(connection) else index_name


def canon_foreign_key_clause(connection: sqlite3.Connection) -> str:
    return "" if has_canon_attachment(connection) else ",\n            FOREIGN KEY (parcel_id) REFERENCES canon_parcels(parcel_id) ON DELETE CASCADE"
