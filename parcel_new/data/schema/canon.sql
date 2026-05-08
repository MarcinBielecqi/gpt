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
CREATE INDEX IF NOT EXISTS idx_canon_osm_features_center
    ON canon_osm_features(center_lat, center_lon);

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
CREATE INDEX IF NOT EXISTS idx_canon_parcels_center
    ON canon_parcels(centroid_lat, centroid_lon);
CREATE INDEX IF NOT EXISTS idx_canon_parcels_commune
    ON canon_parcels(commune);

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
CREATE INDEX IF NOT EXISTS idx_canon_rcn_price_observations_run_price
    ON canon_rcn_price_observations(run_id, price_per_m2);
CREATE INDEX IF NOT EXISTS idx_canon_rcn_price_observations_parcel
    ON canon_rcn_price_observations(parcel_id);
