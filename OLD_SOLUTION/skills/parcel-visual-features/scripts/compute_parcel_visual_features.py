#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable

try:
    from PIL import Image, ImageDraw
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Pillow is required for parcel visual features.") from exc

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, canon_foreign_key_clause, connect_workspace

DEFAULT_DB_PATH = DEFAULT_ANALYSIS_DB_PATH
DEFAULT_TILE_TEMPLATE = (
    "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
ALGORITHM_VERSION = "deriv_parcel_visual_features_v1"
TILE_SIZE = 256

TileFetcher = Callable[[str, int, int, int, float], Image.Image]


def ensure_visual_feature_table(connection: sqlite3.Connection) -> None:
    helper_fk = canon_foreign_key_clause(connection)
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS deriv_parcel_visual_features (
            parcel_id TEXT PRIMARY KEY,
            computed_at TEXT NOT NULL,
            source_geometry_hash TEXT,
            image_source TEXT NOT NULL,
            zoom INTEGER NOT NULL,
            algorithm_version TEXT NOT NULL,
            masked_pixel_count INTEGER NOT NULL,
            tile_count INTEGER NOT NULL,
            rgb_r_mean REAL NOT NULL,
            rgb_g_mean REAL NOT NULL,
            rgb_b_mean REAL NOT NULL,
            rgb_r_std REAL NOT NULL,
            rgb_g_std REAL NOT NULL,
            rgb_b_std REAL NOT NULL,
            brightness_mean REAL NOT NULL,
            brightness_std REAL NOT NULL,
            green_index_mean REAL NOT NULL,
            green_index_std REAL NOT NULL,
            green_pixel_ratio REAL NOT NULL,
            dark_pixel_ratio REAL NOT NULL,
            bright_pixel_ratio REAL NOT NULL,
            low_saturation_ratio REAL NOT NULL,
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


def world_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    lat = max(-85.05112878, min(85.05112878, lat))
    scale = TILE_SIZE * (2**zoom)
    x = (lon + 180.0) / 360.0 * scale
    sin_lat = math.sin(math.radians(lat))
    y = (0.5 - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)) * scale
    return x, y


def tile_range(polygons: dict[int, dict[int, list[tuple[float, float]]]], zoom: int) -> tuple[int, int, int, int]:
    pixels = [world_pixel(lon, lat, zoom) for rings in polygons.values() for ring in rings.values() for lon, lat in ring]
    if not pixels:
        raise ValueError("parcel has no polygon points")
    min_x = max(0, int(math.floor(min(x for x, _ in pixels) / TILE_SIZE)))
    max_x = max(0, int(math.floor(max(x for x, _ in pixels) / TILE_SIZE)))
    min_y = max(0, int(math.floor(min(y for _, y in pixels) / TILE_SIZE)))
    max_y = max(0, int(math.floor(max(y for _, y in pixels) / TILE_SIZE)))
    return min_x, min_y, max_x, max_y


def fetch_tile(tile_template: str, z: int, x: int, y: int, timeout: float) -> Image.Image:
    url = tile_template.format(z=z, x=x, y=y)
    request = urllib.request.Request(url, headers={"User-Agent": "parcel-visual-features/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    return Image.open(BytesIO(data)).convert("RGB")


def build_mosaic(
    polygons: dict[int, dict[int, list[tuple[float, float]]]],
    zoom: int,
    tile_template: str,
    timeout: float,
    tile_fetcher: TileFetcher = fetch_tile,
) -> tuple[Image.Image, int, int, int]:
    min_tile_x, min_tile_y, max_tile_x, max_tile_y = tile_range(polygons, zoom)
    cols = max_tile_x - min_tile_x + 1
    rows = max_tile_y - min_tile_y + 1
    mosaic = Image.new("RGB", (cols * TILE_SIZE, rows * TILE_SIZE))
    tile_count = 0
    for tile_y in range(min_tile_y, max_tile_y + 1):
        for tile_x in range(min_tile_x, max_tile_x + 1):
            tile = tile_fetcher(tile_template, zoom, tile_x, tile_y, timeout).convert("RGB")
            mosaic.paste(tile, ((tile_x - min_tile_x) * TILE_SIZE, (tile_y - min_tile_y) * TILE_SIZE))
            tile_count += 1
    return mosaic, min_tile_x * TILE_SIZE, min_tile_y * TILE_SIZE, tile_count


def build_mask(
    polygons: dict[int, dict[int, list[tuple[float, float]]]],
    zoom: int,
    origin_x: int,
    origin_y: int,
    size: tuple[int, int],
) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for rings in polygons.values():
        for ring_index, ring in sorted(rings.items()):
            points = [(x - origin_x, y - origin_y) for lon, lat in close_ring(ring) for x, y in [world_pixel(lon, lat, zoom)]]
            if len(points) >= 3:
                draw.polygon(points, fill=255 if ring_index == 0 else 0)
    return mask


def _std(total: float, total_sq: float, count: int) -> float:
    if count <= 0:
        return 0.0
    variance = max(0.0, total_sq / count - (total / count) ** 2)
    return math.sqrt(variance)


def pixel_stats(rgb: Image.Image, mask: Image.Image) -> dict:
    rgb_data = rgb.convert("RGB").getdata()
    mask_data = mask.getdata()
    count = 0
    sums = {"r": 0.0, "g": 0.0, "b": 0.0, "brightness": 0.0, "green_index": 0.0}
    sums_sq = {key: 0.0 for key in sums}
    green = dark = bright = low_sat = 0
    for (r, g, b), m in zip(rgb_data, mask_data):
        if not m:
            continue
        count += 1
        brightness = (r + g + b) / 3.0
        green_index = (g - r) / max(1.0, g + r)
        values = {"r": r, "g": g, "b": b, "brightness": brightness, "green_index": green_index}
        for key, value in values.items():
            sums[key] += value
            sums_sq[key] += value * value
        if g > 60 and g >= r * 1.05 and g >= b * 1.05:
            green += 1
        if brightness < 70:
            dark += 1
        if brightness > 190:
            bright += 1
        if max(r, g, b) - min(r, g, b) < 25:
            low_sat += 1
    if count <= 0:
        raise ValueError("parcel mask has no pixels")
    return {
        "masked_pixel_count": count,
        "rgb_r_mean": sums["r"] / count,
        "rgb_g_mean": sums["g"] / count,
        "rgb_b_mean": sums["b"] / count,
        "rgb_r_std": _std(sums["r"], sums_sq["r"], count),
        "rgb_g_std": _std(sums["g"], sums_sq["g"], count),
        "rgb_b_std": _std(sums["b"], sums_sq["b"], count),
        "brightness_mean": sums["brightness"] / count,
        "brightness_std": _std(sums["brightness"], sums_sq["brightness"], count),
        "green_index_mean": sums["green_index"] / count,
        "green_index_std": _std(sums["green_index"], sums_sq["green_index"], count),
        "green_pixel_ratio": green / count,
        "dark_pixel_ratio": dark / count,
        "bright_pixel_ratio": bright / count,
        "low_saturation_ratio": low_sat / count,
    }


def compute_features(
    parcel_id: str,
    polygons: dict[int, dict[int, list[tuple[float, float]]]],
    zoom: int,
    tile_template: str,
    timeout: float,
    tile_fetcher: TileFetcher = fetch_tile,
) -> dict:
    rgb, origin_x, origin_y, tile_count = build_mosaic(polygons, zoom, tile_template, timeout, tile_fetcher)
    mask = build_mask(polygons, zoom, origin_x, origin_y, rgb.size)
    stats = pixel_stats(rgb, mask)
    raw = {
        "algorithm_version": ALGORITHM_VERSION,
        "tile_template": tile_template,
        "zoom": zoom,
        "thresholds": {
            "green": "g > 60 and g >= r*1.05 and g >= b*1.05",
            "dark_brightness_lt": 70,
            "bright_brightness_gt": 190,
            "low_saturation_rgb_range_lt": 25,
        },
    }
    return {
        "parcel_id": parcel_id,
        "source_geometry_hash": geometry_hash(polygons),
        "image_source": tile_template,
        "zoom": zoom,
        "algorithm_version": ALGORITHM_VERSION,
        "tile_count": tile_count,
        "raw_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
        **stats,
    }


def upsert_feature(connection: sqlite3.Connection, feature: dict, computed_at: str) -> None:
    ensure_visual_feature_table(connection)
    connection.execute(
        """
        INSERT INTO deriv_parcel_visual_features (
            parcel_id, computed_at, source_geometry_hash, image_source, zoom, algorithm_version,
            masked_pixel_count, tile_count,
            rgb_r_mean, rgb_g_mean, rgb_b_mean, rgb_r_std, rgb_g_std, rgb_b_std,
            brightness_mean, brightness_std, green_index_mean, green_index_std,
            green_pixel_ratio, dark_pixel_ratio, bright_pixel_ratio, low_saturation_ratio, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(parcel_id) DO UPDATE SET
            computed_at = excluded.computed_at,
            source_geometry_hash = excluded.source_geometry_hash,
            image_source = excluded.image_source,
            zoom = excluded.zoom,
            algorithm_version = excluded.algorithm_version,
            masked_pixel_count = excluded.masked_pixel_count,
            tile_count = excluded.tile_count,
            rgb_r_mean = excluded.rgb_r_mean,
            rgb_g_mean = excluded.rgb_g_mean,
            rgb_b_mean = excluded.rgb_b_mean,
            rgb_r_std = excluded.rgb_r_std,
            rgb_g_std = excluded.rgb_g_std,
            rgb_b_std = excluded.rgb_b_std,
            brightness_mean = excluded.brightness_mean,
            brightness_std = excluded.brightness_std,
            green_index_mean = excluded.green_index_mean,
            green_index_std = excluded.green_index_std,
            green_pixel_ratio = excluded.green_pixel_ratio,
            dark_pixel_ratio = excluded.dark_pixel_ratio,
            bright_pixel_ratio = excluded.bright_pixel_ratio,
            low_saturation_ratio = excluded.low_saturation_ratio,
            raw_json = excluded.raw_json
        """,
        (
            feature["parcel_id"],
            computed_at,
            feature["source_geometry_hash"],
            feature["image_source"],
            feature["zoom"],
            feature["algorithm_version"],
            feature["masked_pixel_count"],
            feature["tile_count"],
            feature["rgb_r_mean"],
            feature["rgb_g_mean"],
            feature["rgb_b_mean"],
            feature["rgb_r_std"],
            feature["rgb_g_std"],
            feature["rgb_b_std"],
            feature["brightness_mean"],
            feature["brightness_std"],
            feature["green_index_mean"],
            feature["green_index_std"],
            feature["green_pixel_ratio"],
            feature["dark_pixel_ratio"],
            feature["bright_pixel_ratio"],
            feature["low_saturation_ratio"],
            feature["raw_json"],
        ),
    )


def progress_bar(processed: int, total: int, width: int = 18) -> str:
    if total <= 0:
        return "[" + "." * width + "]"
    filled = min(width, max(0, round(width * processed / total)))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def compute_to_db(
    connection: sqlite3.Connection,
    ids: list[str],
    zoom: int = 18,
    tile_template: str = DEFAULT_TILE_TEMPLATE,
    timeout: float = 20.0,
    progress_every: int = 10,
    tile_fetcher: TileFetcher = fetch_tile,
) -> dict:
    ensure_visual_feature_table(connection)
    computed_at = datetime.now(timezone.utc).isoformat()
    computed = skipped = errors = 0
    examples: list[str] = []
    for index, parcel_id in enumerate(ids, start=1):
        try:
            polygons = load_polygon_rings(connection, parcel_id)
            if not polygons:
                skipped += 1
                continue
            feature = compute_features(parcel_id, polygons, zoom, tile_template, timeout, tile_fetcher)
            upsert_feature(connection, feature, computed_at)
            computed += 1
        except Exception as exc:
            errors += 1
            if len(examples) < 3:
                examples.append(f"{parcel_id}: {exc}")
        if progress_every and (index % progress_every == 0 or index == len(ids)):
            print(
                "PROGRESS "
                + json.dumps(
                    {
                        "stage": "deriv_parcel_visual_features",
                        "processed": index,
                        "total": len(ids),
                        "percent": round(index * 100.0 / len(ids), 1) if ids else 100.0,
                        "bar": progress_bar(index, len(ids)),
                        "computed": computed,
                        "errors": errors,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
    connection.commit()
    return {"requested": len(ids), "computed": computed, "skipped": skipped, "errors": errors, "error_examples": examples}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute compact parcel visual features from imagery clipped to SQLite parcel polygons.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--run-id")
    parser.add_argument("--parcel-id")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--zoom", type=int, default=18)
    parser.add_argument("--tile-template", default=DEFAULT_TILE_TEMPLATE)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--summary-output")
    args = parser.parse_args()

    connection = connect_workspace(args.db_path, args.canon_db_path)
    try:
        ids = parcel_ids(connection, args.run_id, args.all, args.parcel_id)
        summary = compute_to_db(connection, ids, args.zoom, args.tile_template, args.timeout, args.progress_every)
    finally:
        connection.close()

    summary.update(
        {
            "run_id": args.run_id,
            "parcel_id": args.parcel_id,
            "table": "deriv_parcel_visual_features",
            "zoom": args.zoom,
            "image_source": args.tile_template,
        }
    )
    if args.summary_output:
        output = Path(args.summary_output)
    else:
        output = Path("results") / ("visual_features_summary.json" if not args.run_id else f"analysis_{args.run_id}/visual_features_summary.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=True, sort_keys=True))
    return 1 if summary["errors"] and not summary["computed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
