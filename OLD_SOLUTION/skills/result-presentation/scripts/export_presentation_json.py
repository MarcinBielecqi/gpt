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

from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, connect_workspace, table_exists

DEFAULT_DB_PATH = DEFAULT_ANALYSIS_DB_PATH

QUERIES = {
    "run_summary": "",
    "top_layer1_candidates": """
        SELECT cell_id, category, tag_key, tag_value, score, point_count,
               bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon
        FROM helper_osm_hotspot_mesh_cells
        WHERE run_id = :run_id
        ORDER BY score DESC, cell_id
        LIMIT :limit
    """,
    "layer2_parcels": """
        SELECT p.parcel_id, p.parcel_number, p.commune, p.county, p.voivodeship,
               ROUND(p.area_m2, 1) AS area_m2,
               p.bbox_min_lat, p.bbox_min_lon, p.bbox_max_lat, p.bbox_max_lon,
               rp.candidate_index, rp.source_bbox
        FROM helper_layer2_run_parcels rp
        JOIN canon_parcels p ON p.parcel_id = rp.parcel_id
        WHERE rp.run_id = :run_id
        ORDER BY rp.candidate_index, p.parcel_id
        LIMIT :limit
    """,
    "layer3_rcn_summary": """
        SELECT
            COUNT(*) AS rcn_records,
            SUM(CASE WHEN price_per_m2 IS NOT NULL AND price_per_m2 > 0 THEN 1 ELSE 0 END) AS priced_records,
            ROUND(MIN(price_per_m2), 2) AS min_price_per_m2,
            ROUND(AVG(price_per_m2), 2) AS avg_price_per_m2,
            ROUND(MAX(price_per_m2), 2) AS max_price_per_m2,
            SUM(CASE WHEN inflation_adjusted_price_per_m2 IS NOT NULL AND inflation_adjusted_price_per_m2 > 0 THEN 1 ELSE 0 END) AS inflation_adjusted_priced_records,
            ROUND(AVG(inflation_adjusted_price_per_m2), 2) AS avg_inflation_adjusted_price_per_m2,
            MAX(inflation_reference_year) AS inflation_reference_year
        FROM canon_rcn_price_observations
        WHERE run_id = :run_id
    """,
    "deriv_parcel_geometry_features": """
        SELECT gf.parcel_id, ROUND(gf.area_m2, 1) AS area_m2,
               ROUND(gf.perimeter_m, 1) AS perimeter_m,
               ROUND(gf.compactness, 4) AS compactness,
               ROUND(gf.elongation_ratio, 4) AS elongation_ratio,
               ROUND(gf.centroidal_ixx_m4, 3) AS centroidal_ixx_m4,
               ROUND(gf.centroidal_iyy_m4, 3) AS centroidal_iyy_m4,
               ROUND(gf.centroidal_ixy_m4, 3) AS centroidal_ixy_m4,
               ROUND(gf.principal_moment_min_m4, 3) AS principal_moment_min_m4,
               ROUND(gf.principal_moment_max_m4, 3) AS principal_moment_max_m4
        FROM deriv_parcel_geometry_features gf
        JOIN helper_layer2_run_parcels rp ON rp.parcel_id = gf.parcel_id
        WHERE rp.run_id = :run_id
        ORDER BY gf.elongation_ratio DESC, gf.parcel_id
        LIMIT :limit
    """,
    "deriv_parcel_visual_features": """
        SELECT vf.parcel_id,
               vf.masked_pixel_count,
               vf.tile_count,
               vf.zoom,
               ROUND(vf.brightness_mean, 2) AS brightness_mean,
               ROUND(vf.green_index_mean, 4) AS green_index_mean,
               ROUND(vf.green_pixel_ratio, 4) AS green_pixel_ratio,
               ROUND(vf.dark_pixel_ratio, 4) AS dark_pixel_ratio,
               ROUND(vf.bright_pixel_ratio, 4) AS bright_pixel_ratio,
               ROUND(vf.low_saturation_ratio, 4) AS low_saturation_ratio
        FROM deriv_parcel_visual_features vf
        JOIN helper_layer2_run_parcels rp ON rp.parcel_id = vf.parcel_id
        WHERE rp.run_id = :run_id
        ORDER BY vf.green_pixel_ratio DESC, vf.parcel_id
        LIMIT :limit
    """,
}


