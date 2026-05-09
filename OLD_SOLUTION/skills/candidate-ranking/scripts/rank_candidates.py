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


def load_rows(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    connection.row_factory = sqlite3.Row
    has_geometry = table_exists(connection, "deriv_parcel_geometry_features")
    has_visual = table_exists(connection, "deriv_parcel_visual_features")
    geometry_cols = (
        "gf.area_m2 AS geometry_area_m2, gf.compactness, gf.elongation_ratio, "
        "gf.principal_moment_min_m4, gf.principal_moment_max_m4"
        if has_geometry
        else "NULL AS geometry_area_m2, NULL AS compactness, NULL AS elongation_ratio, "
        "NULL AS principal_moment_min_m4, NULL AS principal_moment_max_m4"
    )
    visual_cols = (
        "vf.green_pixel_ratio, vf.dark_pixel_ratio, vf.bright_pixel_ratio, vf.low_saturation_ratio, vf.brightness_mean"
        if has_visual
        else "NULL AS green_pixel_ratio, NULL AS dark_pixel_ratio, NULL AS bright_pixel_ratio, "
        "NULL AS low_saturation_ratio, NULL AS brightness_mean"
    )
    geometry_join = "LEFT JOIN deriv_parcel_geometry_features gf ON gf.parcel_id = p.parcel_id" if has_geometry else ""
    visual_join = "LEFT JOIN deriv_parcel_visual_features vf ON vf.parcel_id = p.parcel_id" if has_visual else ""
    rows = connection.execute(
        f"""
        SELECT p.parcel_id, p.parcel_number, p.commune, p.county, p.voivodeship,
               p.area_m2 AS parcel_area_m2, p.centroid_lat, p.centroid_lon,
               p.bbox_min_lat, p.bbox_min_lon, p.bbox_max_lat, p.bbox_max_lon,
               rp.candidate_index, rp.source_bbox,
               {geometry_cols},
               {visual_cols}
        FROM helper_layer2_run_parcels rp
        JOIN canon_parcels p ON p.parcel_id = rp.parcel_id
        {geometry_join}
        {visual_join}
        WHERE rp.run_id = ?
        ORDER BY rp.candidate_index, p.parcel_id
        """,
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def bell_score(value: float, target: float, tolerance: float) -> float:
    if tolerance <= 0:
        return 0.0
    return max(0.0, 1.0 - abs(value - target) / tolerance)


def score_row(row: dict, weights: dict) -> float:
    area = row["area_m2"]
    compactness = row.get("compactness") or 0.0
    elongation = row.get("elongation_ratio") or 0.0
    green = row.get("green_pixel_ratio") or 0.0
    dark = row.get("dark_pixel_ratio") or 0.0
    bright = row.get("bright_pixel_ratio") or 0.0
    score = 0.0
    score += bell_score(area, weights["target_area_m2"], weights["area_tolerance_m2"]) * weights["area_weight"]
    score += min(compactness / max(0.001, weights["target_compactness"]), 1.0) * weights["compactness_weight"]
    score += bell_score(elongation, weights["target_elongation"], weights["elongation_tolerance"]) * weights["elongation_weight"]
    score += green * weights["green_weight"]
    score += bell_score(dark, weights["target_dark_ratio"], weights["dark_tolerance"]) * weights["dark_weight"]
    score += bell_score(bright, weights["target_bright_ratio"], weights["bright_tolerance"]) * weights["bright_weight"]
    return score


def google_maps_link(row: dict) -> str:
    lat = row.get("centroid_lat") or ((row["bbox_min_lat"] + row["bbox_max_lat"]) / 2.0)
    lon = row.get("centroid_lon") or ((row["bbox_min_lon"] + row["bbox_max_lon"]) / 2.0)
    return f"https://www.google.com/maps/search/?api=1&query={lat:.7f},{lon:.7f}"


def rank_candidates(
    connection: sqlite3.Connection,
    run_id: str,
    filters: dict,
    weights: dict,
    limit: int,
) -> dict:
    rows = load_rows(connection, run_id)
    counts = [{"step": "layer2_parcels", "count": len(rows)}]

    deduped = list({row["parcel_id"]: row for row in rows}.values())
    counts.append({"step": "deduped_parcels", "count": len(deduped)})

    with_geometry = [row for row in deduped if row.get("geometry_area_m2") is not None]
    counts.append({"step": "with_geometry_features", "count": len(with_geometry)})

    area_rows = []
    for row in with_geometry:
        row["area_m2"] = row.get("geometry_area_m2") or row.get("parcel_area_m2") or 0.0
        if filters["min_area_m2"] <= row["area_m2"] <= filters["max_area_m2"]:
            area_rows.append(row)
    counts.append({"step": f"area_{filters['min_area_m2']:g}_{filters['max_area_m2']:g}", "count": len(area_rows)})

    geometry_rows = [
        row
        for row in area_rows
        if (not filters["min_compactness"] or (row.get("compactness") or 0.0) >= filters["min_compactness"])
        and (not filters["max_elongation"] or (row.get("elongation_ratio") or 0.0) <= filters["max_elongation"])
    ]
    counts.append({"step": "geometry_shape_filter", "count": len(geometry_rows)})

    visual_rows = [
        row
        for row in geometry_rows
        if not filters["require_visual"] or row.get("green_pixel_ratio") is not None
    ]
    counts.append({"step": "with_visual_features" if filters["require_visual"] else "visual_not_required", "count": len(visual_rows)})

    visual_land_rows = [
        row
        for row in visual_rows
        if (not filters["min_green_pixel_ratio"] or (row.get("green_pixel_ratio") or 0.0) >= filters["min_green_pixel_ratio"])
        and (
            not filters["max_bright_pixel_ratio"]
            or row.get("bright_pixel_ratio") is None
            or row.get("bright_pixel_ratio") <= filters["max_bright_pixel_ratio"]
        )
        and (
            not filters["max_low_saturation_ratio"]
            or row.get("low_saturation_ratio") is None
            or row.get("low_saturation_ratio") <= filters["max_low_saturation_ratio"]
        )
        and (
            not filters["max_brightness_mean"]
            or row.get("brightness_mean") is None
            or row.get("brightness_mean") <= filters["max_brightness_mean"]
        )
    ]
    counts.append({"step": "visual_land_filter", "count": len(visual_land_rows)})

    ranked = []
    for row in visual_land_rows:
        ranked.append(
            {
                "parcel_id": row["parcel_id"],
                "score": round(score_row(row, weights), 4),
                "area_m2": round(row["area_m2"], 1),
                "commune": row.get("commune"),
                "county": row.get("county"),
                "voivodeship": row.get("voivodeship"),
                "compactness": None if row.get("compactness") is None else round(row["compactness"], 4),
                "elongation_ratio": None if row.get("elongation_ratio") is None else round(row["elongation_ratio"], 4),
                "green_pixel_ratio": None if row.get("green_pixel_ratio") is None else round(row["green_pixel_ratio"], 4),
                "dark_pixel_ratio": None if row.get("dark_pixel_ratio") is None else round(row["dark_pixel_ratio"], 4),
                "bright_pixel_ratio": None if row.get("bright_pixel_ratio") is None else round(row["bright_pixel_ratio"], 4),
                "low_saturation_ratio": None if row.get("low_saturation_ratio") is None else round(row["low_saturation_ratio"], 4),
                "google_maps": google_maps_link(row),
            }
        )
    ranked.sort(key=lambda row: (-row["score"], row["parcel_id"]))
    counts.append({"step": f"top_{limit}", "count": min(limit, len(ranked))})
    return {
        "run_id": run_id,
        "filters": filters,
        "weights": weights,
        "funnel_counts": counts,
        "top_count": min(limit, len(ranked)),
        "top": ranked[:limit],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank parcel candidates from SQLite using runtime filters and weights.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--min-area-m2", type=float, default=0.0)
    parser.add_argument("--max-area-m2", type=float, default=1_000_000_000.0)
    parser.add_argument("--min-compactness", type=float, default=0.0)
    parser.add_argument("--max-elongation", type=float, default=0.0)
    parser.add_argument("--require-visual", action="store_true")
    parser.add_argument("--min-green-pixel-ratio", type=float, default=0.0)
    parser.add_argument("--max-bright-pixel-ratio", type=float, default=0.0)
    parser.add_argument("--max-low-saturation-ratio", type=float, default=0.0)
    parser.add_argument("--max-brightness-mean", type=float, default=0.0)
    parser.add_argument("--target-area-m2", type=float, default=5000.0)
    parser.add_argument("--area-tolerance-m2", type=float, default=5000.0)
    parser.add_argument("--target-compactness", type=float, default=0.35)
    parser.add_argument("--target-elongation", type=float, default=2.5)
    parser.add_argument("--elongation-tolerance", type=float, default=7.0)
    parser.add_argument("--target-dark-ratio", type=float, default=0.35)
    parser.add_argument("--dark-tolerance", type=float, default=0.35)
    parser.add_argument("--target-bright-ratio", type=float, default=0.10)
    parser.add_argument("--bright-tolerance", type=float, default=0.20)
    parser.add_argument("--area-weight", type=float, default=2.0)
    parser.add_argument("--compactness-weight", type=float, default=1.0)
    parser.add_argument("--elongation-weight", type=float, default=0.8)
    parser.add_argument("--green-weight", type=float, default=1.4)
    parser.add_argument("--dark-weight", type=float, default=0.6)
    parser.add_argument("--bright-weight", type=float, default=0.4)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    filters = {
        "min_area_m2": args.min_area_m2,
        "max_area_m2": args.max_area_m2,
        "min_compactness": args.min_compactness,
        "max_elongation": args.max_elongation,
        "require_visual": args.require_visual,
        "min_green_pixel_ratio": args.min_green_pixel_ratio,
        "max_bright_pixel_ratio": args.max_bright_pixel_ratio,
        "max_low_saturation_ratio": args.max_low_saturation_ratio,
        "max_brightness_mean": args.max_brightness_mean,
    }
    weights = {
        "target_area_m2": args.target_area_m2,
        "area_tolerance_m2": args.area_tolerance_m2,
        "target_compactness": args.target_compactness,
        "target_elongation": args.target_elongation,
        "elongation_tolerance": args.elongation_tolerance,
        "target_dark_ratio": args.target_dark_ratio,
        "dark_tolerance": args.dark_tolerance,
        "target_bright_ratio": args.target_bright_ratio,
        "bright_tolerance": args.bright_tolerance,
        "area_weight": args.area_weight,
        "compactness_weight": args.compactness_weight,
        "elongation_weight": args.elongation_weight,
        "green_weight": args.green_weight,
        "dark_weight": args.dark_weight,
        "bright_weight": args.bright_weight,
    }

    connection = connect_workspace(args.db_path, args.canon_db_path)
    try:
        payload = rank_candidates(connection, args.run_id, filters, weights, args.limit)
    finally:
        connection.close()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "run_id": args.run_id, "top_count": payload["top_count"], "funnel_counts": payload["funnel_counts"]}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
