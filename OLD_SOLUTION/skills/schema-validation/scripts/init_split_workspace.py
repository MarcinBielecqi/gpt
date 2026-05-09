#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, connect_workspace


def load_module(relative_path: str, module_name: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table_counts(connection, schema: str) -> dict[str, int]:
    rows = connection.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return {name: int(connection.execute(f'SELECT COUNT(*) FROM {schema}."{name}"').fetchone()[0]) for (name,) in rows}


def init_workspace(analysis_db_path: Path, canon_db_path: Path, replace: bool = False) -> dict:
    if replace:
        for path in (analysis_db_path, canon_db_path):
            path.unlink(missing_ok=True)
            path.with_name(path.name + "-journal").unlink(missing_ok=True)
            path.with_name(path.name + "-wal").unlink(missing_ok=True)
            path.with_name(path.name + "-shm").unlink(missing_ok=True)

    connection = connect_workspace(analysis_db_path, canon_db_path)
    try:
        hotspot = load_module("skills/osm_hotspot_grid/scripts/build_hotspot_grid.py", "build_hotspot_grid")
        layer2 = load_module("skills/uldk-parcel-grid/scripts/probe_uldk_parcels.py", "probe_uldk_parcels")
        layer3 = load_module("skills/poland-rcn-wfs/scripts/fetch_rcn_wfs.py", "fetch_rcn_wfs")
        geometry = load_module("skills/parcel-geometry-features/scripts/compute_parcel_geometry_features.py", "compute_parcel_geometry_features")
        visual = load_module("skills/parcel-visual-features/scripts/compute_parcel_visual_features.py", "compute_parcel_visual_features")

        hotspot.ensure_layer1_tables(connection)
        layer2.ensure_layer2_tables(connection)
        layer3.ensure_layer3_tables(connection)
        geometry.ensure_geometry_feature_table(connection)
        visual.ensure_visual_feature_table(connection)
        connection.commit()

        return {
            "analysis_db_path": str(analysis_db_path),
            "canon_db_path": str(canon_db_path),
            "analysis_tables": table_counts(connection, "main"),
            "canon_tables": table_counts(connection, "canon_db"),
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the split SQLite workspace: canon DB plus local analysis DB.")
    parser.add_argument("--db-path", default=str(DEFAULT_ANALYSIS_DB_PATH), help="Local analysis DB for deriv_* and helper_* tables.")
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH), help="Git-synced canonical DB for canon_* tables.")
    parser.add_argument("--replace", action="store_true", help="Replace only the target split DB files, not old parcel_workspace.sqlite.")
    args = parser.parse_args()

    summary = init_workspace(Path(args.db_path), Path(args.canon_db_path), args.replace)
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
