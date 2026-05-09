from __future__ import annotations

import sqlite3
from pathlib import Path


DEFAULT_ANALYSIS_DB_PATH = Path("data/analysis_workspace.sqlite")
DEFAULT_CANON_DB_PATH = Path("data/canon_workspace.sqlite")
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
    if has_canon_attachment(connection):
        return f"{CANON_SCHEMA}.{table_name}"
    return table_name


def canon_index(connection: sqlite3.Connection, index_name: str) -> str:
    if has_canon_attachment(connection):
        return f"{CANON_SCHEMA}.{index_name}"
    return index_name


def canon_foreign_key_clause(connection: sqlite3.Connection) -> str:
    if has_canon_attachment(connection):
        return ""
    return ",\n            FOREIGN KEY (parcel_id) REFERENCES canon_parcels(parcel_id) ON DELETE CASCADE"


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    for _seq, schema, _file in connection.execute("PRAGMA database_list").fetchall():
        row = connection.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name = ?", (name,)).fetchone()
        if row is not None:
            return True
    return False
