import importlib.util
import sqlite3
from pathlib import Path


def load_rank_module():
    path = Path("skills/candidate-ranking/scripts/rank_candidates.py")
    spec = importlib.util.spec_from_file_location("rank_candidates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rank_candidates_reports_funnel_counts():
    rank = load_rank_module()
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE canon_parcels (
            parcel_id TEXT PRIMARY KEY,
            parcel_number TEXT,
            commune TEXT,
            county TEXT,
            voivodeship TEXT,
            area_m2 REAL,
            centroid_lat REAL,
            centroid_lon REAL,
            bbox_min_lat REAL,
            bbox_min_lon REAL,
            bbox_max_lat REAL,
            bbox_max_lon REAL
        );
        CREATE TABLE helper_layer2_run_parcels (
            run_id TEXT NOT NULL,
            parcel_id TEXT NOT NULL,
            candidate_index INTEGER NOT NULL,
            source_bbox TEXT NOT NULL,
            expected_commune TEXT,
            PRIMARY KEY (run_id, parcel_id)
        );
        CREATE TABLE deriv_parcel_geometry_features (
            parcel_id TEXT PRIMARY KEY,
            area_m2 REAL NOT NULL,
            compactness REAL NOT NULL,
            elongation_ratio REAL NOT NULL,
            principal_moment_min_m4 REAL,
            principal_moment_max_m4 REAL
        );
        CREATE TABLE deriv_parcel_visual_features (
            parcel_id TEXT PRIMARY KEY,
            green_pixel_ratio REAL NOT NULL,
            dark_pixel_ratio REAL NOT NULL,
            bright_pixel_ratio REAL NOT NULL,
            low_saturation_ratio REAL NOT NULL,
            brightness_mean REAL NOT NULL
        );
        """
    )
    parcels = [
        ("P1", 5000.0, 0.50, 2.0, 0.60),
        ("P2", 50000.0, 0.50, 2.0, 0.90),
        ("P3", 3000.0, 0.20, 12.0, 0.20),
    ]
    for index, (parcel_id, area, compactness, elongation, green) in enumerate(parcels, start=1):
        connection.execute(
            "INSERT INTO canon_parcels VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (parcel_id, parcel_id, "Gmina", "Powiat", "Dolnoslaskie", area, 50.0, 16.0, 50.0, 16.0, 50.01, 16.01),
        )
        connection.execute("INSERT INTO helper_layer2_run_parcels VALUES ('demo', ?, ?, 'bbox', NULL)", (parcel_id, index))
        connection.execute(
            "INSERT INTO deriv_parcel_geometry_features VALUES (?, ?, ?, ?, 1, 2)",
            (parcel_id, area, compactness, elongation),
        )
        connection.execute(
            "INSERT INTO deriv_parcel_visual_features VALUES (?, ?, 0.3, 0.1, 0.2, 100)",
            (parcel_id, green),
        )
    connection.commit()

    filters = {
        "min_area_m2": 1500,
        "max_area_m2": 12000,
        "min_compactness": 0.25,
        "max_elongation": 8,
        "require_visual": True,
        "min_green_pixel_ratio": 0.5,
        "max_bright_pixel_ratio": 0.2,
        "max_low_saturation_ratio": 0.4,
        "max_brightness_mean": 140,
    }
    weights = {
        "target_area_m2": 5000,
        "area_tolerance_m2": 5000,
        "target_compactness": 0.35,
        "target_elongation": 2.5,
        "elongation_tolerance": 7,
        "target_dark_ratio": 0.35,
        "dark_tolerance": 0.35,
        "target_bright_ratio": 0.10,
        "bright_tolerance": 0.20,
        "area_weight": 2,
        "compactness_weight": 1,
        "elongation_weight": 0.8,
        "green_weight": 1.4,
        "dark_weight": 0.6,
        "bright_weight": 0.4,
    }

    payload = rank.rank_candidates(connection, "demo", filters, weights, limit=10)

    assert payload["funnel_counts"] == [
        {"step": "layer2_parcels", "count": 3},
        {"step": "deduped_parcels", "count": 3},
        {"step": "with_geometry_features", "count": 3},
        {"step": "area_1500_12000", "count": 2},
        {"step": "geometry_shape_filter", "count": 1},
        {"step": "with_visual_features", "count": 1},
        {"step": "visual_land_filter", "count": 1},
        {"step": "top_10", "count": 1},
    ]
    assert payload["top"][0]["parcel_id"] == "P1"


def test_rank_candidates_can_reject_visually_built_up_parcels():
    rank = load_rank_module()
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE canon_parcels (
            parcel_id TEXT PRIMARY KEY,
            parcel_number TEXT,
            commune TEXT,
            county TEXT,
            voivodeship TEXT,
            area_m2 REAL,
            centroid_lat REAL,
            centroid_lon REAL,
            bbox_min_lat REAL,
            bbox_min_lon REAL,
            bbox_max_lat REAL,
            bbox_max_lon REAL
        );
        CREATE TABLE helper_layer2_run_parcels (
            run_id TEXT NOT NULL,
            parcel_id TEXT NOT NULL,
            candidate_index INTEGER NOT NULL,
            source_bbox TEXT NOT NULL,
            expected_commune TEXT,
            PRIMARY KEY (run_id, parcel_id)
        );
        CREATE TABLE deriv_parcel_geometry_features (
            parcel_id TEXT PRIMARY KEY,
            area_m2 REAL NOT NULL,
            compactness REAL NOT NULL,
            elongation_ratio REAL NOT NULL,
            principal_moment_min_m4 REAL,
            principal_moment_max_m4 REAL
        );
        CREATE TABLE deriv_parcel_visual_features (
            parcel_id TEXT PRIMARY KEY,
            green_pixel_ratio REAL NOT NULL,
            dark_pixel_ratio REAL NOT NULL,
            bright_pixel_ratio REAL NOT NULL,
            low_saturation_ratio REAL NOT NULL,
            brightness_mean REAL NOT NULL
        );
        """
    )
    rows = [
        ("MEADOW", 1800.0, 0.65, 1.6, 0.88, 0.28, 0.01, 0.18, 95.0),
        ("BUILT", 1800.0, 0.65, 1.6, 0.28, 0.25, 0.22, 0.58, 165.0),
    ]
    for index, (parcel_id, area, compactness, elongation, green, dark, bright, low_sat, brightness_mean) in enumerate(rows, start=1):
        connection.execute(
            "INSERT INTO canon_parcels VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (parcel_id, parcel_id, "Gmina", "Powiat", "Dolnoslaskie", area, 50.0, 16.0, 50.0, 16.0, 50.01, 16.01),
        )
        connection.execute("INSERT INTO helper_layer2_run_parcels VALUES ('demo', ?, ?, 'bbox', NULL)", (parcel_id, index))
        connection.execute(
            "INSERT INTO deriv_parcel_geometry_features VALUES (?, ?, ?, ?, 1, 2)",
            (parcel_id, area, compactness, elongation),
        )
        connection.execute(
            "INSERT INTO deriv_parcel_visual_features VALUES (?, ?, ?, ?, ?, ?)",
            (parcel_id, green, dark, bright, low_sat, brightness_mean),
        )
    connection.commit()

    filters = {
        "min_area_m2": 1500,
        "max_area_m2": 3000,
        "min_compactness": 0.25,
        "max_elongation": 8,
        "require_visual": True,
        "min_green_pixel_ratio": 0.5,
        "max_bright_pixel_ratio": 0.08,
        "max_low_saturation_ratio": 0.35,
        "max_brightness_mean": 130,
    }
    weights = {
        "target_area_m2": 1800,
        "area_tolerance_m2": 2000,
        "target_compactness": 0.35,
        "target_elongation": 2.5,
        "elongation_tolerance": 7,
        "target_dark_ratio": 0.35,
        "dark_tolerance": 0.35,
        "target_bright_ratio": 0.10,
        "bright_tolerance": 0.20,
        "area_weight": 2,
        "compactness_weight": 1,
        "elongation_weight": 0.8,
        "green_weight": 1.4,
        "dark_weight": 0.6,
        "bright_weight": 0.4,
    }

    payload = rank.rank_candidates(connection, "demo", filters, weights, limit=10)

    assert payload["funnel_counts"][-2] == {"step": "visual_land_filter", "count": 1}
    assert [row["parcel_id"] for row in payload["top"]] == ["MEADOW"]
