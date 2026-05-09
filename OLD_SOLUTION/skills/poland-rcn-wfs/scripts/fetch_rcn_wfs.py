#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, canon_index, canon_table, connect_workspace

BASE_URL = "https://mapy.geoportal.gov.pl/wss/service/rcn"
DEFAULT_DB_PATH = DEFAULT_ANALYSIS_DB_PATH
SOURCE = "gugik_rcn_wfs"
NS = {
    "ms": "http://mapserver.gis.umn.edu/mapserver",
}
FIELDS = [
    "serwis_rcn",
    "teryt",
    "tran_przestrzen_nazw",
    "tran_lokalny_id_iip",
    "tran_wersja_id",
    "tran_rodzaj_trans",
    "tran_rodzaj_rynku",
    "tran_sprzedajacy",
    "tran_kupujacy",
    "tran_vat",
    "dok_data",
    "nier_rodzaj",
    "nier_prawo",
    "nier_udzial",
    "nier_pow_gruntu",
    "nier_cena_brutto",
    "nier_vat",
    "dzi_id_dzialki",
    "dzi_nr_dzialki",
    "dzi_przezn_wmpzp",
    "dzi_pow_ewid",
    "dzi_sposob_uzyt",
    "dzi_cena_brutto",
    "dzi_vat",
    "dzi_adres",
]


def ensure_layer3_tables(connection: sqlite3.Connection) -> None:
    observations = canon_table(connection, "canon_rcn_price_observations")
    run_price_index = canon_index(connection, "idx_canon_rcn_price_observations_run_price")
    parcel_index = canon_index(connection, "idx_canon_rcn_price_observations_parcel")
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {observations} (
            source TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            run_id TEXT NOT NULL,
            bbox_query TEXT,
            query_json TEXT NOT NULL,
            teryt TEXT,
            transaction_date TEXT,
            transaction_type TEXT,
            market_type TEXT,
            seller_type TEXT,
            buyer_type TEXT,
            property_type TEXT,
            property_right TEXT,
            parcel_id TEXT,
            parcel_number TEXT,
            address TEXT,
            land_use TEXT,
            zoning TEXT,
            area_m2 REAL,
            price_pln REAL,
            price_per_m2 REAL,
            inflation_reference_year TEXT,
            inflation_factor REAL,
            inflation_adjusted_price_pln REAL,
            inflation_adjusted_price_per_m2 REAL,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (source, source_record_id, run_id)
        )
        """
    )
    add_missing_column(connection, observations, "inflation_reference_year", "TEXT")
    add_missing_column(connection, observations, "inflation_factor", "REAL")
    add_missing_column(connection, observations, "inflation_adjusted_price_pln", "REAL")
    add_missing_column(connection, observations, "inflation_adjusted_price_per_m2", "REAL")
    connection.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {run_price_index}
            ON canon_rcn_price_observations(run_id, price_per_m2)
        """
    )
    connection.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {parcel_index}
            ON canon_rcn_price_observations(parcel_id)
        """
    )
    connection.commit()


def add_missing_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if "." in table:
        schema, table_name = table.split(".", 1)
        rows = connection.execute(f"PRAGMA {schema}.table_info({table_name})").fetchall()
    else:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    columns = {row[1] for row in rows}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def lonlat_to_epsg2180(lon: float, lat: float) -> tuple[float, float]:
    a = 6378137.0
    inv_f = 298.257222101
    f = 1.0 / inv_f
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon0 = math.radians(19.0)
    k0 = 0.9993
    x0 = -5300000.0
    y0 = 500000.0

    n = a / math.sqrt(1 - e2 * math.sin(lat_rad) ** 2)
    t = math.tan(lat_rad) ** 2
    c = ep2 * math.cos(lat_rad) ** 2
    aa = (lon_rad - lon0) * math.cos(lat_rad)
    m = a * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * lat_rad)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * lat_rad)
        - (35 * e2**3 / 3072) * math.sin(6 * lat_rad)
    )
    northing_x = x0 + k0 * (
        m
        + n
        * math.tan(lat_rad)
        * (
            aa**2 / 2
            + (5 - t + 9 * c + 4 * c**2) * aa**4 / 24
            + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * aa**6 / 720
        )
    )
    easting_y = y0 + k0 * n * (
        aa
        + (1 - t + c) * aa**3 / 6
        + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * aa**5 / 120
    )
    return northing_x, easting_y


def bbox_4326_to_2180(bbox: str) -> str:
    min_lon, min_lat, max_lon, max_lat = [float(part.strip()) for part in bbox.split(",")]
    points = [
        lonlat_to_epsg2180(min_lon, min_lat),
        lonlat_to_epsg2180(min_lon, max_lat),
        lonlat_to_epsg2180(max_lon, min_lat),
        lonlat_to_epsg2180(max_lon, max_lat),
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return f"{min(xs):.3f},{min(ys):.3f},{max(xs):.3f},{max(ys):.3f}"


def layer2_bbox_4326(connection: sqlite3.Connection, run_id: str, margin_m: float) -> str | None:
    row = connection.execute(
        """
        SELECT MIN(p.bbox_min_lon), MIN(p.bbox_min_lat), MAX(p.bbox_max_lon), MAX(p.bbox_max_lat)
        FROM canon_parcels p
        JOIN helper_layer2_run_parcels rp ON rp.parcel_id = p.parcel_id
        WHERE rp.run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if not row or any(value is None for value in row):
        return None
    min_lon, min_lat, max_lon, max_lat = [float(value) for value in row]
    lat_margin = margin_m / 111_320.0
    mid_lat = (min_lat + max_lat) / 2.0
    lon_margin = margin_m / max(1.0, 111_320.0 * math.cos(math.radians(mid_lat)))
    return f"{min_lon - lon_margin},{min_lat - lat_margin},{max_lon + lon_margin},{max_lat + lat_margin}"


