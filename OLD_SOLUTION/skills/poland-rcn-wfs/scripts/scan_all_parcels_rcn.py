#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import fetch_rcn_wfs as rcn
from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, canon_index, canon_table, connect_workspace


DEFAULT_DB_PATH = DEFAULT_ANALYSIS_DB_PATH


def ensure_scan_tables(connection: sqlite3.Connection) -> None:
    checks = canon_table(connection, "canon_rcn_parcel_checks")
    checked_index = canon_index(connection, "idx_canon_rcn_parcel_checks_status")
    run_index = canon_index(connection, "idx_canon_rcn_parcel_checks_run")
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {checks} (
            parcel_id TEXT PRIMARY KEY,
            checked_at TEXT NOT NULL,
            run_id TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            bbox_query TEXT,
            tile_id TEXT,
            rcn_records INTEGER NOT NULL DEFAULT 0,
            priced_records INTEGER NOT NULL DEFAULT 0,
            min_price_per_m2 REAL,
            avg_price_per_m2 REAL,
            max_price_per_m2 REAL,
            min_inflation_adjusted_price_per_m2 REAL,
            avg_inflation_adjusted_price_per_m2 REAL,
            max_inflation_adjusted_price_per_m2 REAL,
            error_message TEXT
        )
        """
    )
    connection.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {checked_index}
            ON canon_rcn_parcel_checks(status, checked_at)
        """
    )
    connection.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {run_index}
            ON canon_rcn_parcel_checks(run_id, tile_id)
        """
    )
    connection.commit()


def parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    parts = [float(part.strip()) for part in bbox.split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox-4326 must be minLon,minLat,maxLon,maxLat")
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError("--bbox-4326 has invalid bounds")
    return min_lon, min_lat, max_lon, max_lat


def margin_degrees(margin_m: float, lat: float) -> tuple[float, float]:
    lat_margin = margin_m / 111_320.0
    lon_margin = margin_m / max(1.0, 111_320.0 * math.cos(math.radians(lat)))
    return lon_margin, lat_margin


def bbox_sql_filter(bbox: tuple[float, float, float, float] | None) -> tuple[str, list[float]]:
    if not bbox:
        return "", []
    min_lon, min_lat, max_lon, max_lat = bbox
    return (
        """
        AND bbox_max_lon >= ?
        AND bbox_min_lon <= ?
        AND bbox_max_lat >= ?
        AND bbox_min_lat <= ?
        """,
        [min_lon, max_lon, min_lat, max_lat],
    )


def load_parcels(
    connection: sqlite3.Connection,
    bbox: tuple[float, float, float, float] | None,
    limit_parcels: int,
    skip_checked: bool,
) -> list[sqlite3.Row]:
    filter_sql, params = bbox_sql_filter(bbox)
    skip_sql = ""
    if skip_checked:
        skip_sql = """
        AND NOT EXISTS (
            SELECT 1
            FROM canon_rcn_parcel_checks c
            WHERE c.parcel_id = p.parcel_id
              AND c.status = 'ok'
        )
        """
    limit_sql = "LIMIT ?" if limit_parcels > 0 else ""
    if limit_parcels > 0:
        params.append(limit_parcels)
    return connection.execute(
        f"""
        SELECT parcel_id, centroid_lat, centroid_lon,
               bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon
        FROM canon_parcels p
        WHERE centroid_lat IS NOT NULL
          AND centroid_lon IS NOT NULL
          AND bbox_min_lat IS NOT NULL
          AND bbox_min_lon IS NOT NULL
          AND bbox_max_lat IS NOT NULL
          AND bbox_max_lon IS NOT NULL
          {filter_sql}
          {skip_sql}
        ORDER BY centroid_lat, centroid_lon, parcel_id
        {limit_sql}
        """,
        params,
    ).fetchall()


def build_tiles(parcels: list[sqlite3.Row], tile_size_km: float, margin_m: float) -> list[dict]:
    if not parcels:
        return []
    min_lat = min(float(row["centroid_lat"]) for row in parcels)
    min_lon = min(float(row["centroid_lon"]) for row in parcels)
    mid_lat = sum(float(row["centroid_lat"]) for row in parcels) / len(parcels)
    lat_step = max(0.0001, tile_size_km / 111.32)
    lon_step = max(0.0001, tile_size_km / max(1.0, 111.32 * math.cos(math.radians(mid_lat))))
    buckets: dict[tuple[int, int], list[sqlite3.Row]] = defaultdict(list)
    for row in parcels:
        y = int((float(row["centroid_lat"]) - min_lat) / lat_step)
        x = int((float(row["centroid_lon"]) - min_lon) / lon_step)
        buckets[(y, x)].append(row)
    tiles = []
    for index, ((y, x), rows) in enumerate(sorted(buckets.items()), 1):
        center_lat = sum(float(row["centroid_lat"]) for row in rows) / len(rows)
        lon_margin, lat_margin = margin_degrees(margin_m, center_lat)
        min_tile_lon = min(float(row["bbox_min_lon"]) for row in rows) - lon_margin
        min_tile_lat = min(float(row["bbox_min_lat"]) for row in rows) - lat_margin
        max_tile_lon = max(float(row["bbox_max_lon"]) for row in rows) + lon_margin
        max_tile_lat = max(float(row["bbox_max_lat"]) for row in rows) + lat_margin
        tiles.append(
            {
                "tile_id": f"tile_{index:06d}_{y}_{x}",
                "bbox_4326": f"{min_tile_lon:.8f},{min_tile_lat:.8f},{max_tile_lon:.8f},{max_tile_lat:.8f}",
                "parcel_ids": [row["parcel_id"] for row in rows],
            }
        )
    return tiles


def fetch_tile(
    connection: sqlite3.Connection,
    args: argparse.Namespace,
    bbox_4326: str,
) -> tuple[int, int]:
    bbox_2180 = rcn.bbox_4326_to_2180(bbox_4326)
    fetch_args = SimpleNamespace(cql=None, limit=args.limit_per_tile, page_size=args.page_size, timeout=args.timeout)
    inflation_index = rcn.load_inflation_index(args.inflation_index_json)
    reference_year = rcn.inflation_reference_year(inflation_index, args.inflation_reference_year)
    query = {
        "scan_mode": "all_parcels_tile",
        "bbox_4326": bbox_4326,
        "bbox_2180": bbox_2180,
        "limit": args.limit_per_tile,
        "page_size": args.page_size,
        "inflation_reference_year": reference_year,
    }
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = fetched = changed = 0
    page_size = min(args.page_size, args.limit_per_tile)
    while start < args.limit_per_tile:
        count = min(page_size, args.limit_per_tile - start)
        url = rcn.build_url(fetch_args, start, count, bbox_2180)
        xml_text = rcn.fetch_text(url, args.timeout)
        records, returned = rcn.parse_records(xml_text)
        fetched += len(records)
        changed += rcn.upsert_records(
            connection,
            records,
            args.run_id,
            bbox_4326,
            query,
            fetched_at,
            inflation_index,
            reference_year,
        )
        if not records or returned == 0 or len(records) < count:
            break
        start += count
    return fetched, changed


def price_stats_for_parcels(connection: sqlite3.Connection, run_id: str, parcel_ids: list[str]) -> dict[str, dict]:
    stats = {
        parcel_id: {
            "rcn_records": 0,
            "priced_records": 0,
            "min_price_per_m2": None,
            "avg_price_per_m2": None,
            "max_price_per_m2": None,
            "min_inflation_adjusted_price_per_m2": None,
            "avg_inflation_adjusted_price_per_m2": None,
            "max_inflation_adjusted_price_per_m2": None,
        }
        for parcel_id in parcel_ids
    }
    if not parcel_ids:
        return stats
    for offset in range(0, len(parcel_ids), 500):
        chunk = parcel_ids[offset : offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"""
            SELECT parcel_id,
                   COUNT(*) AS rcn_records,
                   SUM(CASE WHEN price_per_m2 IS NOT NULL AND price_per_m2 > 0 THEN 1 ELSE 0 END) AS priced_records,
                   MIN(price_per_m2) AS min_price_per_m2,
                   AVG(price_per_m2) AS avg_price_per_m2,
                   MAX(price_per_m2) AS max_price_per_m2,
                   MIN(inflation_adjusted_price_per_m2) AS min_inflation_adjusted_price_per_m2,
                   AVG(inflation_adjusted_price_per_m2) AS avg_inflation_adjusted_price_per_m2,
                   MAX(inflation_adjusted_price_per_m2) AS max_inflation_adjusted_price_per_m2
            FROM canon_rcn_price_observations
            WHERE run_id = ?
              AND parcel_id IN ({placeholders})
            GROUP BY parcel_id
            """,
            [run_id, *chunk],
        ).fetchall()
        for row in rows:
            stats[row["parcel_id"]] = dict(row)
    return stats


def mark_parcels(
    connection: sqlite3.Connection,
    run_id: str,
    tile_id: str,
    bbox_4326: str,
    parcel_ids: list[str],
    status: str,
    error_message: str | None = None,
) -> None:
    checked_at = datetime.now(timezone.utc).isoformat()
    stats = price_stats_for_parcels(connection, run_id, parcel_ids) if status == "ok" else {}
    for parcel_id in parcel_ids:
        row = stats.get(parcel_id, {})
        connection.execute(
            """
            INSERT INTO canon_rcn_parcel_checks (
                parcel_id, checked_at, run_id, source, status, bbox_query, tile_id,
                rcn_records, priced_records,
                min_price_per_m2, avg_price_per_m2, max_price_per_m2,
                min_inflation_adjusted_price_per_m2, avg_inflation_adjusted_price_per_m2, max_inflation_adjusted_price_per_m2,
                error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(parcel_id) DO UPDATE SET
                checked_at = excluded.checked_at,
                run_id = excluded.run_id,
                source = excluded.source,
                status = excluded.status,
                bbox_query = excluded.bbox_query,
                tile_id = excluded.tile_id,
                rcn_records = excluded.rcn_records,
                priced_records = excluded.priced_records,
                min_price_per_m2 = excluded.min_price_per_m2,
                avg_price_per_m2 = excluded.avg_price_per_m2,
                max_price_per_m2 = excluded.max_price_per_m2,
                min_inflation_adjusted_price_per_m2 = excluded.min_inflation_adjusted_price_per_m2,
                avg_inflation_adjusted_price_per_m2 = excluded.avg_inflation_adjusted_price_per_m2,
                max_inflation_adjusted_price_per_m2 = excluded.max_inflation_adjusted_price_per_m2,
                error_message = excluded.error_message
            """,
            (
                parcel_id,
                checked_at,
                run_id,
                rcn.SOURCE,
                status,
                bbox_4326,
                tile_id,
                int(row.get("rcn_records") or 0),
                int(row.get("priced_records") or 0),
                row.get("min_price_per_m2"),
                row.get("avg_price_per_m2"),
                row.get("max_price_per_m2"),
                row.get("min_inflation_adjusted_price_per_m2"),
                row.get("avg_inflation_adjusted_price_per_m2"),
                row.get("max_inflation_adjusted_price_per_m2"),
                error_message[:500] if error_message else None,
            ),
        )
    connection.commit()


def progress(payload: dict) -> None:
    print("PROGRESS " + json.dumps(payload, ensure_ascii=True, sort_keys=True), file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan all canonical parcels with RCN WFS tiles and persist parcel-level coverage.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--bbox-4326", help="Optional WGS84 test bbox: minLon,minLat,maxLon,maxLat.")
    parser.add_argument("--tile-size-km", type=float, default=2.0)
    parser.add_argument("--tile-margin-m", type=float, default=50.0)
    parser.add_argument("--limit-tiles", type=int, default=0, help="0 means all tiles.")
    parser.add_argument("--limit-parcels", type=int, default=0, help="0 means all matching parcels.")
    parser.add_argument("--limit-per-tile", type=int, default=1000)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--sleep-ms", type=int, default=0)
    parser.add_argument("--skip-checked", action="store_true", help="Skip parcels already marked status=ok in canon_rcn_parcel_checks.")
    parser.add_argument("--continue-on-error", action="store_true", help="Mark failed tiles as error and continue.")
    parser.add_argument("--inflation-index-json", help="Optional CPI/index JSON.")
    parser.add_argument("--inflation-reference-year")
    parser.add_argument("--summary-output")
    args = parser.parse_args()

    bbox = parse_bbox(args.bbox_4326)
    connection = connect_workspace(args.db_path, args.canon_db_path)
    connection.row_factory = sqlite3.Row
    try:
        rcn.ensure_layer3_tables(connection)
        ensure_scan_tables(connection)
        parcels = load_parcels(connection, bbox, args.limit_parcels, args.skip_checked)
        tiles = build_tiles(parcels, args.tile_size_km, args.tile_margin_m)
        if args.limit_tiles > 0:
            tiles = tiles[: args.limit_tiles]
        totals = {
            "run_id": args.run_id,
            "selected_parcels": len(parcels),
            "tiles": len(tiles),
            "ok_tiles": 0,
            "error_tiles": 0,
            "fetched_records": 0,
            "inserted_or_updated": 0,
            "checked_parcels": 0,
            "priced_parcels": 0,
            "bbox_4326": args.bbox_4326,
        }
        progress({"stage": "rcn_scan_start", **totals})
        for index, tile in enumerate(tiles, 1):
            try:
                fetched, changed = fetch_tile(connection, args, tile["bbox_4326"])
                mark_parcels(connection, args.run_id, tile["tile_id"], tile["bbox_4326"], tile["parcel_ids"], "ok")
                stats = price_stats_for_parcels(connection, args.run_id, tile["parcel_ids"])
                priced_parcels = sum(1 for row in stats.values() if int(row.get("priced_records") or 0) > 0)
                totals["ok_tiles"] += 1
                totals["fetched_records"] += fetched
                totals["inserted_or_updated"] += changed
                totals["checked_parcels"] += len(tile["parcel_ids"])
                totals["priced_parcels"] += priced_parcels
                progress(
                    {
                        "stage": "rcn_scan_tile",
                        "run_id": args.run_id,
                        "tile": index,
                        "tiles": len(tiles),
                        "tile_id": tile["tile_id"],
                        "status": "ok",
                        "parcels": len(tile["parcel_ids"]),
                        "fetched_records": fetched,
                        "inserted_or_updated": changed,
                        "priced_parcels": priced_parcels,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - batch scanner must persist compact failure state.
                totals["error_tiles"] += 1
                mark_parcels(connection, args.run_id, tile["tile_id"], tile["bbox_4326"], tile["parcel_ids"], "error", str(exc))
                progress(
                    {
                        "stage": "rcn_scan_tile",
                        "run_id": args.run_id,
                        "tile": index,
                        "tiles": len(tiles),
                        "tile_id": tile["tile_id"],
                        "status": "error",
                        "parcels": len(tile["parcel_ids"]),
                        "error": str(exc)[:180],
                    }
                )
                if not args.continue_on_error:
                    raise
            if args.sleep_ms > 0:
                time.sleep(args.sleep_ms / 1000.0)
        totals["rcn_records_total_for_run"] = connection.execute(
            "SELECT COUNT(*) FROM canon_rcn_price_observations WHERE run_id = ?", (args.run_id,)
        ).fetchone()[0]
        totals["status"] = "ok" if totals["error_tiles"] == 0 else "partial_error"
    finally:
        connection.close()

    output = Path(args.summary_output) if args.summary_output else Path("results") / f"analysis_{args.run_id}" / "rcn_parcel_scan_summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(totals, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **totals}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
