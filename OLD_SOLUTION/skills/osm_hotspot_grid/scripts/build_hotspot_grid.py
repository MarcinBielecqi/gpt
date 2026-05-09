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


OSM_FETCH_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "osm-overpass-fetch" / "scripts"
if str(OSM_FETCH_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(OSM_FETCH_SCRIPTS_DIR))
ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import overpass_core
from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, canon_index, canon_table, connect_workspace


DEFAULT_DB_PATH = DEFAULT_ANALYSIS_DB_PATH
HOTSPOT_METHOD = "fem_like_triangular_density_mesh"


def results_output_dir(path: str | Path) -> Path:
    output_dir = Path(path)
    if output_dir.parts and output_dir.parts[0].lower() == "outputs":
        return Path("results").joinpath(*output_dir.parts[1:])
    return output_dir


class Cell:
    def __init__(self, min_lat: float, min_lon: float, max_lat: float, max_lon: float, depth: int) -> None:
        self.min_lat = min_lat
        self.min_lon = min_lon
        self.max_lat = max_lat
        self.max_lon = max_lon
        self.depth = depth


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [float(p.strip()) for p in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must have 4 values: min_lat,min_lon,max_lat,max_lon")
    min_lat, min_lon, max_lat, max_lon = parts
    if min_lat >= max_lat or min_lon >= max_lon:
        raise ValueError("bbox bounds are invalid")
    return min_lat, min_lon, max_lat, max_lon


def parse_osm_types(raw: str) -> list[tuple[str, str, str]]:
    pairs = []
    for token in [x.strip() for x in raw.split(",") if x.strip()]:
        if "=" not in token:
            raise ValueError(f"invalid type token: {token}")
        key, value = token.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or not value:
            raise ValueError(f"invalid key=value token: {token}")
        pairs.append((key, value, f"{key}_{value}"))
    if not pairs:
        raise ValueError("at least one type is required")
    return pairs


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def geometry_hash(geometry_json: str | None) -> str | None:
    if not geometry_json:
        return None
    return hashlib.sha256(geometry_json.encode("utf-8")).hexdigest()


def ensure_layer1_tables(connection: sqlite3.Connection) -> None:
    osm_features = canon_table(connection, "canon_osm_features")
    osm_features_center_index = canon_index(connection, "idx_canon_osm_features_center")
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {osm_features} (
            osm_type TEXT NOT NULL,
            osm_id INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            source TEXT NOT NULL,
            bbox_query TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            geometry_json TEXT,
            center_lat REAL,
            center_lon REAL,
            bbox_min_lat REAL,
            bbox_min_lon REAL,
            bbox_max_lat REAL,
            bbox_max_lon REAL,
            geometry_hash TEXT,
            raw_json TEXT,
            UNIQUE(osm_type, osm_id)
        )
        """
    )
    connection.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {osm_features_center_index}
            ON canon_osm_features(center_lat, center_lon)
        """
    )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS helper_osm_hotspot_mesh_cells (
            cell_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            source TEXT NOT NULL,
            method TEXT NOT NULL,
            category TEXT NOT NULL,
            tag_key TEXT,
            tag_value TEXT,
            bbox_input TEXT NOT NULL,
            mesh_nx INTEGER,
            kernel_m REAL,
            min_norm REAL,
            score REAL NOT NULL,
            norm REAL,
            point_count INTEGER,
            area_m2 REAL,
            density_per_km2 REAL,
            depth INTEGER,
            center_lat REAL NOT NULL,
            center_lon REAL NOT NULL,
            bbox_min_lat REAL NOT NULL,
            bbox_min_lon REAL NOT NULL,
            bbox_max_lat REAL NOT NULL,
            bbox_max_lon REAL NOT NULL,
            polygon_json TEXT NOT NULL,
            points_json TEXT,
            params_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_helper_osm_hotspot_mesh_cells_run_category
            ON helper_osm_hotspot_mesh_cells(run_id, category);
        CREATE INDEX IF NOT EXISTS idx_helper_osm_hotspot_mesh_cells_run_score
            ON helper_osm_hotspot_mesh_cells(run_id, score DESC);
        """
    )


def element_center(element: dict) -> tuple[float | None, float | None]:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    bounds = element.get("bounds") or {}
    if all(k in bounds for k in ("minlat", "minlon", "maxlat", "maxlon")):
        return (float(bounds["minlat"]) + float(bounds["maxlat"])) / 2.0, (float(bounds["minlon"]) + float(bounds["maxlon"])) / 2.0
    geometry = element.get("geometry") or []
    if geometry:
        lats = [float(p["lat"]) for p in geometry if "lat" in p]
        lons = [float(p["lon"]) for p in geometry if "lon" in p]
        if lats and lons:
            return sum(lats) / len(lats), sum(lons) / len(lons)
    return None, None


def element_bbox(element: dict, center_lat: float | None, center_lon: float | None) -> tuple[float | None, float | None, float | None, float | None]:
    bounds = element.get("bounds") or {}
    if all(k in bounds for k in ("minlat", "minlon", "maxlat", "maxlon")):
        return float(bounds["minlat"]), float(bounds["minlon"]), float(bounds["maxlat"]), float(bounds["maxlon"])
    geometry = element.get("geometry") or []
    if geometry:
        lats = [float(p["lat"]) for p in geometry if "lat" in p]
        lons = [float(p["lon"]) for p in geometry if "lon" in p]
        if lats and lons:
            return min(lats), min(lons), max(lats), max(lons)
    if center_lat is not None and center_lon is not None:
        return center_lat, center_lon, center_lat, center_lon
    return None, None, None, None


def element_geometry_payload(element: dict) -> dict | None:
    payload = {}
    for key in ("geometry", "center", "bounds", "nodes", "members"):
        if key in element:
            payload[key] = element[key]
    return payload or None


def upsert_canon_osm_features(
    connection: sqlite3.Connection,
    elements: list[dict],
    fetched_at: str,
    source: str,
    bbox_query: str,
) -> int:
    ensure_layer1_tables(connection)
    rows = []
    for element in elements:
        osm_type = element.get("type")
        osm_id = element.get("id")
        if not osm_type or osm_id is None:
            continue
        repaired = dict(element)
        repaired["tags"] = overpass_core.repair_text_values(element.get("tags", {}))
        center_lat, center_lon = element_center(repaired)
        min_lat, min_lon, max_lat, max_lon = element_bbox(repaired, center_lat, center_lon)
        geometry_payload = element_geometry_payload(repaired)
        geometry_json = canonical_json(geometry_payload) if geometry_payload else None
        rows.append(
            (
                osm_type,
                int(osm_id),
                fetched_at,
                source,
                bbox_query,
                canonical_json(repaired.get("tags", {})),
                geometry_json,
                center_lat,
                center_lon,
                min_lat,
                min_lon,
                max_lat,
                max_lon,
                geometry_hash(geometry_json),
                canonical_json(repaired),
            )
        )
    connection.executemany(
        """
        INSERT INTO canon_osm_features(
            osm_type, osm_id, fetched_at, source, bbox_query, tags_json,
            geometry_json, center_lat, center_lon, bbox_min_lat, bbox_min_lon,
            bbox_max_lat, bbox_max_lon, geometry_hash, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(osm_type, osm_id) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            source = excluded.source,
            bbox_query = excluded.bbox_query,
            tags_json = excluded.tags_json,
            geometry_json = excluded.geometry_json,
            center_lat = excluded.center_lat,
            center_lon = excluded.center_lon,
            bbox_min_lat = excluded.bbox_min_lat,
            bbox_min_lon = excluded.bbox_min_lon,
            bbox_max_lat = excluded.bbox_max_lat,
            bbox_max_lon = excluded.bbox_max_lon,
            geometry_hash = excluded.geometry_hash,
            raw_json = excluded.raw_json
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def bbox_intersects(feature: dict, bbox: tuple[float, float, float, float]) -> bool:
    min_lat, min_lon, max_lat, max_lon = bbox
    fmin_lat = feature.get("bbox_min_lat")
    fmin_lon = feature.get("bbox_min_lon")
    fmax_lat = feature.get("bbox_max_lat")
    fmax_lon = feature.get("bbox_max_lon")
    if None not in (fmin_lat, fmin_lon, fmax_lat, fmax_lon):
        return not (fmax_lat < min_lat or fmin_lat > max_lat or fmax_lon < min_lon or fmin_lon > max_lon)
    lat = feature.get("center_lat")
    lon = feature.get("center_lon")
    return lat is not None and lon is not None and min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def load_points_from_canon_osm_features(
    connection: sqlite3.Connection,
    bbox: tuple[float, float, float, float],
    type_pairs: list[tuple[str, str, str]],
) -> list[dict]:
    ensure_layer1_tables(connection)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT osm_type, osm_id, tags_json, center_lat, center_lon,
               bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon
        FROM canon_osm_features
        WHERE center_lat IS NOT NULL AND center_lon IS NOT NULL
        """
    ).fetchall()
    points = []
    for row in rows:
        feature = dict(row)
        if not bbox_intersects(feature, bbox):
            continue
        tags = json.loads(row["tags_json"] or "{}")
        for key, value, category in type_pairs:
            if tags.get(key) != value:
                continue
            points.append(
                {
                    "source": "sqlite_canon_osm_features",
                    "osm_type": row["osm_type"],
                    "osm_id": row["osm_id"],
                    "category": category,
                    "tag_key": key,
                    "tag_value": value,
                    "name": tags.get("name", ""),
                    "lat": float(row["center_lat"]),
                    "lon": float(row["center_lon"]),
                    "tags": tags,
                }
            )
    return points


def cell_polygon_json(cell: dict) -> str:
    b = cell["bbox"]
    return canonical_json(
        [
            [b["min_lon"], b["min_lat"]],
            [b["max_lon"], b["min_lat"]],
            [b["max_lon"], b["max_lat"]],
            [b["min_lon"], b["max_lat"]],
            [b["min_lon"], b["min_lat"]],
        ]
    )


def write_hotspot_cells_sqlite(
    connection: sqlite3.Connection,
    run_id: str,
    generated_at: str,
    bbox_input: str,
    cells: list[dict],
    params: dict,
) -> int:
    ensure_layer1_tables(connection)
    connection.execute("DELETE FROM helper_osm_hotspot_mesh_cells WHERE run_id = ?", (run_id,))
    max_score = max((c["score"] for c in cells), default=0.0)
    rows = []
    for idx, cell in enumerate(cells, start=1):
        b = cell["bbox"]
        center_lat = (b["min_lat"] + b["max_lat"]) / 2.0
        center_lon = (b["min_lon"] + b["max_lon"]) / 2.0
        rows.append(
            (
                f"{run_id}_{idx:05d}",
                run_id,
                generated_at,
                "canon_osm_features",
                HOTSPOT_METHOD,
                cell["category"],
                cell.get("tag_key"),
                cell.get("tag_value"),
                bbox_input,
                cell["score"],
                cell["score"] / max_score if max_score else 0.0,
                cell["point_count"],
                cell["area_m2"],
                cell["density_per_km2"],
                cell["depth"],
                center_lat,
                center_lon,
                b["min_lat"],
                b["min_lon"],
                b["max_lat"],
                b["max_lon"],
                cell_polygon_json(cell),
                canonical_json(cell.get("points", [])),
                canonical_json(params),
            )
        )
    connection.executemany(
        """
        INSERT INTO helper_osm_hotspot_mesh_cells(
            cell_id, run_id, generated_at, source, method, category, tag_key,
            tag_value, bbox_input, score, norm, point_count, area_m2,
            density_per_km2, depth, center_lat, center_lon, bbox_min_lat,
            bbox_min_lon, bbox_max_lat, bbox_max_lon, polygon_json, points_json,
            params_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def build_triangular_mesh(
    bbox: tuple[float, float, float, float],
    points: list[dict],
    mesh_nx: int,
    kernel_m: float,
    min_norm: float,
    category: str,
    tag_key: str,
    tag_value: str,
) -> list[dict]:
    min_lat, min_lon, max_lat, max_lon = bbox
    lat_mid = (min_lat + max_lat) / 2.0
    _, meters_per_lon = local_meters(lat_mid)
    width_m = (max_lon - min_lon) * meters_per_lon
    height_m = (max_lat - min_lat) * 111_320.0
    mesh_ny = max(4, round(mesh_nx * height_m / max(width_m, 1.0)))

    def center_score(lat: float, lon: float) -> float:
        score = 0.0
        for point in points:
            distance = point_distance_m(lat, lon, point["lat"], point["lon"], lat_mid)
            score += math.exp(-(distance * distance) / (2.0 * kernel_m * kernel_m))
        return score

    triangles = []
    for y in range(mesh_ny):
        lat0 = min_lat + (max_lat - min_lat) * y / mesh_ny
        lat1 = min_lat + (max_lat - min_lat) * (y + 1) / mesh_ny
        for x in range(mesh_nx):
            lon0 = min_lon + (max_lon - min_lon) * x / mesh_nx
            lon1 = min_lon + (max_lon - min_lon) * (x + 1) / mesh_nx
            for coords in (
                [(lat0, lon0), (lat1, lon0), (lat1, lon1)],
                [(lat0, lon0), (lat1, lon1), (lat0, lon1)],
            ):
                center_lat = sum(lat for lat, _ in coords) / 3.0
                center_lon = sum(lon for _, lon in coords) / 3.0
                triangles.append(
                    {
                        "category": category,
                        "tag_key": tag_key,
                        "tag_value": tag_value,
                        "coords": coords,
                        "center": {"lat": center_lat, "lon": center_lon},
                        "score": center_score(center_lat, center_lon),
                        "source_point_count": len(points),
                    }
                )

    max_score = max((triangle["score"] for triangle in triangles), default=0.0)
    for triangle in triangles:
        triangle["norm"] = triangle["score"] / max_score if max_score else 0.0
    return [triangle for triangle in triangles if triangle["norm"] >= min_norm]


def triangle_bbox(triangle: dict) -> dict:
    lats = [lat for lat, _ in triangle["coords"]]
    lons = [lon for _, lon in triangle["coords"]]
    return {"min_lat": min(lats), "min_lon": min(lons), "max_lat": max(lats), "max_lon": max(lons)}


def triangle_polygon_json(triangle: dict) -> str:
    coords = [[lon, lat] for lat, lon in triangle["coords"]]
    coords.append(coords[0])
    return canonical_json(coords)


def write_mesh_cells_sqlite(
    connection: sqlite3.Connection,
    run_id: str,
    generated_at: str,
    bbox_input: str,
    mesh_nx: int,
    kernel_m: float,
    min_norm: float,
    triangles: list[dict],
) -> int:
    ensure_layer1_tables(connection)
    connection.execute("DELETE FROM helper_osm_hotspot_mesh_cells WHERE run_id = ?", (run_id,))
    rows = []
    for idx, triangle in enumerate(triangles, start=1):
        box = triangle_bbox(triangle)
        rows.append(
            (
                f"{run_id}_{idx:05d}",
                run_id,
                generated_at,
                "canon_osm_features",
                HOTSPOT_METHOD,
                triangle["category"],
                triangle.get("tag_key"),
                triangle.get("tag_value"),
                bbox_input,
                mesh_nx,
                kernel_m,
                min_norm,
                triangle["score"],
                triangle["norm"],
                triangle.get("source_point_count", 0),
                None,
                None,
                None,
                triangle["center"]["lat"],
                triangle["center"]["lon"],
                box["min_lat"],
                box["min_lon"],
                box["max_lat"],
                box["max_lon"],
                triangle_polygon_json(triangle),
                None,
                None,
            )
        )
    connection.executemany(
        """
        INSERT INTO helper_osm_hotspot_mesh_cells(
            cell_id, run_id, generated_at, source, method, category, tag_key,
            tag_value, bbox_input, mesh_nx, kernel_m, min_norm, score, norm,
            point_count, area_m2, density_per_km2, depth, center_lat, center_lon,
            bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon, polygon_json,
            points_json, params_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def load_config(path: str | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def local_meters(lat_mid: float) -> tuple[float, float]:
    mpl = 111_320.0
    mpo = 111_320.0 * math.cos(math.radians(lat_mid))
    return mpl, max(mpo, 1e-6)


def bbox_area_m2(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> float:
    lat_mid = (min_lat + max_lat) / 2.0
    mpl, mpo = local_meters(lat_mid)
    width = (max_lon - min_lon) * mpo
    height = (max_lat - min_lat) * mpl
    return max(width * height, 0.0)


def point_distance_m(lat1: float, lon1: float, lat2: float, lon2: float, lat_mid: float) -> float:
    mpl, mpo = local_meters(lat_mid)
    dy = (lat2 - lat1) * mpl
    dx = (lon2 - lon1) * mpo
    return math.sqrt(dx * dx + dy * dy)


def split_cell(cell: Cell) -> list[Cell]:
    mid_lat = (cell.min_lat + cell.max_lat) / 2.0
    mid_lon = (cell.min_lon + cell.max_lon) / 2.0
    d = cell.depth + 1
    return [
        Cell(mid_lat, cell.min_lon, cell.max_lat, mid_lon, d),
        Cell(mid_lat, mid_lon, cell.max_lat, cell.max_lon, d),
        Cell(cell.min_lat, cell.min_lon, mid_lat, mid_lon, d),
        Cell(cell.min_lat, mid_lon, mid_lat, cell.max_lon, d),
    ]


def assign_points_to_cell(points: list[dict], cell: Cell) -> list[dict]:
    out = []
    for p in points:
        if cell.min_lat <= p["lat"] <= cell.max_lat and cell.min_lon <= p["lon"] <= cell.max_lon:
            out.append(p)
    return out


def score_cell(point_count: int, density_per_km2: float) -> float:
    return density_per_km2 * math.log1p(point_count)


def dedupe_points(points: list[dict], radius_m: float, lat_mid: float) -> list[dict]:
    if radius_m <= 0:
        return points
    kept = []
    for p in points:
        duplicate = False
        for k in kept:
            if point_distance_m(p["lat"], p["lon"], k["lat"], k["lon"], lat_mid) <= radius_m:
                duplicate = True
                break
        if not duplicate:
            kept.append(p)
    return kept


def remove_overlapping_hotspots(cells: list[dict], max_overlap_ratio: float) -> list[dict]:
    def inter_area(a: dict, b: dict) -> float:
        min_lat = max(a["bbox"]["min_lat"], b["bbox"]["min_lat"])
        min_lon = max(a["bbox"]["min_lon"], b["bbox"]["min_lon"])
        max_lat = min(a["bbox"]["max_lat"], b["bbox"]["max_lat"])
        max_lon = min(a["bbox"]["max_lon"], b["bbox"]["max_lon"])
        if min_lat >= max_lat or min_lon >= max_lon:
            return 0.0
        return bbox_area_m2(min_lat, min_lon, max_lat, max_lon)

    selected = []
    for c in sorted(cells, key=lambda x: x["score"], reverse=True):
        keep = True
        for s in selected:
            if c["category"] != s["category"]:
                continue
            overlap = inter_area(c, s)
            if overlap <= 0:
                continue
            smaller = min(c["area_m2"], s["area_m2"])
            ratio = overlap / smaller if smaller > 0 else 0.0
            if ratio > max_overlap_ratio:
                keep = False
                break
        if keep:
            selected.append(c)
    return selected


def filter_points_to_bbox(points: list[dict], bbox: tuple[float, float, float, float]) -> tuple[list[dict], int]:
    min_lat, min_lon, max_lat, max_lon = bbox
    kept = []
    out_count = 0
    for p in points:
        if min_lat <= p["lat"] <= max_lat and min_lon <= p["lon"] <= max_lon:
            kept.append(p)
        else:
            out_count += 1
    return kept, out_count


def build_adaptive_grid(points: list[dict], category: str, key: str, value: str, bbox: tuple[float, float, float, float], cfg: dict) -> list[dict]:
    min_lat, min_lon, max_lat, max_lon = bbox
    root = Cell(min_lat, min_lon, max_lat, max_lon, 0)
    min_points = int(cfg["min_points"])
    max_depth = int(cfg["max_depth"])
    min_cell_m = float(cfg["min_cell_m"])
    min_area_m2 = float(cfg["min_area_m2"])
    max_area_m2 = float(cfg["max_area_m2"])
    hotspot_percentile = float(cfg["hotspot_percentile"])

    leaves: list[tuple[Cell, list[dict], float, float, float]] = []

    queue = [(root, points)]
    while queue:
        cell, cell_points = queue.pop()
        area = bbox_area_m2(cell.min_lat, cell.min_lon, cell.max_lat, cell.max_lon)
        density = (len(cell_points) / (area / 1_000_000.0)) if area > 0 else 0.0
        score = score_cell(len(cell_points), density)
        leaves.append((cell, cell_points, area, density, score))

        lat_mid = (cell.min_lat + cell.max_lat) / 2.0
        mpl, mpo = local_meters(lat_mid)
        cell_h = (cell.max_lat - cell.min_lat) * mpl
        cell_w = (cell.max_lon - cell.min_lon) * mpo
        cell_size = min(cell_h, cell_w)
        if cell.depth >= max_depth or cell_size < min_cell_m or len(cell_points) < min_points:
            continue
        for child in split_cell(cell):
            child_points = assign_points_to_cell(cell_points, child)
            if child_points:
                queue.append((child, child_points))

    densities = sorted([x[3] for x in leaves if x[1]])
    idx = max(0, min(len(densities) - 1, int((len(densities) - 1) * hotspot_percentile))) if densities else 0
    threshold = densities[idx] if densities else 0.0

    out = []
    seq = 1
    for cell, cell_points, area, density, score in sorted(leaves, key=lambda x: x[4], reverse=True):
        if len(cell_points) < min_points:
            continue
        if area < min_area_m2 or area > max_area_m2:
            continue
        if density < threshold:
            continue
        out.append(
            {
                "id": f"{category}_{seq:04d}",
                "category": category,
                "tag_key": key,
                "tag_value": value,
                "bbox": {
                    "min_lat": cell.min_lat,
                    "min_lon": cell.min_lon,
                    "max_lat": cell.max_lat,
                    "max_lon": cell.max_lon,
                },
                "point_count": len(cell_points),
                "area_m2": area,
                "density_per_km2": density,
                "score": score,
                "depth": cell.depth,
                "points": [
                    {
                        "osm_type": p["osm_type"],
                        "osm_id": p["osm_id"],
                        "name": p["name"],
                        "lat": p["lat"],
                        "lon": p["lon"],
                    }
                    for p in cell_points
                ],
            }
        )
        seq += 1
    return out


def write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_geojson(path: Path, cells: list[dict]) -> None:
    feats = []
    for c in cells:
        b = c["bbox"]
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    "id": c["id"],
                    "category": c["category"],
                    "tag_key": c["tag_key"],
                    "tag_value": c["tag_value"],
                    "point_count": c["point_count"],
                    "area_m2": c["area_m2"],
                    "density_per_km2": c["density_per_km2"],
                    "score": c["score"],
                    "depth": c["depth"],
                    "point_names": [p.get("name", "") for p in c["points"]],
                    "osm_ids": [p.get("osm_id", "") for p in c["points"]],
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [b["min_lon"], b["min_lat"]],
                        [b["max_lon"], b["min_lat"]],
                        [b["max_lon"], b["max_lat"]],
                        [b["min_lon"], b["max_lat"]],
                        [b["min_lon"], b["min_lat"]],
                    ]],
                },
            }
        )
    write_json(path, {"type": "FeatureCollection", "features": feats})


def write_mesh_geojson(path: Path, triangles: list[dict]) -> None:
    features = []
    for idx, triangle in enumerate(triangles, start=1):
        coords = [[lon, lat] for lat, lon in triangle["coords"]]
        coords.append(coords[0])
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": f"mesh_{idx:05d}",
                    "category": triangle["category"],
                    "tag_key": triangle.get("tag_key"),
                    "tag_value": triangle.get("tag_value"),
                    "score": triangle["score"],
                    "norm": triangle["norm"],
                    "center_lat": triangle["center"]["lat"],
                    "center_lon": triangle["center"]["lon"],
                    "source_point_count": triangle.get("source_point_count", 0),
                },
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            }
        )
    write_json(path, {"type": "FeatureCollection", "features": features})


def write_mesh_demo_map(path: Path, bbox: tuple[float, float, float, float], points: list[dict], triangles: list[dict], kernel_m: float) -> None:
    min_lat, min_lon, max_lat, max_lon = bbox
    point_payload = [
        {"lat": point["lat"], "lon": point["lon"], "name": point.get("name", ""), "category": point.get("category", ""), "id": point.get("osm_id", "")}
        for point in points
    ]
    html = f"""<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>Layer 1 OSM FEM Mesh</title>
  <link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'>
  <style>
    html,body,#map{{height:100%;margin:0}}
    body{{font-family:Arial,sans-serif}}
    .panel{{position:absolute;z-index:1000;top:12px;right:12px;background:#fff;border:1px solid #bbb;padding:10px;font:13px Arial;max-width:280px}}
  </style>
</head>
<body>
<div id='map'></div>
<div class='panel'><b>Layer 1 OSM FEM Mesh</b><br>points: {len(points)}<br>triangles: {len(triangles)}<br>kernel: {int(kernel_m)} m</div>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
<script>
const bbox = {{minLat:{min_lat}, minLon:{min_lon}, maxLat:{max_lat}, maxLon:{max_lon}}};
const points = {json.dumps(point_payload, ensure_ascii=False)};
const triangles = {json.dumps(triangles, ensure_ascii=False)};
const categories = [...new Set(triangles.map(t => t.category || 'all'))].sort();
const palette = ['#dc2626','#2563eb','#16a34a','#9333ea','#ea580c','#0f766e','#be123c'];
function esc(v) {{ return String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
function color(norm, category) {{
  const idx = Math.max(0, categories.indexOf(category));
  if (Number(norm || 0) > 0.72) return '#ef4444';
  return palette[idx % palette.length];
}}
function opacity(norm) {{ return Math.max(0.04, Math.min(0.58, Number(norm || 0) * 0.62)); }}
const map = L.map('map');
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom:19, attribution:'OSM'}}).addTo(map);
const fg = L.featureGroup().addTo(map);
const overlays = {{}};
categories.forEach(category => overlays[category] = L.layerGroup().addTo(map));
for (const t of triangles) {{
  const category = t.category || 'all';
  const poly = L.polygon(t.coords.map(([lat, lon]) => [lat, lon]), {{
    color: '#334155',
    weight: 0.45,
    opacity: 0.22,
    fillColor: color(t.norm, category),
    fillOpacity: opacity(t.norm)
  }});
  poly.bindPopup(`${{esc(category)}}<br>score: ${{Number(t.score || 0).toFixed(3)}}<br>norm: ${{Number(t.norm || 0).toFixed(3)}}`);
  poly.addTo(overlays[category]);
  poly.addTo(fg);
}}
for (const point of points) {{
  const marker = L.circleMarker([point.lat, point.lon], {{radius:3,color:'#111827',weight:1,fillOpacity:0.7}});
  marker.bindPopup(`<b>${{esc(point.name || '(no name)')}}</b><br>${{esc(point.category)}}<br>id: ${{esc(point.id)}}`);
  marker.addTo(fg);
}}
L.control.layers(null, overlays, {{collapsed:false}}).addTo(map);
if (fg.getLayers().length) map.fitBounds(fg.getBounds(), {{padding:[20,20]}});
else map.fitBounds([[bbox.minLat,bbox.minLon],[bbox.maxLat,bbox.maxLon]]);
</script>
</body>
</html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def write_summary(path: Path, summary: dict) -> None:
    write_json(path, summary)


def write_demo_map(path: Path, bbox: tuple[float, float, float, float], points: list[dict], cells: list[dict]) -> None:
    p = [
        {"lat": x["lat"], "lon": x["lon"], "name": x.get("name", ""), "category": x.get("category", ""), "id": x.get("osm_id", "")}
        for x in points
    ]
    geo = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": c["id"], "category": c["category"], "score": c["score"], "point_count": c["point_count"]},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [c["bbox"]["min_lon"], c["bbox"]["min_lat"]],
                        [c["bbox"]["max_lon"], c["bbox"]["min_lat"]],
                        [c["bbox"]["max_lon"], c["bbox"]["max_lat"]],
                        [c["bbox"]["min_lon"], c["bbox"]["max_lat"]],
                        [c["bbox"]["min_lon"], c["bbox"]["min_lat"]],
                    ]],
                },
            }
            for c in cells
        ],
    }
    min_lat, min_lon, max_lat, max_lon = bbox
    html = f"""<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>Hotspot Grid Demo</title>
  <link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'>
  <style>
    html,body,#map{{height:100%;margin:0}}
    body{{font-family:Arial,sans-serif}}
    .panel{{position:absolute;z-index:1000;top:12px;right:12px;background:#fff;border:1px solid #bbb;padding:10px;font:13px Arial}}
    .fallback{{position:absolute;inset:0;background:#eef3ef}}
    .fallback svg{{width:100%;height:100%;display:block}}
    .fallback .label{{font:12px Arial;fill:#243}}
  </style>
</head>
<body>
<div id='map'></div>
<div class='panel'><b>Hotspot Grid Demo</b><br>points: {len(p)}<br>cells: {len(cells)}</div>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
<script>
const bbox = {{minLat:{min_lat}, minLon:{min_lon}, maxLat:{max_lat}, maxLon:{max_lon}}};
const pts = {json.dumps(p, ensure_ascii=False)};
const cells = {json.dumps(geo, ensure_ascii=False)};
function esc(v) {{ return String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
function project(lat, lon, w, h) {{
  const pad = 34;
  const x = pad + ((lon - bbox.minLon) / (bbox.maxLon - bbox.minLon)) * (w - pad * 2);
  const y = h - pad - ((lat - bbox.minLat) / (bbox.maxLat - bbox.minLat)) * (h - pad * 2);
  return [x, y];
}}
function renderFallback() {{
  const mapEl = document.getElementById('map');
  const w = Math.max(window.innerWidth, 320);
  const h = Math.max(window.innerHeight, 320);
  let svg = `<div class="fallback"><svg viewBox="0 0 ${{w}} ${{h}}" role="img">`;
  svg += `<rect x="0" y="0" width="${{w}}" height="${{h}}" fill="#edf3ee"/>`;
  svg += `<g stroke="#c8d5cb" stroke-width="1">`;
  for (let i = 1; i < 8; i++) {{
    const x = i * w / 8; const y = i * h / 8;
    svg += `<line x1="${{x}}" y1="0" x2="${{x}}" y2="${{h}}"/><line x1="0" y1="${{y}}" x2="${{w}}" y2="${{y}}"/>`;
  }}
  svg += `</g>`;
  for (const f of cells.features || []) {{
    const ring = f.geometry.coordinates[0];
    const ptsAttr = ring.map(([lon, lat]) => project(lat, lon, w, h).join(',')).join(' ');
    svg += `<polygon points="${{ptsAttr}}" fill="#0f766e22" stroke="#0f766e" stroke-width="2"/>`;
  }}
  for (const p of pts) {{
    const [x, y] = project(p.lat, p.lon, w, h);
    svg += `<circle cx="${{x}}" cy="${{y}}" r="4" fill="#c02626" opacity="0.7"><title>${{esc(p.name || '(no name)')}} | ${{esc(p.category)}} | ${{esc(p.id)}}</title></circle>`;
  }}
  svg += `<text x="18" y="26" class="label">Fallback view: OSM points and hotspot cells, bbox ${{bbox.minLat}},${{bbox.minLon}},${{bbox.maxLat}},${{bbox.maxLon}}</text>`;
  svg += `</svg></div>`;
  mapEl.innerHTML = svg;
}}
function renderLeaflet() {{
  const map = L.map('map');
  L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom:19, attribution:'OSM'}}).addTo(map);
  const fg = L.featureGroup().addTo(map);
  for (const x of pts) {{
    const m = L.circleMarker([x.lat,x.lon],{{radius:4,color:'#c22',weight:1,fillOpacity:.5}});
    const nm = x.name || '(no name)';
    m.bindPopup(`<b>${{esc(nm)}}</b><br>${{esc(x.category)}}<br>id: ${{esc(x.id)}}`);
    m.addTo(fg);
  }}
  L.geoJSON(cells,{{
    style:()=>({{color:'#155',weight:2,fillOpacity:0.08}}),
    onEachFeature:(f,l)=>l.bindPopup(`<b>${{esc(f.properties.id)}}</b><br>${{esc(f.properties.category)}}<br>score: ${{Number(f.properties.score).toFixed(2)}}<br>points: ${{f.properties.point_count}}`)
  }}).addTo(fg);
  if(fg.getLayers().length) map.fitBounds(fg.getBounds(),{{padding:[20,20]}});
  else map.fitBounds([[bbox.minLat,bbox.minLon],[bbox.maxLat,bbox.maxLon]]);
}}
if (window.L) renderLeaflet(); else renderFallback();
</script>
</body>
</html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def category_config(global_cfg: dict, categories_cfg: dict, category: str) -> dict:
    cfg = dict(global_cfg)
    cfg.update(categories_cfg.get(category, {}))
    return cfg


def fetch_osm_elements(
    bbox: str,
    type_pairs: list[tuple[str, str, str]],
    endpoint: str,
    timeout: int,
    limit: int,
) -> tuple[list[dict], list[str]]:
    elements = []
    warnings = []
    for key, value, category in type_pairs:
        query = overpass_core.build_query(bbox, key, value, ["node", "way", "relation"], "center", limit)
        try:
            payload = overpass_core.post_overpass(endpoint, query, timeout)
            elements.extend(payload.get("elements", []))
        except Exception as exc:
            warnings.append(f"{category}: fetch failed: {exc}")
    return elements, warnings


def build_hotspot_cells_from_db(
    connection: sqlite3.Connection,
    bbox: tuple[float, float, float, float],
    bbox_input: str,
    type_pairs: list[tuple[str, str, str]],
    global_cfg: dict,
    category_cfgs: dict,
) -> tuple[list[dict], list[dict], dict, list[str]]:
    all_points = []
    warnings = []
    category_stats = {}
    lat_mid = (bbox[0] + bbox[2]) / 2.0
    stored_points = load_points_from_canon_osm_features(connection, bbox, type_pairs)

    for key, value, category in type_pairs:
        raw_points = [p for p in stored_points if p["category"] == category]
        in_bbox, out_count = filter_points_to_bbox(raw_points, bbox)
        cfg = category_config(global_cfg, category_cfgs, category)
        deduped = dedupe_points(in_bbox, float(cfg.get("dedupe_radius_m", global_cfg["dedupe_radius_m"])), lat_mid)
        all_points.extend(deduped)
        category_stats[category] = {
            "stored_matching_points": len(raw_points),
            "in_bbox_points": len(in_bbox),
            "deduped_points": len(deduped),
            "outside_bbox_points": out_count,
        }
        if not deduped:
            warnings.append(f"{category}: no points in canon_osm_features after bbox/tag filtering")

    cells = []
    for key, value, category in type_pairs:
        pts = [p for p in all_points if p["category"] == category]
        cfg = category_config(global_cfg, category_cfgs, category)
        category_cells = build_adaptive_grid(pts, category, key, value, bbox, cfg)
        category_cells = remove_overlapping_hotspots(category_cells, float(cfg.get("max_overlap_ratio", global_cfg["max_overlap_ratio"])))
        cells.extend(category_cells)

    cells = sorted(cells, key=lambda x: x["score"], reverse=True)
    return all_points, cells, category_stats, warnings


def build_mesh_cells_from_db(
    connection: sqlite3.Connection,
    bbox: tuple[float, float, float, float],
    type_pairs: list[tuple[str, str, str]],
    mesh_nx: int,
    kernel_m: float,
    min_norm: float,
) -> tuple[list[dict], list[dict], dict, list[str]]:
    warnings = []
    points = load_points_from_canon_osm_features(connection, bbox, type_pairs)
    category_stats = {}
    triangles = []
    for key, value, category in type_pairs:
        category_points = [point for point in points if point["category"] == category]
        category_stats[category] = {
            "stored_matching_points": len(category_points),
            "mesh_triangles": 0,
        }
        if not category_points:
            warnings.append(f"{category}: no points in canon_osm_features after bbox/tag filtering")
            continue
        category_triangles = build_triangular_mesh(bbox, category_points, mesh_nx, kernel_m, min_norm, category, key, value)
        category_stats[category]["mesh_triangles"] = len(category_triangles)
        triangles.extend(category_triangles)
    triangles = sorted(triangles, key=lambda triangle: triangle["score"], reverse=True)
    return points, triangles, category_stats, warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bbox", required=True)
    parser.add_argument("--types", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--config")
    parser.add_argument("--min-points", type=int)
    parser.add_argument("--base-cell-m", type=float)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--hotspot-percentile", type=float)
    parser.add_argument("--margin-m", type=float)
    parser.add_argument("--dedupe-radius-m", type=float)
    parser.add_argument("--mesh-nx", type=int, default=28)
    parser.add_argument("--kernel-m", type=float, default=450.0)
    parser.add_argument("--min-norm", type=float, default=0.0)
    parser.add_argument("--endpoint", default=overpass_core.DEFAULT_ENDPOINT)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--format", default="both", choices=["json", "geojson", "both"])
    args = parser.parse_args()

    bbox = parse_bbox(args.bbox)
    type_pairs = parse_osm_types(args.types)
    output_dir = results_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)

    global_defaults = {
        "base_cell_m": 1000,
        "min_cell_m": 100,
        "max_depth": 6,
        "min_points": 3,
        "min_area_m2": 10000,
        "max_area_m2": 3_000_000,
        "hotspot_percentile": 0.75,
        "margin_m": 50,
        "dedupe_radius_m": 25,
        "max_overlap_ratio": 0.7,
    }
    global_cfg = dict(global_defaults)
    global_cfg.update(config.get("global", {}))
    if args.min_points is not None:
        global_cfg["min_points"] = args.min_points
    if args.base_cell_m is not None:
        global_cfg["base_cell_m"] = args.base_cell_m
    if args.max_depth is not None:
        global_cfg["max_depth"] = args.max_depth
    if args.hotspot_percentile is not None:
        global_cfg["hotspot_percentile"] = args.hotspot_percentile
    if args.margin_m is not None:
        global_cfg["margin_m"] = args.margin_m
    if args.dedupe_radius_m is not None:
        global_cfg["dedupe_radius_m"] = args.dedupe_radius_m

    category_cfgs = config.get("categories", {})
    fetched_at = datetime.now(timezone.utc).isoformat()
    elements, warnings = fetch_osm_elements(args.bbox, type_pairs, args.endpoint, args.timeout, args.limit)
    if warnings and not elements:
        raise RuntimeError("; ".join(warnings))

    connection = connect_workspace(args.db_path, args.canon_db_path)
    try:
        ensure_layer1_tables(connection)
        feature_rows = upsert_canon_osm_features(connection, elements, fetched_at, "overpass", args.bbox)
        all_points, cells, category_stats, db_warnings = build_mesh_cells_from_db(
            connection,
            bbox,
            type_pairs,
            args.mesh_nx,
            args.kernel_m,
            args.min_norm,
        )
        warnings.extend(db_warnings)
        grid_rows = write_mesh_cells_sqlite(
            connection,
            args.run_id,
            fetched_at,
            args.bbox,
            args.mesh_nx,
            args.kernel_m,
            args.min_norm,
            cells,
        )
    finally:
        connection.close()

    write_json(output_dir / "normalized_points.json", all_points)
    hotspots_payload = {
        "bbox_input": args.bbox,
        "generated_at": fetched_at,
        "run_id": args.run_id,
        "source": "sqlite_canon_osm_features",
        "method": HOTSPOT_METHOD,
        "mesh_nx": args.mesh_nx,
        "kernel_m": args.kernel_m,
        "min_norm": args.min_norm,
        "db_path": str(db_path),
        "cells": cells,
    }
    write_json(output_dir / "hotspot_mesh.json", hotspots_payload)
    if args.format in ("geojson", "both"):
        write_mesh_geojson(output_dir / "hotspot_mesh.geojson", cells)

    summary = {
        "bbox_input": args.bbox,
        "run_id": args.run_id,
        "types": [f"{k}={v}" for k, v, _ in type_pairs],
        "points_per_category": category_stats,
        "hotspots_per_category": {category: len([c for c in cells if c["category"] == category]) for _, _, category in type_pairs},
        "algorithm_params": global_cfg,
        "mesh_params": {"mesh_nx": args.mesh_nx, "kernel_m": args.kernel_m, "min_norm": args.min_norm},
        "sqlite": {
            "db_path": str(db_path),
            "canon_osm_features_upserted": feature_rows,
            "helper_osm_hotspot_mesh_cells_rows": grid_rows,
        },
        "output_files": {
            "normalized_points": str(output_dir / "normalized_points.json"),
            "hotspot_mesh_json": str(output_dir / "hotspot_mesh.json"),
            "hotspot_mesh_geojson": str(output_dir / "hotspot_mesh.geojson"),
            "summary": str(output_dir / "summary.json"),
        },
        "warnings": warnings,
    }
    write_summary(output_dir / "summary.json", summary)
    write_mesh_demo_map(output_dir / "hotspot_mesh_demo.html", bbox, all_points, cells, args.kernel_m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