def build_url(args: argparse.Namespace, start_index: int, count: int, bbox_2180: str | None) -> str:
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": "ms:dzialki",
        "COUNT": str(count),
        "STARTINDEX": str(start_index),
    }
    if args.cql:
        params["CQL_FILTER"] = args.cql
    if bbox_2180:
        params["BBOX"] = f"{bbox_2180},EPSG:2180"
    return BASE_URL + "?" + urllib.parse.urlencode(params)


def fetch_text(url: str, timeout: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Codex RCN WFS helper"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_float(value: str | None) -> float | None:
    if not value:
        return None
    clean = value.strip().replace(" ", "").replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None


def area_to_m2(record: dict[str, str]) -> float | None:
    area = parse_float(record.get("dzi_pow_ewid")) or parse_float(record.get("nier_pow_gruntu"))
    if area is None:
        return None
    return area * 10_000 if 0 < area < 100 else area


def price_pln(record: dict[str, str]) -> float | None:
    return parse_float(record.get("dzi_cena_brutto")) or parse_float(record.get("nier_cena_brutto"))


def load_inflation_index(path: str | None) -> dict[str, float]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("inflation index JSON must be an object like {'2024': 100.0, '2025': 104.0}")
    return {str(key): float(value) for key, value in raw.items()}


def inflation_reference_year(index: dict[str, float], requested: str | None) -> str | None:
    if not index:
        return None
    if requested:
        if requested not in index:
            raise ValueError(f"inflation reference year {requested!r} is not present in index JSON")
        return requested
    return sorted(index)[-1]


def inflation_factor_for(record: dict[str, str], index: dict[str, float], reference_year: str | None) -> float | None:
    if not index or not reference_year:
        return None
    transaction_date = record.get("dok_data") or ""
    transaction_year = transaction_date[:4]
    if transaction_year not in index:
        return None
    base = index.get(transaction_year)
    reference = index.get(reference_year)
    if not base or not reference:
        return None
    return reference / base


def parse_records(xml_text: str) -> tuple[list[dict[str, str]], int | None]:
    root = ET.fromstring(xml_text)
    returned_text = root.attrib.get("numberReturned")
    try:
        number_returned = int(returned_text) if returned_text is not None else None
    except ValueError:
        number_returned = None
    records = []
    for node in root.findall(".//ms:dzialki", NS):
        row = {"gml_id": node.attrib.get("{http://www.opengis.net/gml/3.2}id", "")}
        for field in FIELDS:
            child = node.find(f"ms:{field}", NS)
            row[field] = (child.text or "").strip() if child is not None else ""
        records.append(row)
    return records, number_returned


def source_record_id(record: dict[str, str]) -> str:
    if record.get("gml_id"):
        return record["gml_id"]
    basis = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def upsert_records(
    connection: sqlite3.Connection,
    records: list[dict[str, str]],
    run_id: str,
    bbox_query: str | None,
    query: dict,
    fetched_at: str,
    inflation_index: dict[str, float] | None = None,
    reference_year: str | None = None,
) -> int:
    ensure_layer3_tables(connection)
    changed = 0
    query_json = json.dumps(query, ensure_ascii=False, sort_keys=True)
    for record in records:
        area_m2 = area_to_m2(record)
        price = price_pln(record)
        price_per_m2 = price / area_m2 if price and area_m2 else None
        inflation_factor = inflation_factor_for(record, inflation_index or {}, reference_year)
        adjusted_price = price * inflation_factor if price and inflation_factor else None
        adjusted_price_per_m2 = price_per_m2 * inflation_factor if price_per_m2 and inflation_factor else None
        before = connection.total_changes
        connection.execute(
            """
            INSERT INTO canon_rcn_price_observations (
                source, source_record_id, fetched_at, run_id, bbox_query, query_json,
                teryt, transaction_date, transaction_type, market_type, seller_type, buyer_type,
                property_type, property_right, parcel_id, parcel_number, address, land_use, zoning,
                area_m2, price_pln, price_per_m2,
                inflation_reference_year, inflation_factor, inflation_adjusted_price_pln, inflation_adjusted_price_per_m2,
                raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_record_id, run_id) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                run_id = excluded.run_id,
                bbox_query = excluded.bbox_query,
                query_json = excluded.query_json,
                teryt = excluded.teryt,
                transaction_date = excluded.transaction_date,
                transaction_type = excluded.transaction_type,
                market_type = excluded.market_type,
                seller_type = excluded.seller_type,
                buyer_type = excluded.buyer_type,
                property_type = excluded.property_type,
                property_right = excluded.property_right,
                parcel_id = excluded.parcel_id,
                parcel_number = excluded.parcel_number,
                address = excluded.address,
                land_use = excluded.land_use,
                zoning = excluded.zoning,
                area_m2 = excluded.area_m2,
                price_pln = excluded.price_pln,
                price_per_m2 = excluded.price_per_m2,
                inflation_reference_year = excluded.inflation_reference_year,
                inflation_factor = excluded.inflation_factor,
                inflation_adjusted_price_pln = excluded.inflation_adjusted_price_pln,
                inflation_adjusted_price_per_m2 = excluded.inflation_adjusted_price_per_m2,
                raw_json = excluded.raw_json
            """,
            (
                SOURCE,
                source_record_id(record),
                fetched_at,
                run_id,
                bbox_query,
                query_json,
                record.get("teryt"),
                record.get("dok_data"),
                record.get("tran_rodzaj_trans"),
                record.get("tran_rodzaj_rynku"),
                record.get("tran_sprzedajacy"),
                record.get("tran_kupujacy"),
                record.get("nier_rodzaj"),
                record.get("nier_prawo"),
                record.get("dzi_id_dzialki"),
                record.get("dzi_nr_dzialki"),
                record.get("dzi_adres"),
                record.get("dzi_sposob_uzyt"),
                record.get("dzi_przezn_wmpzp"),
                area_m2,
                price,
                price_per_m2,
                reference_year,
                inflation_factor,
                adjusted_price,
                adjusted_price_per_m2,
                json.dumps(record, ensure_ascii=False, sort_keys=True),
            ),
        )
        if connection.total_changes > before:
            changed += 1
    connection.commit()
    return changed


def median(values: list[float]) -> float | None:
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2:
        return clean[mid]
    return (clean[mid - 1] + clean[mid]) / 2.0


def rounded(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def run_summary(connection: sqlite3.Connection, run_id: str) -> dict:
    rows = connection.execute(
        """
        SELECT price_per_m2, inflation_adjusted_price_per_m2
        FROM canon_rcn_price_observations
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchall()
    prices = [float(row[0]) for row in rows if row[0] is not None and float(row[0]) > 0]
    adjusted_prices = [float(row[1]) for row in rows if row[1] is not None and float(row[1]) > 0]
    total = connection.execute("SELECT COUNT(*) FROM canon_rcn_price_observations WHERE run_id = ?", (run_id,)).fetchone()[0]
    return {
        "run_id": run_id,
        "rcn_records": total,
        "priced_records": len(prices),
        "median_price_per_m2": rounded(median(prices)),
        "median_inflation_adjusted_price_per_m2": rounded(median(adjusted_prices)),
        "min_price_per_m2": rounded(min(prices) if prices else None),
        "max_price_per_m2": rounded(max(prices) if prices else None),
        "inflation_adjusted_priced_records": len(adjusted_prices),
        "source": SOURCE,
        "caveat": "RCN contains past transaction evidence, not current availability.",
    }


def fetch_pages(connection: sqlite3.Connection, args: argparse.Namespace, bbox_4326: str | None, bbox_2180: str | None) -> dict:
    fetched_at = datetime.now(timezone.utc).isoformat()
    page_size = min(args.page_size, args.limit)
    start = 0
    fetched = inserted_or_updated = 0
    query = {
        "bbox_4326": bbox_4326,
        "bbox_2180": bbox_2180,
        "cql": args.cql,
        "limit": args.limit,
        "page_size": args.page_size,
        "inflation_reference_year": args.inflation_reference_year,
    }
    inflation_index = load_inflation_index(args.inflation_index_json)
    reference_year = inflation_reference_year(inflation_index, args.inflation_reference_year)
    while start < args.limit:
        count = min(page_size, args.limit - start)
        url = build_url(args, start, count, bbox_2180)
        xml_text = fetch_text(url, args.timeout)
        records, returned = parse_records(xml_text)
        fetched += len(records)
        inserted_or_updated += upsert_records(
            connection,
            records,
            args.run_id,
            bbox_4326 or bbox_2180,
            query,
            fetched_at,
            inflation_index,
            reference_year,
        )
        print(
            "PROGRESS "
            + json.dumps(
                {"stage": "layer3_rcn", "run_id": args.run_id, "start": start, "returned": len(records), "total": fetched},
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        if not records or returned == 0 or len(records) < count:
            break
        start += count
    summary = run_summary(connection, args.run_id)
    summary.update({"fetched_records": fetched, "inserted_or_updated": inserted_or_updated, "bbox_4326": bbox_4326})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch RCN WFS raw price observations into SQLite.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--from-linked-parcels", dest="from_linked_parcels", action="store_true", help="Build bbox from parcels linked to run_id.")
    parser.add_argument("--from-layer2", dest="from_linked_parcels", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--bbox-4326", help="Explicit WGS84 bbox: minLon,minLat,maxLon,maxLat.")
    parser.add_argument("--bbox-2180", help="Explicit EPSG:2180 bbox: minX,minY,maxX,maxY.")
    parser.add_argument("--bbox-margin-m", type=float, default=500.0)
    parser.add_argument("--cql")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--inflation-index-json", help="Optional CPI/index JSON, e.g. {'2024': 100, '2025': 104.0}.")
    parser.add_argument("--inflation-reference-year", help="Reference year key from --inflation-index-json. Defaults to max key.")
    parser.add_argument("--summary-output")
    args = parser.parse_args()

    connection = connect_workspace(args.db_path, args.canon_db_path)
    try:
        ensure_layer3_tables(connection)
        bbox_4326 = args.bbox_4326
        if args.from_linked_parcels:
            bbox_4326 = layer2_bbox_4326(connection, args.run_id, args.bbox_margin_m)
            if not bbox_4326:
                summary = {"run_id": args.run_id, "status": "no_layer2_parcels", "rcn_records": 0, "priced_records": 0}
                print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
                return 0
        bbox_2180 = args.bbox_2180 or (bbox_4326_to_2180(bbox_4326) if bbox_4326 else None)
        if not bbox_2180 and not args.cql:
            parser.error("Provide --from-linked-parcels, --bbox-4326, --bbox-2180, or --cql.")
        summary = fetch_pages(connection, args, bbox_4326, bbox_2180)
    finally:
        connection.close()

    output = Path(args.summary_output) if args.summary_output else Path("results") / f"analysis_{args.run_id}" / "layer3_rcn_summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
