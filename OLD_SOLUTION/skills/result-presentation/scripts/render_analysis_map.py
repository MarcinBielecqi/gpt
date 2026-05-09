#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, connect_workspace, table_exists

DEFAULT_DB_PATH = DEFAULT_ANALYSIS_DB_PATH
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "analysis_map.html"
LEAFLET_VERSION = "1.9.4"
LEAFLET_CSS_URL = f"https://unpkg.com/leaflet@{LEAFLET_VERSION}/dist/leaflet.css"
LEAFLET_JS_URL = f"https://unpkg.com/leaflet@{LEAFLET_VERSION}/dist/leaflet.js"


def parse_run_ids(raw: str) -> list[str]:
    run_ids = [item.strip() for item in raw.split(",") if item.strip()]
    if not run_ids:
        raise ValueError("At least one run_id is required.")
    return run_ids


def sql_placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def download_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "parcel-result-presentation"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def ensure_leaflet_assets(output_dir: Path) -> tuple[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    css_path = output_dir / "leaflet.css"
    js_path = output_dir / "leaflet.js"
    if not css_path.exists():
        css_path.write_text(download_text(LEAFLET_CSS_URL), encoding="utf-8")
    if not js_path.exists():
        js_path.write_text(download_text(LEAFLET_JS_URL), encoding="utf-8")
    return css_path.name, js_path.name


def write_data_js(path: Path, payload: dict) -> None:
    path.write_text(
        "window.PARCEL_ANALYSIS_DATA = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )


def candidate_rows(connection: sqlite3.Connection, run_id: str | list[str], limit: int) -> list[sqlite3.Row]:
    run_ids = parse_run_ids(run_id) if isinstance(run_id, str) else run_id
    connection.row_factory = sqlite3.Row
    limit_clause = "" if limit <= 0 else "LIMIT ?"
    params: tuple = tuple(run_ids) if limit <= 0 else (*run_ids, limit)
    return connection.execute(
        f"""
        SELECT cell_id, run_id, category, tag_key, tag_value, score, norm,
               point_count, area_m2, density_per_km2, depth,
               center_lat, center_lon,
               bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon,
               bbox_input, polygon_json, points_json
        FROM helper_osm_hotspot_mesh_cells
        WHERE run_id IN ({sql_placeholders(run_ids)})
        ORDER BY score DESC, run_id, cell_id
        {limit_clause}
        """,
        params,
    ).fetchall()


def feature_from_row(row: sqlite3.Row) -> dict:
    ring = json.loads(row["polygon_json"])
    properties = {
        "cell_id": row["cell_id"],
        "run_id": row["run_id"],
        "category": row["category"],
        "tag_key": row["tag_key"],
        "tag_value": row["tag_value"],
        "score": row["score"],
        "norm": row["norm"],
        "point_count": row["point_count"],
        "area_m2": row["area_m2"],
        "density_per_km2": row["density_per_km2"],
        "depth": row["depth"],
        "center_lat": row["center_lat"],
        "center_lon": row["center_lon"],
        "bbox": f"{row['bbox_min_lat']:.6f},{row['bbox_min_lon']:.6f},{row['bbox_max_lat']:.6f},{row['bbox_max_lon']:.6f}",
    }
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [float(part.strip()) for part in raw.split(",")]
    if len(parts) != 4:
        raise ValueError(f"Invalid bbox_input: {raw}")
    return parts[0], parts[1], parts[2], parts[3]


def point_rows(connection: sqlite3.Connection, mesh_rows: list[sqlite3.Row]) -> list[dict]:
    if not mesh_rows:
        return []
    bboxes = [parse_bbox(row["bbox_input"]) for row in mesh_rows]
    min_lat = min(bbox[0] for bbox in bboxes)
    min_lon = min(bbox[1] for bbox in bboxes)
    max_lat = max(bbox[2] for bbox in bboxes)
    max_lon = max(bbox[3] for bbox in bboxes)
    requested = sorted(
        {(row["tag_key"], row["tag_value"], row["category"], row["run_id"]) for row in mesh_rows if row["tag_key"] and row["tag_value"]}
    )
    if not requested:
        return []

    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT osm_type, osm_id, tags_json, center_lat, center_lon
        FROM canon_osm_features
        WHERE center_lat BETWEEN ? AND ?
          AND center_lon BETWEEN ? AND ?
        ORDER BY osm_type, osm_id
        """,
        (min_lat, max_lat, min_lon, max_lon),
    ).fetchall()

    points = []
    seen = set()
    for row in rows:
        tags = json.loads(row["tags_json"] or "{}")
        for key, value, category, run_id in requested:
            if tags.get(key) != value:
                continue
            point_key = (row["osm_type"], row["osm_id"], category)
            if point_key in seen:
                continue
            seen.add(point_key)
            points.append(
                {
                    "osm_type": row["osm_type"],
                    "osm_id": row["osm_id"],
                    "category": category,
                    "tag_key": key,
                    "tag_value": value,
                    "run_id": run_id,
                    "name": tags.get("name", ""),
                    "lat": row["center_lat"],
                    "lon": row["center_lon"],
                }
            )
    return points


def load_selected_parcel_ids(path: str | None) -> set[str]:
    if not path:
        return set()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    top_rows = payload.get("top") or []
    return {str(row["parcel_id"]) for row in top_rows if row.get("parcel_id")}


def parcel_features(connection: sqlite3.Connection, mesh_rows: list[sqlite3.Row], selected_parcel_ids: set[str] | None = None) -> list[dict]:
    if (
        not mesh_rows
        or not table_exists(connection, "canon_parcels")
        or not table_exists(connection, "canon_parcel_polygon_points")
        or not table_exists(connection, "helper_layer2_run_parcels")
    ):
        return []
    selected_parcel_ids = selected_parcel_ids or set()
    run_ids = sorted({row["run_id"] for row in mesh_rows})
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        f"""
        SELECT parcel_id, parcel_number, commune, county, voivodeship, area_m2,
               bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon
        FROM canon_parcels
        WHERE parcel_id IN (
            SELECT parcel_id
            FROM helper_layer2_run_parcels
            WHERE run_id IN ({sql_placeholders(run_ids)})
        )
        ORDER BY parcel_id
        """,
        tuple(run_ids),
    ).fetchall()
    features = []
    has_layer3 = table_exists(connection, "canon_rcn_price_observations")
    has_geometry_features = table_exists(connection, "deriv_parcel_geometry_features")
    has_visual_features = table_exists(connection, "deriv_parcel_visual_features")
    for row in rows:
        if selected_parcel_ids and row["parcel_id"] not in selected_parcel_ids:
            continue
        price_stats = {"rcn_records": 0, "avg_price_per_m2": None}
        if has_layer3:
            price_row = connection.execute(
                f"""
                SELECT COUNT(*) AS rcn_records,
                       ROUND(AVG(price_per_m2), 2) AS avg_price_per_m2,
                       ROUND(AVG(inflation_adjusted_price_per_m2), 2) AS avg_inflation_adjusted_price_per_m2,
                       MAX(inflation_reference_year) AS inflation_reference_year
                FROM canon_rcn_price_observations
                WHERE run_id IN ({sql_placeholders(run_ids)})
                  AND parcel_id = ?
                  AND price_per_m2 IS NOT NULL
                """,
                (*run_ids, row["parcel_id"]),
            ).fetchone()
            price_stats = {
                "rcn_records": price_row["rcn_records"],
                "avg_price_per_m2": price_row["avg_price_per_m2"],
                "avg_inflation_adjusted_price_per_m2": price_row["avg_inflation_adjusted_price_per_m2"],
                "inflation_reference_year": price_row["inflation_reference_year"],
            }
        geometry_stats = {
            "shape_area_m2": None,
            "compactness": None,
            "elongation_ratio": None,
            "principal_moment_min_m4": None,
            "principal_moment_max_m4": None,
        }
        if has_geometry_features:
            shape_row = connection.execute(
                """
                SELECT area_m2, compactness, elongation_ratio,
                       principal_moment_min_m4, principal_moment_max_m4
                FROM deriv_parcel_geometry_features
                WHERE parcel_id = ?
                """,
                (row["parcel_id"],),
            ).fetchone()
            if shape_row:
                geometry_stats = {
                    "shape_area_m2": shape_row["area_m2"],
                    "compactness": shape_row["compactness"],
                    "elongation_ratio": shape_row["elongation_ratio"],
                    "principal_moment_min_m4": shape_row["principal_moment_min_m4"],
                    "principal_moment_max_m4": shape_row["principal_moment_max_m4"],
                }
        visual_stats = {
            "green_pixel_ratio": None,
            "dark_pixel_ratio": None,
            "bright_pixel_ratio": None,
            "low_saturation_ratio": None,
            "brightness_mean": None,
            "masked_pixel_count": None,
        }
        if has_visual_features:
            visual_row = connection.execute(
                """
                SELECT green_pixel_ratio, dark_pixel_ratio, bright_pixel_ratio,
                       low_saturation_ratio, brightness_mean, masked_pixel_count
                FROM deriv_parcel_visual_features
                WHERE parcel_id = ?
                """,
                (row["parcel_id"],),
            ).fetchone()
            if visual_row:
                visual_stats = {
                    "green_pixel_ratio": visual_row["green_pixel_ratio"],
                    "dark_pixel_ratio": visual_row["dark_pixel_ratio"],
                    "bright_pixel_ratio": visual_row["bright_pixel_ratio"],
                    "low_saturation_ratio": visual_row["low_saturation_ratio"],
                    "brightness_mean": visual_row["brightness_mean"],
                    "masked_pixel_count": visual_row["masked_pixel_count"],
                }
        point_rows_for_parcel = connection.execute(
            """
            SELECT polygon_index, ring_index, point_index, lon, lat
            FROM canon_parcel_polygon_points
            WHERE parcel_id = ?
            ORDER BY polygon_index, ring_index, point_index
            """,
            (row["parcel_id"],),
        ).fetchall()
        polygons: dict[int, dict[int, list[list[float]]]] = {}
        for point in point_rows_for_parcel:
            polygons.setdefault(point["polygon_index"], {}).setdefault(point["ring_index"], []).append([point["lon"], point["lat"]])
        if not polygons:
            continue
        if len(polygons) == 1:
            geometry = {"type": "Polygon", "coordinates": [ring for _, ring in sorted(polygons[0].items())]}
        else:
            geometry = {
                "type": "MultiPolygon",
                "coordinates": [
                    [ring for _, ring in sorted(rings_by_index.items())]
                    for _, rings_by_index in sorted(polygons.items())
                ],
            }
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "parcel_id": row["parcel_id"],
                    "parcel_number": row["parcel_number"],
                    "commune": row["commune"],
                    "county": row["county"],
                    "voivodeship": row["voivodeship"],
                    "area_m2": row["area_m2"],
                    "lat": (row["bbox_min_lat"] + row["bbox_max_lat"]) / 2.0,
                    "lon": (row["bbox_min_lon"] + row["bbox_max_lon"]) / 2.0,
                    **price_stats,
                    **geometry_stats,
                    **visual_stats,
                },
                "geometry": geometry,
            }
        )
    return features


def build_html(
    analysis_id: str,
    features: list[dict],
    points: list[dict] | None = None,
    parcels: list[dict] | None = None,
    run_ids: list[str] | None = None,
    leaflet_css_href: str = "leaflet.css",
    leaflet_js_src: str = "leaflet.js",
    data_js_src: str = "analysis_data.js",
) -> str:
    points = points or []
    parcels = parcels or []
    run_ids = run_ids or [analysis_id]
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "__TITLE__": html.escape(f"Analysis map - {analysis_id}", quote=True),
        "__LEAFLET_CSS_HREF__": html.escape(leaflet_css_href, quote=True),
        "__LEAFLET_JS_SRC__": html.escape(leaflet_js_src, quote=True),
        "__DATA_JS_SRC__": html.escape(data_js_src, quote=True),
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template


def build_payload(
    analysis_id: str,
    features: list[dict],
    points: list[dict] | None = None,
    parcels: list[dict] | None = None,
    run_ids: list[str] | None = None,
) -> dict:
    points = points or []
    parcels = parcels or []
    run_ids = run_ids or [analysis_id]
    categories = sorted({feature["properties"]["category"] for feature in features} | {point["category"] for point in points})
    return {
        "analysis_id": analysis_id,
        "run_ids": run_ids,
        "data": {"type": "FeatureCollection", "features": features},
        "points": points,
        "parcels": {"type": "FeatureCollection", "features": parcels},
        "categories": categories,
        "counts": {
            "mesh_cells": len(features),
            "osm_points": len(points),
            "parcels": len(parcels),
            "runs": len(run_ids),
        },
    }


def write_summary_json(path: Path, analysis_id: str, run_ids: list[str], rows: list[sqlite3.Row], points: list[dict], parcels: list[dict]) -> None:
    top_candidates = []
    seen = set()
    for row in rows[:50]:
        bbox = (row["bbox_min_lat"], row["bbox_min_lon"], row["bbox_max_lat"], row["bbox_max_lon"])
        if bbox in seen:
            continue
        seen.add(bbox)
        top_candidates.append(
            {
                "cell_id": row["cell_id"],
                "run_id": row["run_id"],
                "category": row["category"],
                "score": row["score"],
                "point_count": row["point_count"],
                "bbox_order": "min_lat,min_lon,max_lat,max_lon",
                "bbox": list(bbox),
            }
        )
        if len(top_candidates) >= 10:
            break
    payload = {
        "analysis_id": analysis_id,
        "run_ids": run_ids,
        "mesh_cells_rendered": len(rows),
        "osm_points_rendered": len(points),
        "parcels_rendered": len(parcels),
        "top_candidates": top_candidates,
        "source_of_truth": "SQLite tables",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render completed analysis layers from SQLite to one HTML map.")
    parser.add_argument("--run-id", required=True, help="One run_id or comma-separated run_ids for one combined analysis map.")
    parser.add_argument("--analysis-id", help="Output analysis id. Defaults to the single run_id, or combined_<first_run_id> for multiple run_ids.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--limit", type=int, default=2500, help="Maximum mesh rows to render. Use 0 for all rows.")
    parser.add_argument("--selected-parcels-json", help="Optional ranked candidates JSON. When set, only parcel_ids from payload.top are rendered.")
    parser.add_argument("--output")
    parser.add_argument("--output-dir", help="Directory for analysis_map.html. Convenience alias for --output.")
    args = parser.parse_args()

    run_ids = parse_run_ids(args.run_id)
    analysis_id = args.analysis_id or (run_ids[0] if len(run_ids) == 1 else f"combined_{run_ids[0]}")
    if args.output and args.output_dir:
        raise SystemExit("Use either --output or --output-dir, not both.")
    output = Path(args.output) if args.output else Path(args.output_dir) / "analysis_map.html" if args.output_dir else Path("results") / f"analysis_{analysis_id}" / "analysis_map.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    leaflet_css_href, leaflet_js_src = ensure_leaflet_assets(output.parent)
    data_js_src = "analysis_data.js"

    connection = connect_workspace(args.db_path, args.canon_db_path)
    try:
        rows = candidate_rows(connection, run_ids, args.limit)
        points = point_rows(connection, rows)
        selected_parcel_ids = load_selected_parcel_ids(args.selected_parcels_json)
        parcels = parcel_features(connection, rows, selected_parcel_ids)
    finally:
        connection.close()

    if not rows:
        raise SystemExit(f"No Layer 1 hotspot rows found for run_id={args.run_id!r}.")

    features = [feature_from_row(row) for row in rows]
    payload = build_payload(analysis_id, features, points, parcels, run_ids)
    write_data_js(output.parent / data_js_src, payload)
    output.write_text(
        build_html(analysis_id, features, points, parcels, run_ids, leaflet_css_href, leaflet_js_src, data_js_src),
        encoding="utf-8",
    )
    write_summary_json(output.parent / "presentation_summary.json", analysis_id, run_ids, rows, points, parcels)
    print(output.resolve())
    print(json.dumps({"analysis_id": analysis_id, "run_ids": run_ids, "mesh_cells": len(features), "osm_points": len(points), "parcels": len(parcels)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
