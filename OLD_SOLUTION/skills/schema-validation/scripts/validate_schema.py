#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, connect_workspace, table_exists


LEGACY_TABLE_NAMES = {
    "osm_features",
    "osm_hotspot_mesh_cells",
    "parcels",
    "parcel_polygon_points",
    "layer2_run_parcels",
    "parcel_geometry_features",
    "parcel_visual_features",
    "parcel_railway_features",
    "rcn_price_observations",
}


def unique_columns(connection: sqlite3.Connection, table: str) -> list[tuple[str, ...]]:
    uniques = []
    for index in connection.execute(f"PRAGMA index_list({table})").fetchall():
        if not index[2]:
            continue
        cols = tuple(row[2] for row in connection.execute(f"PRAGMA index_info({index[1]})").fetchall())
        uniques.append(cols)
    return uniques


def validate(
    db_path: Path,
    canon_db_path: Path | None = None,
    require_layer2: bool = False,
    require_layer3: bool = False,
    require_geometry_features: bool = False,
    require_visual_features: bool = False,
) -> list[str]:
    errors = []
    connection = connect_workspace(db_path, canon_db_path)
    try:
        legacy_tables = sorted(name for name in LEGACY_TABLE_NAMES if table_exists(connection, name))
        for table in legacy_tables:
            errors.append(f"legacy unprefixed table remains: {table}")

        if not table_exists(connection, "canon_osm_features"):
            errors.append("missing table: canon_osm_features")
        else:
            if ("osm_type", "osm_id") not in unique_columns(connection, "canon_osm_features"):
                errors.append("canon_osm_features missing UNIQUE(osm_type, osm_id)")
            duplicate_count = connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT osm_type, osm_id
                    FROM canon_osm_features
                    GROUP BY osm_type, osm_id
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
            if duplicate_count:
                errors.append(f"canon_osm_features has duplicate OSM objects: {duplicate_count}")

        if not table_exists(connection, "helper_osm_hotspot_mesh_cells"):
            errors.append("missing table: helper_osm_hotspot_mesh_cells")
        else:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(helper_osm_hotspot_mesh_cells)").fetchall()}
            if "run_id" not in columns:
                errors.append("helper_osm_hotspot_mesh_cells missing run_id")
            else:
                missing_run_rows = connection.execute(
                    "SELECT COUNT(*) FROM helper_osm_hotspot_mesh_cells WHERE run_id IS NULL OR run_id = ''"
                ).fetchone()[0]
                if missing_run_rows:
                    errors.append(f"helper_osm_hotspot_mesh_cells rows missing run_id: {missing_run_rows}")

        if require_layer2:
            if not table_exists(connection, "canon_parcels"):
                errors.append("missing table: canon_parcels")
            else:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(canon_parcels)").fetchall()}
                for column in ("parcel_id", "bbox_min_lat", "bbox_min_lon", "bbox_max_lat", "bbox_max_lon", "geometry_hash"):
                    if column not in columns:
                        errors.append(f"canon_parcels missing {column}")
                duplicate_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT parcel_id
                        FROM canon_parcels
                        GROUP BY parcel_id
                        HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
                if duplicate_count:
                    errors.append(f"canon_parcels has duplicate parcel IDs: {duplicate_count}")
            if not table_exists(connection, "canon_parcel_polygon_points"):
                errors.append("missing table: canon_parcel_polygon_points")
            if not table_exists(connection, "helper_layer2_run_parcels"):
                errors.append("missing table: helper_layer2_run_parcels")
            else:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(helper_layer2_run_parcels)").fetchall()}
                for column in ("run_id", "parcel_id", "candidate_index", "source_bbox"):
                    if column not in columns:
                        errors.append(f"helper_layer2_run_parcels missing {column}")
                if table_exists(connection, "canon_parcels"):
                    orphan_count = connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM helper_layer2_run_parcels rp
                        WHERE NOT EXISTS (
                            SELECT 1 FROM canon_parcels p WHERE p.parcel_id = rp.parcel_id
                        )
                        """
                    ).fetchone()[0]
                    if orphan_count:
                        errors.append(f"helper_layer2_run_parcels has orphan parcel links: {orphan_count}")
        if require_layer3:
            if not table_exists(connection, "canon_rcn_price_observations"):
                errors.append("missing table: canon_rcn_price_observations")
            else:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(canon_rcn_price_observations)").fetchall()}
                for column in (
                    "source",
                    "source_record_id",
                    "run_id",
                    "raw_json",
                    "price_per_m2",
                    "inflation_reference_year",
                    "inflation_factor",
                    "inflation_adjusted_price_pln",
                    "inflation_adjusted_price_per_m2",
                ):
                    if column not in columns:
                        errors.append(f"canon_rcn_price_observations missing {column}")
                duplicate_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT source, source_record_id, run_id
                        FROM canon_rcn_price_observations
                        GROUP BY source, source_record_id, run_id
                        HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
                if duplicate_count:
                    errors.append(f"canon_rcn_price_observations has duplicate source records per run: {duplicate_count}")
        if require_geometry_features:
            if not table_exists(connection, "deriv_parcel_geometry_features"):
                errors.append("missing table: deriv_parcel_geometry_features")
            else:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(deriv_parcel_geometry_features)").fetchall()}
                for column in (
                    "parcel_id",
                    "area_m2",
                    "centroidal_ixx_m4",
                    "centroidal_iyy_m4",
                    "centroidal_ixy_m4",
                    "principal_moment_min_m4",
                    "principal_moment_max_m4",
                    "elongation_ratio",
                    "compactness",
                ):
                    if column not in columns:
                        errors.append(f"deriv_parcel_geometry_features missing {column}")
                duplicate_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT parcel_id
                        FROM deriv_parcel_geometry_features
                        GROUP BY parcel_id
                        HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
                if duplicate_count:
                    errors.append(f"deriv_parcel_geometry_features has duplicate parcel IDs: {duplicate_count}")
                if table_exists(connection, "canon_parcels"):
                    orphan_count = connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM deriv_parcel_geometry_features gf
                        WHERE NOT EXISTS (
                            SELECT 1 FROM canon_parcels p WHERE p.parcel_id = gf.parcel_id
                        )
                        """
                    ).fetchone()[0]
                    if orphan_count:
                        errors.append(f"deriv_parcel_geometry_features has orphan parcel rows: {orphan_count}")
        if require_visual_features:
            if not table_exists(connection, "deriv_parcel_visual_features"):
                errors.append("missing table: deriv_parcel_visual_features")
            else:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(deriv_parcel_visual_features)").fetchall()}
                for column in (
                    "parcel_id",
                    "source_geometry_hash",
                    "image_source",
                    "zoom",
                    "algorithm_version",
                    "masked_pixel_count",
                    "tile_count",
                    "rgb_r_mean",
                    "rgb_g_mean",
                    "rgb_b_mean",
                    "brightness_mean",
                    "green_index_mean",
                    "green_pixel_ratio",
                    "dark_pixel_ratio",
                    "bright_pixel_ratio",
                    "low_saturation_ratio",
                ):
                    if column not in columns:
                        errors.append(f"deriv_parcel_visual_features missing {column}")
                if "area_m2" in columns:
                    errors.append("deriv_parcel_visual_features must not duplicate area_m2")
                duplicate_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT parcel_id
                        FROM deriv_parcel_visual_features
                        GROUP BY parcel_id
                        HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
                if duplicate_count:
                    errors.append(f"deriv_parcel_visual_features has duplicate parcel IDs: {duplicate_count}")
                if table_exists(connection, "canon_parcels"):
                    orphan_count = connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM deriv_parcel_visual_features vf
                        WHERE NOT EXISTS (
                            SELECT 1 FROM canon_parcels p WHERE p.parcel_id = vf.parcel_id
                        )
                        """
                    ).fetchone()[0]
                    if orphan_count:
                        errors.append(f"deriv_parcel_visual_features has orphan parcel rows: {orphan_count}")
    finally:
        connection.close()
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate minimal analysis SQLite schema.")
    parser.add_argument("--db-path", default=str(DEFAULT_ANALYSIS_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--require-linked-parcels", dest="require_linked_parcels", action="store_true")
    parser.add_argument("--require-layer2", dest="require_linked_parcels", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--require-price-observations", dest="require_price_observations", action="store_true")
    parser.add_argument("--require-layer3", dest="require_price_observations", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--require-geometry-features", action="store_true")
    parser.add_argument("--require-visual-features", action="store_true")
    args = parser.parse_args()

    try:
        errors = validate(
            Path(args.db_path),
            Path(args.canon_db_path) if args.canon_db_path else None,
            args.require_linked_parcels,
            args.require_price_observations,
            args.require_geometry_features,
            args.require_visual_features,
        )
    except sqlite3.DatabaseError as error:
        print(f"ERROR: database unreadable: {error}")
        return 1
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Schema validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
