#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH

DEFAULT_DB_PATH = DEFAULT_ANALYSIS_DB_PATH
HELPER_TABLES = ["helper_osm_hotspot_mesh_cells", "helper_layer2_run_parcels"]


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)).fetchone() is not None


def cleanup(db_path: Path) -> dict:
    connection = sqlite3.connect(db_path)
    before = {}
    after = {}
    try:
        for table in HELPER_TABLES:
            if not table_exists(connection, table):
                continue
            before[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            connection.execute(f'DELETE FROM "{table}"')
            after[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        connection.commit()
    finally:
        connection.close()
    return {"db_path": str(db_path), "cleared_tables": HELPER_TABLES, "before": before, "after": after}


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear helper_* run-output tables. Canonical and derived tables are never touched.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()
    try:
        summary = cleanup(Path(args.db_path))
    except sqlite3.DatabaseError as error:
        print(json.dumps({"db_path": args.db_path, "error": f"database unreadable: {error}"}, ensure_ascii=True, sort_keys=True))
        return 1
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
