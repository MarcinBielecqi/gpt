#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, canon_foreign_key_clause, connect_workspace

DEFAULT_DB_PATH = DEFAULT_ANALYSIS_DB_PATH


def ensure_geometry_feature_table(connection: sqlite3.Connection) -> None:
    helper_fk = canon_foreign_key_clause(connection)
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS deriv_parcel_geometry_features (
            parcel_id TEXT PRIMARY KEY,
            computed_at TEXT NOT NULL,
            source_geometry_hash TEXT,
            area_m2 REAL NOT NULL,
            centroid_lat REAL NOT NULL,
            centroid_lon REAL NOT NULL,
            perimeter_m REAL NOT NULL,
            bbox_min_lat REAL NOT NULL,
            bbox_min_lon REAL NOT NULL,
            bbox_max_lat REAL NOT NULL,
            bbox_max_lon REAL NOT NULL,
            moment_ixx_m4 REAL NOT NULL,
            moment_iyy_m4 REAL NOT NULL,
            moment_ixy_m4 REAL NOT NULL,
            centroidal_ixx_m4 REAL NOT NULL,
            centroidal_iyy_m4 REAL NOT NULL,
            centroidal_ixy_m4 REAL NOT NULL,
            polar_moment_m4 REAL NOT NULL,
            principal_moment_min_m4 REAL NOT NULL,
            principal_moment_max_m4 REAL NOT NULL,
            principal_axis_angle_deg REAL NOT NULL,
            radius_gyration_min_m REAL NOT NULL,
            radius_gyration_max_m REAL NOT NULL,
            elongation_ratio REAL NOT NULL,
            compactness REAL NOT NULL,
            raw_json TEXT NOT NULL{helper_fk}
        )
        """
    )
    connection.commit()


def parcel_ids(connection: sqlite3.Connection, run_id: str | None, all_parcels: bool, parcel_id: str | None) -> list[str]:
    if parcel_id:
        return [parcel_id]
    if run_id:
        rows = connection.execute(
            """
            SELECT DISTINCT parcel_id
            FROM helper_layer2_run_parcels
            WHERE run_id = ?
            ORDER BY parcel_id
            """,
            (run_id,),
        ).fetchall()
        return [row[0] for row in rows]
    if all_parcels:
        rows = connection.execute("SELECT parcel_id FROM canon_parcels ORDER BY parcel_id").fetchall()
        return [row[0] for row in rows]
    raise ValueError("Use --run-id, --parcel-id, or --all.")


def load_polygon_rings(connection: sqlite3.Connection, parcel_id: str) -> dict[int, dict[int, list[tuple[float, float]]]]:
    rows = connection.execute(
        """
        SELECT polygon_index, ring_index, point_index, lon, lat
        FROM canon_parcel_polygon_points
        WHERE parcel_id = ?
        ORDER BY polygon_index, ring_index, point_index
        """,
        (parcel_id,),
    ).fetchall()
    polygons: dict[int, dict[int, list[tuple[float, float]]]] = {}
    for polygon_index, ring_index, _point_index, lon, lat in rows:
        polygons.setdefault(polygon_index, {}).setdefault(ring_index, []).append((float(lon), float(lat)))
    return polygons


def geometry_hash(polygons: dict[int, dict[int, list[tuple[float, float]]]]) -> str:
    payload = {
        str(poly_index): {str(ring_index): ring for ring_index, ring in sorted(rings.items())}
        for poly_index, rings in sorted(polygons.items())
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def close_ring(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(ring) > 1 and ring[0] == ring[-1]:
        return ring[:-1]
    return ring


def reference_lon_lat(polygons: dict[int, dict[int, list[tuple[float, float]]]]) -> tuple[float, float]:
    points = [point for rings in polygons.values() for ring in rings.values() for point in ring]
    if not points:
        raise ValueError("parcel has no polygon points")
    return sum(lon for lon, _ in points) / len(points), sum(lat for _, lat in points) / len(points)


def project_ring(ring: list[tuple[float, float]], ref_lon: float, ref_lat: float) -> list[tuple[float, float, float, float]]:
    meters_per_lon = 111_320.0 * math.cos(math.radians(ref_lat))
    projected = []
    for lon, lat in close_ring(ring):
        x = (lon - ref_lon) * meters_per_lon
        y = (lat - ref_lat) * 111_320.0
        projected.append((x, y, lon, lat))
    return projected


def ring_integrals(points: list[tuple[float, float]], desired_sign: int) -> dict[str, float]:
    if len(points) < 3:
        return {"area": 0.0, "sx": 0.0, "sy": 0.0, "ixx": 0.0, "iyy": 0.0, "ixy": 0.0}
    cross_sum = 0.0
    sx = sy = ixx = iyy = ixy = 0.0
    pairs = zip(points, points[1:] + points[:1])
    for (x0, y0), (x1, y1) in pairs:
        cross = x0 * y1 - x1 * y0
        cross_sum += cross
        sx += (x0 + x1) * cross
        sy += (y0 + y1) * cross
        ixx += (y0 * y0 + y0 * y1 + y1 * y1) * cross
        iyy += (x0 * x0 + x0 * x1 + x1 * x1) * cross
        ixy += (2 * x0 * y0 + x0 * y1 + x1 * y0 + 2 * x1 * y1) * cross
    raw_area = cross_sum / 2.0
    if raw_area == 0:
        return {"area": 0.0, "sx": 0.0, "sy": 0.0, "ixx": 0.0, "iyy": 0.0, "ixy": 0.0}
    sign_factor = desired_sign if raw_area > 0 else -desired_sign
    return {
        "area": abs(raw_area) * desired_sign,
        "sx": sx / 6.0 * sign_factor,
        "sy": sy / 6.0 * sign_factor,
        "ixx": ixx / 12.0 * sign_factor,
        "iyy": iyy / 12.0 * sign_factor,
        "ixy": ixy / 24.0 * sign_factor,
    }


def ring_perimeter(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))


def compute_features(parcel_id: str, polygons: dict[int, dict[int, list[tuple[float, float]]]]) -> dict:
    ref_lon, ref_lat = reference_lon_lat(polygons)
    totals = {"area": 0.0, "sx": 0.0, "sy": 0.0, "ixx": 0.0, "iyy": 0.0, "ixy": 0.0}
    perimeter = 0.0
    lons: list[float] = []
    lats: list[float] = []
    for rings in polygons.values():
        for ring_index, ring in rings.items():
            projected = project_ring(ring, ref_lon, ref_lat)
            xy = [(x, y) for x, y, _lon, _lat in projected]
            desired_sign = 1 if ring_index == 0 else -1
            part = ring_integrals(xy, desired_sign)
            for key in totals:
                totals[key] += part[key]
            perimeter += ring_perimeter(xy)
            lons.extend(lon for _x, _y, lon, _lat in projected)
            lats.extend(lat for _x, _y, _lon, lat in projected)

    area = totals["area"]
    if area <= 0:
        raise ValueError(f"parcel {parcel_id} has non-positive polygon area")
    cx = totals["sx"] / area
    cy = totals["sy"] / area
    meters_per_lon = 111_320.0 * math.cos(math.radians(ref_lat))
    centroid_lon = ref_lon + cx / meters_per_lon
    centroid_lat = ref_lat + cy / 111_320.0

    ixx_c = totals["ixx"] - area * cy * cy
    iyy_c = totals["iyy"] - area * cx * cx
    ixy_c = totals["ixy"] - area * cx * cy
    trace = ixx_c + iyy_c
    diff = ixx_c - iyy_c
    root = math.sqrt(max(0.0, (diff / 2.0) ** 2 + ixy_c**2))
    principal_min = max(0.0, trace / 2.0 - root)
    principal_max = max(0.0, trace / 2.0 + root)
    angle = 0.5 * math.degrees(math.atan2(-2.0 * ixy_c, ixx_c - iyy_c)) if trace else 0.0
    rg_min = math.sqrt(principal_min / area) if area else 0.0
    rg_max = math.sqrt(principal_max / area) if area else 0.0
    elongation = rg_max / rg_min if rg_min else 0.0
    compactness = 4.0 * math.pi * area / (perimeter * perimeter) if perimeter else 0.0
    raw = {
        "reference_lon": ref_lon,
        "reference_lat": ref_lat,
        "area_m2": area,
        "centroid_local_m": [cx, cy],
        "ring_count": sum(len(rings) for rings in polygons.values()),
        "polygon_count": len(polygons),
    }
    return {
        "parcel_id": parcel_id,
        "source_geometry_hash": geometry_hash(polygons),
        "area_m2": area,
        "centroid_lat": centroid_lat,
        "centroid_lon": centroid_lon,
        "perimeter_m": perimeter,
        "bbox_min_lat": min(lats),
        "bbox_min_lon": min(lons),
        "bbox_max_lat": max(lats),
        "bbox_max_lon": max(lons),
        "moment_ixx_m4": totals["ixx"],
        "moment_iyy_m4": totals["iyy"],
        "moment_ixy_m4": totals["ixy"],
        "centroidal_ixx_m4": ixx_c,
        "centroidal_iyy_m4": iyy_c,
        "centroidal_ixy_m4": ixy_c,
        "polar_moment_m4": ixx_c + iyy_c,
        "principal_moment_min_m4": principal_min,
        "principal_moment_max_m4": principal_max,
        "principal_axis_angle_deg": angle,
        "radius_gyration_min_m": rg_min,
        "radius_gyration_max_m": rg_max,
        "elongation_ratio": elongation,
        "compactness": compactness,
        "raw_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }


def upsert_feature(connection: sqlite3.Connection, feature: dict, computed_at: str) -> None:
    ensure_geometry_feature_table(connection)
    connection.execute(
        """
        INSERT INTO deriv_parcel_geometry_features (
            parcel_id, computed_at, source_geometry_hash, area_m2, centroid_lat, centroid_lon,
            perimeter_m, bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon,
            moment_ixx_m4, moment_iyy_m4, moment_ixy_m4,
            centroidal_ixx_m4, centroidal_iyy_m4, centroidal_ixy_m4,
            polar_moment_m4, principal_moment_min_m4, principal_moment_max_m4,
            principal_axis_angle_deg, radius_gyration_min_m, radius_gyration_max_m,
            elongation_ratio, compactness, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(parcel_id) DO UPDATE SET
            computed_at = excluded.computed_at,
            source_geometry_hash = excluded.source_geometry_hash,
            area_m2 = excluded.area_m2,
            centroid_lat = excluded.centroid_lat,
            centroid_lon = excluded.centroid_lon,
            perimeter_m = excluded.perimeter_m,
            bbox_min_lat = excluded.bbox_min_lat,
            bbox_min_lon = excluded.bbox_min_lon,
            bbox_max_lat = excluded.bbox_max_lat,
            bbox_max_lon = excluded.bbox_max_lon,
            moment_ixx_m4 = excluded.moment_ixx_m4,
            moment_iyy_m4 = excluded.moment_iyy_m4,
            moment_ixy_m4 = excluded.moment_ixy_m4,
            centroidal_ixx_m4 = excluded.centroidal_ixx_m4,
            centroidal_iyy_m4 = excluded.centroidal_iyy_m4,
            centroidal_ixy_m4 = excluded.centroidal_ixy_m4,
            polar_moment_m4 = excluded.polar_moment_m4,
            principal_moment_min_m4 = excluded.principal_moment_min_m4,
            principal_moment_max_m4 = excluded.principal_moment_max_m4,
            principal_axis_angle_deg = excluded.principal_axis_angle_deg,
            radius_gyration_min_m = excluded.radius_gyration_min_m,
            radius_gyration_max_m = excluded.radius_gyration_max_m,
            elongation_ratio = excluded.elongation_ratio,
            compactness = excluded.compactness,
            raw_json = excluded.raw_json
        """,
        (
            feature["parcel_id"],
            computed_at,
            feature["source_geometry_hash"],
            feature["area_m2"],
            feature["centroid_lat"],
            feature["centroid_lon"],
            feature["perimeter_m"],
            feature["bbox_min_lat"],
            feature["bbox_min_lon"],
            feature["bbox_max_lat"],
            feature["bbox_max_lon"],
            feature["moment_ixx_m4"],
            feature["moment_iyy_m4"],
            feature["moment_ixy_m4"],
            feature["centroidal_ixx_m4"],
            feature["centroidal_iyy_m4"],
            feature["centroidal_ixy_m4"],
            feature["polar_moment_m4"],
            feature["principal_moment_min_m4"],
            feature["principal_moment_max_m4"],
            feature["principal_axis_angle_deg"],
            feature["radius_gyration_min_m"],
            feature["radius_gyration_max_m"],
            feature["elongation_ratio"],
            feature["compactness"],
            feature["raw_json"],
        ),
    )


def compute_to_db(connection: sqlite3.Connection, ids: list[str], progress_every: int = 100) -> dict:
    ensure_geometry_feature_table(connection)
    computed_at = datetime.now(timezone.utc).isoformat()
    computed = skipped = errors = 0
    examples: list[str] = []
    for index, parcel_id in enumerate(ids, start=1):
        try:
            polygons = load_polygon_rings(connection, parcel_id)
            if not polygons:
                skipped += 1
                continue
            feature = compute_features(parcel_id, polygons)
            upsert_feature(connection, feature, computed_at)
            computed += 1
        except Exception as exc:
            errors += 1
            if len(examples) < 3:
                examples.append(f"{parcel_id}: {exc}")
        if progress_every and index % progress_every == 0:
            print(
                "PROGRESS "
                + json.dumps(
                    {"stage": "deriv_parcel_geometry_features", "processed": index, "total": len(ids), "computed": computed, "errors": errors},
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
    connection.commit()
    return {"requested": len(ids), "computed": computed, "skipped": skipped, "errors": errors, "error_examples": examples}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute derived parcel geometry features from SQLite parcel polygons.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--run-id")
    parser.add_argument("--parcel-id")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--summary-output")
    args = parser.parse_args()

    connection = connect_workspace(args.db_path, args.canon_db_path)
    try:
        ids = parcel_ids(connection, args.run_id, args.all, args.parcel_id)
        summary = compute_to_db(connection, ids, args.progress_every)
    finally:
        connection.close()

    summary.update({"run_id": args.run_id, "parcel_id": args.parcel_id, "table": "deriv_parcel_geometry_features"})
    if args.summary_output:
        output = Path(args.summary_output)
    else:
        output = Path("results") / ("geometry_features_summary.json" if not args.run_id else f"analysis_{args.run_id}/geometry_features_summary.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=True, sort_keys=True))
    return 1 if summary["errors"] and not summary["computed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
