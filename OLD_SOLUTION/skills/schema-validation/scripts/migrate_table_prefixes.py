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

RENAMES = [
    ("osm_features", "canon_osm_features"),
    ("parcels", "canon_parcels"),
    ("parcel_polygon_points", "canon_parcel_polygon_points"),
    ("rcn_price_observations", "canon_rcn_price_observations"),
    ("parcel_geometry_features", "deriv_parcel_geometry_features"),
    ("parcel_visual_features", "deriv_parcel_visual_features"),
    ("parcel_railway_features", "deriv_parcel_railway_features"),
    ("osm_hotspot_mesh_cells", "helper_osm_hotspot_mesh_cells"),
    ("layer2_run_parcels", "helper_layer2_run_parcels"),
]

HELPER_TABLES = ["helper_osm_hotspot_mesh_cells", "helper_layer2_run_parcels"]


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)).fetchone() is not None


def table_count(connection: sqlite3.Connection, name: str) -> int | None:
    if not table_exists(connection, name):
        return None
    return int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])


def migrate(db_path: Path, clear_helpers: bool) -> dict:
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = OFF")
    renamed = []
    skipped = []
    helper_before = {}
    helper_after = {}
    try:
        for old, new in RENAMES:
            old_exists = table_exists(connection, old)
            new_exists = table_exists(connection, new)
            if old_exists and not new_exists:
                connection.execute(f'ALTER TABLE "{old}" RENAME TO "{new}"')
                renamed.append({"from": old, "to": new})
            elif old_exists and new_exists:
                skipped.append({"from": old, "to": new, "reason": "both_exist"})
            else:
                skipped.append({"from": old, "to": new, "reason": "old_missing"})
        if clear_helpers:
            for table in HELPER_TABLES:
                if table_exists(connection, table):
                    helper_before[table] = table_count(connection, table)
                    connection.execute(f'DELETE FROM "{table}"')
                    helper_after[table] = table_count(connection, table)
        connection.commit()
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.close()
    return {
        "db_path": str(db_path),
        "renamed": renamed,
        "skipped": skipped,
        "clear_helpers": clear_helpers,
        "helper_before": helper_before,
        "helper_after": helper_after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate SQLite tables to canon/deriv/helper prefixes.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--clear-helpers", action="store_true", help="Delete rows from helper_* tables after migration.")
    args = parser.parse_args()

    try:
        summary = migrate(Path(args.db_path), args.clear_helpers)
    except sqlite3.DatabaseError as error:
        print(json.dumps({"db_path": args.db_path, "error": f"database unreadable: {error}"}, ensure_ascii=True, sort_keys=True))
        return 1
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
