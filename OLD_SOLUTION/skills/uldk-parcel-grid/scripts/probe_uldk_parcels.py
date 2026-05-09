#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TextIO

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, canon_foreign_key_clause, canon_table, connect_workspace

DEFAULT_DB_PATH = DEFAULT_ANALYSIS_DB_PATH
ULDK_ENDPOINT = "https://uldk.gugik.gov.pl/"


def ensure_layer2_tables(connection: sqlite3.Connection) -> None:
    canon_parcels = canon_table(connection, "canon_parcels")
    canon_points = canon_table(connection, "canon_parcel_polygon_points")
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {canon_parcels} (
            parcel_id TEXT PRIMARY KEY,
            parcel_number TEXT,
            voivodeship TEXT,
            county TEXT,
            commune TEXT,
            precinct TEXT,
            area_m2 REAL,
            centroid_lat REAL,
            centroid_lon REAL,
            bbox_min_lat REAL,
            bbox_min_lon REAL,
            bbox_max_lat REAL,
            bbox_max_lon REAL,
            geometry_hash TEXT
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {canon_points} (
            parcel_id TEXT NOT NULL,
            polygon_index INTEGER NOT NULL DEFAULT 0,
            ring_index INTEGER NOT NULL DEFAULT 0,
            point_index INTEGER NOT NULL,
            lon REAL NOT NULL,
            lat REAL NOT NULL,
            PRIMARY KEY (parcel_id, polygon_index, ring_index, point_index),
            FOREIGN KEY (parcel_id) REFERENCES canon_parcels(parcel_id) ON DELETE CASCADE
        )
        """
    )
    helper_fk = canon_foreign_key_clause(connection)
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS helper_layer2_run_parcels (
            run_id TEXT NOT NULL,
            parcel_id TEXT NOT NULL,
            candidate_index INTEGER NOT NULL,
            source_bbox TEXT NOT NULL,
            expected_commune TEXT,
            PRIMARY KEY (run_id, parcel_id){helper_fk}
        )
        """
    )
    connection.commit()


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [float(part.strip()) for part in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must have four comma-separated numbers")
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError("bbox order must be min_lon,min_lat,max_lon,max_lat")
    return min_lon, min_lat, max_lon, max_lat


def grid_points(bbox: tuple[float, float, float, float], grid_size_m: float) -> list[tuple[float, float]]:
    min_lon, min_lat, max_lon, max_lat = bbox
    lat_step = grid_size_m / 111_320.0
    mid_lat = (min_lat + max_lat) / 2.0
    lon_step = grid_size_m / max(1.0, 111_320.0 * math.cos(math.radians(mid_lat)))
    rows = max(1, math.ceil((max_lat - min_lat) / lat_step))
    cols = max(1, math.ceil((max_lon - min_lon) / lon_step))
    points = []
    for row in range(rows):
        lat = min_lat + (row + 0.5) * (max_lat - min_lat) / rows
        for col in range(cols):
            lon = min_lon + (col + 0.5) * (max_lon - min_lon) / cols
            points.append((lon, lat))
    return points


def _remove_srid(wkt: str) -> str:
    clean = wkt.strip()
    if clean.upper().startswith("SRID="):
        return clean.split(";", 1)[1].strip()
    return clean


def _strip_outer(text: str) -> str:
    clean = text.strip()
    if clean.startswith("(") and clean.endswith(")"):
        return clean[1:-1].strip()
    return clean


def _extract_groups(text: str) -> list[str]:
    groups: list[str] = []
    depth = 0
    start = None
    for index, char in enumerate(text):
        if char == "(":
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and start is not None:
                groups.append(text[start:index].strip())
                start = None
    return groups


def _parse_ring(text: str) -> list[list[float]]:
    coords = []
    for pair in text.split(","):
        x, y, *_ = [float(part) for part in pair.split()]
        coords.append([x, y])
    return coords


def wkt_to_geojson_geometry(wkt: str) -> dict:
    clean = _remove_srid(wkt)
    upper = clean.upper()
    if upper.startswith("POLYGON"):
        body = clean[len("POLYGON") :].strip()
        rings = [_parse_ring(group) for group in _extract_groups(_strip_outer(body))]
        return {"type": "Polygon", "coordinates": rings}
    if upper.startswith("MULTIPOLYGON"):
        body = clean[len("MULTIPOLYGON") :].strip()
        polygons = []
        for polygon_text in _extract_groups(_strip_outer(body)):
            rings = [_parse_ring(group) for group in _extract_groups(polygon_text)]
            polygons.append(rings)
        return {"type": "MultiPolygon", "coordinates": polygons}
    raise ValueError(f"Unsupported WKT geometry: {clean[:32]}")


def geometry_rings(geometry: dict) -> list[tuple[int, int, list[list[float]]]]:
    if geometry["type"] == "Polygon":
        return [(0, ring_index, ring) for ring_index, ring in enumerate(geometry["coordinates"])]
    if geometry["type"] == "MultiPolygon":
        result = []
        for polygon_index, polygon in enumerate(geometry["coordinates"]):
            for ring_index, ring in enumerate(polygon):
                result.append((polygon_index, ring_index, ring))
        return result
    raise ValueError(f"Unsupported geometry type: {geometry['type']}")


def polygon_area_m2(ring: list[list[float]]) -> float:
    if len(ring) < 3:
        return 0.0
    mean_lat = sum(lat for _, lat in ring) / len(ring)
    meters_per_lon = 111_320.0 * math.cos(math.radians(mean_lat))
    projected = [(lon * meters_per_lon, lat * 111_320.0) for lon, lat in ring]
    area = 0.0
    for (x1, y1), (x2, y2) in zip(projected, projected[1:] + projected[:1]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def geometry_stats(geometry: dict) -> dict:
    rings = geometry_rings(geometry)
    outer_rings = [ring for _, ring_index, ring in rings if ring_index == 0]
    all_points = [point for _, _, ring in rings for point in ring]
    lons = [point[0] for point in all_points]
    lats = [point[1] for point in all_points]
    area_m2 = sum(polygon_area_m2(ring) for ring in outer_rings)
    return {
        "area_m2": area_m2,
        "centroid_lon": sum(lons) / len(lons),
        "centroid_lat": sum(lats) / len(lats),
        "bbox_min_lon": min(lons),
        "bbox_min_lat": min(lats),
        "bbox_max_lon": max(lons),
        "bbox_max_lat": max(lats),
        "geometry_hash": hashlib.sha256(json.dumps(geometry, sort_keys=True).encode("utf-8")).hexdigest(),
    }


def bbox_dimensions_m(stats: dict) -> tuple[float, float]:
    mid_lat = (float(stats["bbox_min_lat"]) + float(stats["bbox_max_lat"])) / 2.0
    width_m = (float(stats["bbox_max_lon"]) - float(stats["bbox_min_lon"])) * 111_320.0 * math.cos(math.radians(mid_lat))
    height_m = (float(stats["bbox_max_lat"]) - float(stats["bbox_min_lat"])) * 111_320.0
    return abs(width_m), abs(height_m)


def bbox_shape_stats(stats: dict) -> dict:
    width_m, height_m = bbox_dimensions_m(stats)
    shortest = max(0.001, min(width_m, height_m))
    longest = max(width_m, height_m)
    return {
        "bbox_width_m": width_m,
        "bbox_height_m": height_m,
        "bbox_area_m2": width_m * height_m,
        "bbox_aspect_ratio": longest / shortest,
    }


def parcel_rejection_reason(
    parcel: dict,
    min_parcel_area_m2: float = 0.0,
    max_parcel_area_m2: float = 0.0,
    max_bbox_area_m2: float = 0.0,
    max_bbox_aspect_ratio: float = 0.0,
) -> str | None:
    area_m2 = float(parcel.get("area_m2") or 0.0)
    if min_parcel_area_m2 and area_m2 < min_parcel_area_m2:
        return "too_small"
    if max_parcel_area_m2 and area_m2 > max_parcel_area_m2:
        return "too_large"
    bbox = bbox_shape_stats(parcel)
    if max_bbox_area_m2 and bbox["bbox_area_m2"] > max_bbox_area_m2:
        return "bbox_too_large"
    if max_bbox_aspect_ratio and bbox["bbox_aspect_ratio"] > max_bbox_aspect_ratio:
        return "bbox_too_elongated"
    return None


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i, (xi, yi) in enumerate(ring):
        xj, yj = ring[j]
        intersects = (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        if intersects:
            inside = not inside
        j = i
    return inside


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    if geometry["type"] == "Polygon":
        return bool(geometry["coordinates"]) and point_in_ring(lon, lat, geometry["coordinates"][0])
    if geometry["type"] == "MultiPolygon":
        return any(point_in_ring(lon, lat, polygon[0]) for polygon in geometry["coordinates"] if polygon)
    return False


def load_existing_parcels(connection: sqlite3.Connection) -> list[dict]:
    parcel_rows = {
        row[0]: {
            "parcel_id": row[0],
            "parcel_number": row[1],
            "voivodeship": row[2],
            "county": row[3],
            "commune": row[4],
            "precinct": row[5],
            "area_m2": row[6],
            "centroid_lat": row[7],
            "centroid_lon": row[8],
            "bbox_min_lat": row[9],
            "bbox_min_lon": row[10],
            "bbox_max_lat": row[11],
            "bbox_max_lon": row[12],
            "geometry_hash": row[13],
        }
        for row in connection.execute(
            """
            SELECT parcel_id, parcel_number, voivodeship, county, commune, precinct,
                   area_m2, centroid_lat, centroid_lon,
                   bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon, geometry_hash
            FROM canon_parcels
            """
        ).fetchall()
    }
    if not parcel_rows:
        return []
    rows = connection.execute(
        """
        SELECT parcel_id, polygon_index, ring_index, point_index, lon, lat
        FROM canon_parcel_polygon_points
        ORDER BY parcel_id, polygon_index, ring_index, point_index
        """
    ).fetchall()
    grouped: dict[str, dict[int, dict[int, list[list[float]]]]] = {}
    for parcel_id, polygon_index, ring_index, _, lon, lat in rows:
        grouped.setdefault(parcel_id, {}).setdefault(polygon_index, {}).setdefault(ring_index, []).append([lon, lat])
    parcels = []
    for parcel_id, polygons in grouped.items():
        parcel = parcel_rows.get(parcel_id)
        if not parcel:
            continue
        if len(polygons) == 1:
            rings = [ring for _, ring in sorted(polygons[0].items())]
            geometry = {"type": "Polygon", "coordinates": rings}
        else:
            multi = []
            for _, rings_by_index in sorted(polygons.items()):
                multi.append([ring for _, ring in sorted(rings_by_index.items())])
            geometry = {"type": "MultiPolygon", "coordinates": multi}
        parcels.append({**parcel, "geometry": geometry})
    return parcels


def load_existing_geometries(connection: sqlite3.Connection) -> list[tuple[str, dict]]:
    return [(parcel["parcel_id"], parcel["geometry"]) for parcel in load_existing_parcels(connection)]


def parse_uldk_response(text: str) -> dict | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    if lines[0].startswith("-1") and any("brak wynik" in line.lower() for line in lines):
        return None
    if lines[0] != "0" or len(lines) < 2:
        raise ValueError(f"Unexpected ULDK response: {text[:120]}")
    fields = lines[1].split("|", 6)
    if len(fields) < 7:
        raise ValueError(f"Unexpected ULDK field count: {lines[1][:120]}")
    parcel_id, parcel_number, commune, county, voivodeship, _datasource, geom_wkt = fields[:7]
    geometry = wkt_to_geojson_geometry(geom_wkt)
    stats = geometry_stats(geometry)
    return {
        "parcel_id": parcel_id,
        "parcel_number": parcel_number,
        "voivodeship": voivodeship,
        "county": county,
        "commune": commune,
        "precinct": None,
        "geometry": geometry,
        **stats,
    }


def fetch_uldk_parcel(lon: float, lat: float, timeout: float = 20.0) -> dict | None:
    query = urllib.parse.urlencode(
        {
            "request": "GetParcelByXY",
            "xy": f"{lon},{lat},4326",
            "result": "id,parcel,commune,county,voivodeship,datasource,geom_wkt",
            "srid": "4326",
        }
    )
    with urllib.request.urlopen(f"{ULDK_ENDPOINT}?{query}", timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
    return parse_uldk_response(text)


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    asciiish = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(asciiish.casefold().split())


def commune_matches(actual: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    return normalize_name(actual) == normalize_name(expected)


def upsert_parcel(connection: sqlite3.Connection, parcel: dict) -> None:
    connection.execute(
        """
        INSERT INTO canon_parcels (
            parcel_id, parcel_number, voivodeship, county, commune, precinct,
            area_m2, centroid_lat, centroid_lon,
            bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon,
            geometry_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(parcel_id) DO UPDATE SET
            parcel_number = excluded.parcel_number,
            voivodeship = excluded.voivodeship,
            county = excluded.county,
            commune = excluded.commune,
            precinct = excluded.precinct,
            area_m2 = excluded.area_m2,
            centroid_lat = excluded.centroid_lat,
            centroid_lon = excluded.centroid_lon,
            bbox_min_lat = excluded.bbox_min_lat,
            bbox_min_lon = excluded.bbox_min_lon,
            bbox_max_lat = excluded.bbox_max_lat,
            bbox_max_lon = excluded.bbox_max_lon,
            geometry_hash = excluded.geometry_hash
        """,
        (
            parcel["parcel_id"],
            parcel.get("parcel_number"),
            parcel.get("voivodeship"),
            parcel.get("county"),
            parcel.get("commune"),
            parcel.get("precinct"),
            parcel["area_m2"],
            parcel["centroid_lat"],
            parcel["centroid_lon"],
            parcel["bbox_min_lat"],
            parcel["bbox_min_lon"],
            parcel["bbox_max_lat"],
            parcel["bbox_max_lon"],
            parcel["geometry_hash"],
        ),
    )
    connection.execute("DELETE FROM canon_parcel_polygon_points WHERE parcel_id = ?", (parcel["parcel_id"],))
    for polygon_index, ring_index, ring in geometry_rings(parcel["geometry"]):
        for point_index, (lon, lat) in enumerate(ring):
            connection.execute(
                """
                INSERT INTO canon_parcel_polygon_points (
                    parcel_id, polygon_index, ring_index, point_index, lon, lat
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(parcel_id, polygon_index, ring_index, point_index) DO UPDATE SET
                    lon = excluded.lon,
                    lat = excluded.lat
                """,
                (parcel["parcel_id"], polygon_index, ring_index, point_index, lon, lat),
            )
    connection.commit()


def run_probe(
    connection: sqlite3.Connection,
    bbox: tuple[float, float, float, float],
    grid_size_m: float,
    max_requests: int,
    refresh_existing: bool,
    expected_commune: str | None = None,
    max_error_examples: int = 3,
    progress_label: str | None = None,
    progress_every: int = 0,
    progress_stream: TextIO | None = None,
    min_parcel_area_m2: float = 0.0,
    max_parcel_area_m2: float = 0.0,
    max_bbox_area_m2: float = 0.0,
    max_bbox_aspect_ratio: float = 0.0,
    skip_rejected_polygons: bool = True,
) -> dict:
    ensure_layer2_tables(connection)
    existing = load_existing_parcels(connection)
    points = grid_points(bbox, grid_size_m)
    total_points = len(points)
    requests = inserted = skipped_existing = skipped_rejected_polygon = empty = errors = out_of_scope = rejected = 0
    error_examples: list[str] = []
    found_parcel_ids: list[str] = []
    seen_ids = {parcel["parcel_id"] for parcel in existing}
    rejected_geometries: list[tuple[str, dict, str]] = []
    rejected_reasons: dict[str, int] = {}

    def mark_rejected(parcel: dict, reason: str) -> None:
        nonlocal rejected
        rejected += 1
        rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
        if skip_rejected_polygons and parcel.get("geometry"):
            rejected_geometries.append((parcel["parcel_id"], parcel["geometry"], reason))

    def existing_at_point(lon: float, lat: float) -> dict | None:
        for parcel in existing:
            if point_in_geometry(lon, lat, parcel["geometry"]):
                return parcel
        return None

    def rejected_at_point(lon: float, lat: float) -> bool:
        return any(point_in_geometry(lon, lat, geometry) for _parcel_id, geometry, _reason in rejected_geometries)

    def emit_progress(event: str, processed_points: int) -> None:
        if not progress_stream or not progress_every:
            return
        print(
            "PROGRESS "
            + json.dumps(
                {
                    "stage": "layer2_probe",
                    "event": event,
                    "label": progress_label,
                    "processed_points": processed_points,
                    "grid_points": total_points,
                    "requests": requests,
                    "inserted": inserted,
                    "skipped_existing": skipped_existing,
                    "skipped_rejected_polygon": skipped_rejected_polygon,
                    "empty": empty,
                    "out_of_scope": out_of_scope,
                    "rejected": rejected,
                    "errors": errors,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=progress_stream,
            flush=True,
        )

    emit_progress("start", 0)
    processed_points = 0
    for processed_points, (lon, lat) in enumerate(points, start=1):
        if max_requests and requests >= max_requests:
            break
        if skip_rejected_polygons and rejected_at_point(lon, lat):
            skipped_rejected_polygon += 1
            if progress_every and processed_points % progress_every == 0:
                emit_progress("progress", processed_points)
            continue
        existing_parcel = None if refresh_existing else existing_at_point(lon, lat)
        if existing_parcel:
            skipped_existing += 1
            if not commune_matches(existing_parcel.get("commune"), expected_commune):
                out_of_scope += 1
            else:
                reason = parcel_rejection_reason(
                    existing_parcel,
                    min_parcel_area_m2,
                    max_parcel_area_m2,
                    max_bbox_area_m2,
                    max_bbox_aspect_ratio,
                )
                if reason:
                    mark_rejected(existing_parcel, reason)
                elif existing_parcel["parcel_id"] not in found_parcel_ids:
                    found_parcel_ids.append(existing_parcel["parcel_id"])
            if progress_every and processed_points % progress_every == 0:
                emit_progress("progress", processed_points)
            continue
        requests += 1
        try:
            parcel = fetch_uldk_parcel(lon, lat)
        except Exception as exc:
            errors += 1
            if len(error_examples) < max_error_examples:
                error_examples.append(f"{lon:.7f},{lat:.7f}: {exc}")
            continue
        if parcel is None:
            empty += 1
        elif not commune_matches(parcel.get("commune"), expected_commune):
            out_of_scope += 1
        else:
            upsert_parcel(connection, parcel)
            reason = parcel_rejection_reason(
                parcel,
                min_parcel_area_m2,
                max_parcel_area_m2,
                max_bbox_area_m2,
                max_bbox_aspect_ratio,
            )
            if reason:
                mark_rejected(parcel, reason)
            elif parcel["parcel_id"] not in found_parcel_ids:
                found_parcel_ids.append(parcel["parcel_id"])
            if parcel["parcel_id"] not in seen_ids:
                inserted += 1
                seen_ids.add(parcel["parcel_id"])
                existing.append(parcel)
        if progress_every and processed_points % progress_every == 0:
            emit_progress("progress", processed_points)
    emit_progress("done", processed_points)
    return {
        "grid_points": total_points,
        "requests": requests,
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "skipped_rejected_polygon": skipped_rejected_polygon,
        "empty": empty,
        "errors": errors,
        "out_of_scope": out_of_scope,
        "rejected": rejected,
        "rejected_reasons": rejected_reasons,
        "expected_commune": expected_commune,
        "error_examples": error_examples,
        "found_parcel_ids": found_parcel_ids,
    }
def link_layer2_run_parcel(
    connection: sqlite3.Connection,
    run_id: str,
    parcel_id: str,
    candidate_index: int,
    source_bbox: str,
    expected_commune: str | None = None,
) -> None:
    ensure_layer2_tables(connection)
    connection.execute(
        """
        INSERT OR IGNORE INTO helper_layer2_run_parcels (run_id, parcel_id, candidate_index, source_bbox, expected_commune)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, parcel_id, candidate_index, source_bbox, expected_commune),
    )
    connection.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe ULDK parcels for a selected bbox and persist them in SQLite.")
    parser.add_argument("--bbox", required=True, help="min_lon,min_lat,max_lon,max_lat")
    parser.add_argument("--run-id", required=True, help="Recorded in logs/output only; parcels remain canonical by parcel_id.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--grid-size-m", type=float, default=35.0)
    parser.add_argument("--max-requests", type=int, default=50)
    parser.add_argument("--refresh-existing", action="store_true")
    parser.add_argument("--expected-commune", help="Persist only parcels whose ULDK commune matches this value.")
    parser.add_argument("--progress-every", type=int, default=25, help="Emit compact progress every N grid points; 0 disables.")
    parser.add_argument("--min-parcel-area-m2", type=float, default=0.0)
    parser.add_argument("--max-parcel-area-m2", type=float, default=0.0)
    parser.add_argument("--max-bbox-area-m2", type=float, default=0.0)
    parser.add_argument("--max-bbox-aspect-ratio", type=float, default=0.0)
    parser.add_argument("--no-skip-rejected-polygons", action="store_true")
    args = parser.parse_args()

    bbox = parse_bbox(args.bbox)
    connection = connect_workspace(args.db_path, args.canon_db_path)
    try:
        summary = run_probe(
            connection,
            bbox,
            args.grid_size_m,
            args.max_requests,
            args.refresh_existing,
            args.expected_commune,
            progress_label=args.run_id,
            progress_every=args.progress_every,
            progress_stream=sys.stderr,
            min_parcel_area_m2=args.min_parcel_area_m2,
            max_parcel_area_m2=args.max_parcel_area_m2,
            max_bbox_area_m2=args.max_bbox_area_m2,
            max_bbox_aspect_ratio=args.max_bbox_aspect_ratio,
            skip_rejected_polygons=not args.no_skip_rejected_polygons,
        )
    finally:
        connection.close()

    print(f"run_id={args.run_id}")
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 1 if summary["errors"] and not (summary["inserted"] or summary["empty"] or summary["out_of_scope"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