def rows_as_dicts(connection: sqlite3.Connection, query_name: str, params: dict) -> list[dict]:
    connection.row_factory = sqlite3.Row
    if query_name == "run_summary":
        return run_summary(connection, params["run_id"])
    if query_name == "layer2_parcels" and not table_exists(connection, "helper_layer2_run_parcels"):
        return []
    if query_name == "layer3_rcn_summary" and not table_exists(connection, "canon_rcn_price_observations"):
        return []
    if query_name == "deriv_parcel_geometry_features" and not table_exists(connection, "deriv_parcel_geometry_features"):
        return []
    if query_name == "deriv_parcel_visual_features" and not table_exists(connection, "deriv_parcel_visual_features"):
        return []
    rows = connection.execute(QUERIES[query_name], params).fetchall()
    return [dict(row) for row in rows]


def run_summary(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    layer2_count = 0
    if table_exists(connection, "helper_layer2_run_parcels"):
        layer2_count = scalar(connection, "SELECT COUNT(*) FROM helper_layer2_run_parcels WHERE run_id = ?", (run_id,))
    layer3_count = 0
    if table_exists(connection, "canon_rcn_price_observations"):
        layer3_count = scalar(connection, "SELECT COUNT(*) FROM canon_rcn_price_observations WHERE run_id = ?", (run_id,))
    geometry_feature_count = 0
    if table_exists(connection, "deriv_parcel_geometry_features") and table_exists(connection, "helper_layer2_run_parcels"):
        geometry_feature_count = scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM deriv_parcel_geometry_features gf
            JOIN helper_layer2_run_parcels rp ON rp.parcel_id = gf.parcel_id
            WHERE rp.run_id = ?
            """,
            (run_id,),
        )
    visual_feature_count = 0
    if table_exists(connection, "deriv_parcel_visual_features") and table_exists(connection, "helper_layer2_run_parcels"):
        visual_feature_count = scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM deriv_parcel_visual_features vf
            JOIN helper_layer2_run_parcels rp ON rp.parcel_id = vf.parcel_id
            WHERE rp.run_id = ?
            """,
            (run_id,),
        )
    return [
        {"metric": "canon_osm_features", "value": scalar(connection, "SELECT COUNT(*) FROM canon_osm_features")},
        {
            "metric": "mesh_cells",
            "value": scalar(connection, "SELECT COUNT(*) FROM helper_osm_hotspot_mesh_cells WHERE run_id = ?", (run_id,)),
        },
        {
            "metric": "mesh_categories",
            "value": scalar(
                connection,
                "SELECT COUNT(DISTINCT category) FROM helper_osm_hotspot_mesh_cells WHERE run_id = ?",
                (run_id,),
            ),
        },
        {
            "metric": "max_mesh_score",
            "value": scalar_number(connection, "SELECT COALESCE(MAX(score), 0) FROM helper_osm_hotspot_mesh_cells WHERE run_id = ?", (run_id,)),
        },
        {"metric": "helper_layer2_run_parcels", "value": layer2_count},
        {"metric": "canon_rcn_price_observations", "value": layer3_count},
        {"metric": "deriv_parcel_geometry_features", "value": geometry_feature_count},
        {"metric": "deriv_parcel_visual_features", "value": visual_feature_count},
    ]


def scalar(connection: sqlite3.Connection, query: str, params: tuple = ()) -> int:
    return int(connection.execute(query, params).fetchone()[0])


def scalar_number(connection: sqlite3.Connection, query: str, params: tuple = ()) -> int | float:
    value = connection.execute(query, params).fetchone()[0]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a small presentation JSON from a named read-only SQLite query.")
    parser.add_argument("--query-name", required=True, choices=sorted(QUERIES))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--expected-commune")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    params = {"run_id": args.run_id, "limit": args.limit, "expected_commune": args.expected_commune}

    connection = connect_workspace(args.db_path, args.canon_db_path)
    try:
        rows = rows_as_dicts(connection, args.query_name, params)
    finally:
        connection.close()

    payload = {
        "query_name": args.query_name,
        "run_id": args.run_id,
        "row_count": len(rows),
        "rows": rows,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "query_name": args.query_name, "row_count": len(rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
