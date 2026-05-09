from __future__ import annotations

import sqlite3
from pathlib import Path

CANON_SQL = """
CREATE TABLE IF NOT EXISTS canon_osm_features (
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
    PRIMARY KEY (osm_type, osm_id)
);
CREATE INDEX IF NOT EXISTS idx_canon_osm_features_center ON canon_osm_features(center_lat, center_lon);
CREATE TABLE IF NOT EXISTS canon_parcels (
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
);
CREATE INDEX IF NOT EXISTS idx_canon_parcels_center ON canon_parcels(centroid_lat, centroid_lon);
CREATE INDEX IF NOT EXISTS idx_canon_parcels_commune ON canon_parcels(commune);
CREATE TABLE IF NOT EXISTS canon_parcel_polygon_points (
    parcel_id TEXT NOT NULL,
    polygon_index INTEGER NOT NULL DEFAULT 0,
    ring_index INTEGER NOT NULL DEFAULT 0,
    point_index INTEGER NOT NULL,
    lon REAL NOT NULL,
    lat REAL NOT NULL,
    PRIMARY KEY (parcel_id, polygon_index, ring_index, point_index),
    FOREIGN KEY (parcel_id) REFERENCES canon_parcels(parcel_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS canon_rcn_price_observations (
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
);
CREATE INDEX IF NOT EXISTS idx_canon_rcn_price_observations_run_price ON canon_rcn_price_observations(run_id, price_per_m2);
CREATE INDEX IF NOT EXISTS idx_canon_rcn_price_observations_parcel ON canon_rcn_price_observations(parcel_id);
"""

BUS_SQL = """
CREATE TABLE IF NOT EXISTS bus_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    profile TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS bus_skill_status (
    run_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    status TEXT NOT NULL,
    code TEXT,
    message TEXT,
    counts_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (run_id, skill)
);
CREATE TABLE IF NOT EXISTS bus_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    producer_skill TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    artifact_key TEXT NOT NULL DEFAULT 'default',
    payload_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(run_id, producer_skill, artifact_type, artifact_key)
);
CREATE INDEX IF NOT EXISTS idx_bus_artifacts_lookup ON bus_artifacts(run_id, artifact_type, artifact_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_bus_artifacts_producer ON bus_artifacts(run_id, producer_skill, updated_at DESC);
"""

LOCAL_SQL = """
CREATE TABLE IF NOT EXISTS skill_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS skill_cache (
    cache_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS skill_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    detail_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 60000")
    return con


def ensure_canon_db(path: str | Path) -> None:
    con = connect(path)
    con.executescript(CANON_SQL)
    con.commit()
    con.close()


def ensure_bus_db(path: str | Path) -> None:
    con = connect(path)
    con.executescript(BUS_SQL)
    con.commit()
    con.close()


def ensure_local_db(path: str | Path, schema_sql: str | None = None) -> None:
    con = connect(path)
    con.executescript(LOCAL_SQL)
    if schema_sql:
        con.executescript(schema_sql)
    con.commit()
    con.close()
